# The tabbed shell and GECO-style channel grid are adapted from the design of
# weizmann-atlas/caen_logger.
from __future__ import annotations

import threading
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets

from caen_interface import (
    CAEN_TRANSPORT_DIRECT_SERIAL,
    CAEN_TRANSPORT_OPTIONS,
    CAEN_WRAPPER_CURRENT_SOURCE_AUTO,
    CAEN_WRAPPER_CURRENT_SOURCE_OPTIONS,
    CAEN_WRAPPER_MODEL_LABELS,
    CAEN_WRAPPER_MODEL_N1471,
    CAEN_WRAPPER_MODEL_OPTIONS,
    CHANNEL_DEFINITIONS,
    CHANNEL_LABELS,
    BaseCaenInterface,
    CaenUsbVcpInterface,
    ChannelControlState,
    ChannelSnapshot,
    FieldConfig,
    SimulationInterface,
    USB_VCP_BAUD_OPTIONS,
    USB_VCP_BAUD,
    USB_VCP_BOARD_NUMBER,
    USB_VCP_DATA_BITS_OPTIONS,
    USB_VCP_DATA_BITS,
    USB_VCP_PARITY_OPTIONS,
    USB_VCP_PARITY,
    USB_VCP_STOP_BITS_OPTIONS,
    USB_VCP_STOP_BITS,
    UsbVcpSettings,
    list_serial_ports,
    status_color_hex,
)
from data_logger import DataLogger
from plotly_view import PlotlyViewer
from scan_controller import (
    DEFAULT_WAIT_SECONDS,
    DRIFT_GAP_CM,
    INDUCTION_GAP_CM,
    VTHGEM1_START_V,
    VTHGEM1_STEP_V,
    VTHGEM1_STOP_V,
    ScanCallbacks,
    ScanController,
    ScanParameters,
)


class ScanWorker(QtCore.QObject):
    connected = QtCore.pyqtSignal(bool, str, object, object)
    disconnected = QtCore.pyqtSignal(str)
    channel_refresh = QtCore.pyqtSignal(object)
    control_refresh = QtCore.pyqtSignal(object)
    scan_prepared = QtCore.pyqtSignal(object, str)
    point_recorded = QtCore.pyqtSignal(object)
    scan_finished = QtCore.pyqtSignal(bool, bool, str, object)
    error = QtCore.pyqtSignal(str)
    channel_status = QtCore.pyqtSignal(str)
    log_message = QtCore.pyqtSignal(str)

    def __init__(self, base_dir: Path) -> None:
        super().__init__()
        self.base_dir = base_dir
        self.controller = ScanController()
        self.backend: BaseCaenInterface | None = None
        self.backend_name = ""
        self.abort_event = threading.Event()
        self.scan_active = False

    @QtCore.pyqtSlot(str, object)
    def connect_backend(self, backend_name: str, connection_settings: object) -> None:
        if self.scan_active:
            self.error.emit("Cannot connect while a scan is running.")
            return

        try:
            if self.backend is not None:
                self._disconnect_backend()

            if backend_name == "Simulation":
                backend = SimulationInterface()
            elif backend_name == "CAEN USB-VCP":
                if not isinstance(connection_settings, UsbVcpSettings):
                    raise RuntimeError("Missing CAEN USB-VCP connection settings.")
                backend = CaenUsbVcpInterface(connection_settings)
            else:
                raise RuntimeError(f"Unsupported backend: {backend_name}")

            self.log_message.emit(f"Connecting to {backend_name}...")
            backend.connect()
            backend.set_ramp_rates(300.0, 300.0)
            self.backend = backend
            self.backend_name = backend.connection_name()
            snapshots = backend.read_all_channels()
            controls = backend.read_channel_controls()
            self.connected.emit(True, f"Connected to {backend.connection_name()}.", snapshots, controls)
        except Exception as exc:  # pragma: no cover - hardware-dependent
            self.backend = None
            self.backend_name = ""
            self.connected.emit(False, str(exc), [], [])

    @QtCore.pyqtSlot()
    def disconnect_backend(self) -> None:
        if self.scan_active:
            self.error.emit("Abort the scan before disconnecting.")
            return

        try:
            self._disconnect_backend()
            self.disconnected.emit("Disconnected.")
        except Exception as exc:  # pragma: no cover - hardware-dependent
            self.error.emit(str(exc))

    @QtCore.pyqtSlot()
    def refresh_channels(self) -> None:
        if self.scan_active or self.backend is None:
            return

        try:
            self.channel_refresh.emit(self.backend.read_all_channels())
        except Exception as exc:  # pragma: no cover - hardware-dependent
            self.error.emit(str(exc))

    @QtCore.pyqtSlot()
    def refresh_channel_controls(self) -> None:
        if self.scan_active or self.backend is None:
            return

        try:
            self.control_refresh.emit(self.backend.read_channel_controls())
            self.channel_status.emit("Setpoints refreshed.")
        except Exception as exc:  # pragma: no cover - hardware-dependent
            self.error.emit(str(exc))

    @QtCore.pyqtSlot(object)
    def start_scan(self, params: object) -> None:
        if self.backend is None:
            self.error.emit("Connect to a backend before starting a scan.")
            return
        if self.scan_active:
            self.error.emit("A scan is already running.")
            return

        logger = DataLogger(self.base_dir / "measurements")
        csv_path = logger.open_run(params.label)
        self.scan_prepared.emit(params, str(csv_path))

        callbacks = ScanCallbacks(
            on_point_recorded=self.point_recorded.emit,
            on_channel_refresh=self.channel_refresh.emit,
            on_status_message=self.log_message.emit,
        )

        self.abort_event.clear()
        self.scan_active = True
        try:
            result = self.controller.run_scan(self.backend, params, logger, callbacks, self.abort_event)
            final_snapshots = self.backend.read_all_channels()
            self.scan_finished.emit(result.success, result.aborted, result.message, final_snapshots)
        except Exception as exc:  # pragma: no cover - hardware-dependent
            final_snapshots = []
            try:
                if self.backend is not None:
                    final_snapshots = self.backend.read_all_channels()
            except Exception:
                final_snapshots = []
            self.scan_finished.emit(False, False, str(exc), final_snapshots)
        finally:
            logger.close()
            self.scan_active = False

    def request_abort(self) -> None:
        self.abort_event.set()

    @QtCore.pyqtSlot(str, float)
    def set_channel_voltage(self, label: str, voltage_v: float) -> None:
        self._apply_manual(
            lambda: self.backend.set_channel_voltages({label: float(voltage_v)}),
            f"Set {label} VSet = {voltage_v:.1f} V",
            refresh_controls=True,
        )

    @QtCore.pyqtSlot(str, float)
    def set_channel_ramp_up(self, label: str, ramp_up_v_s: float) -> None:
        self._apply_manual(
            lambda: self.backend.set_channel_ramp_up_rates({label: float(ramp_up_v_s)}),
            f"Set {label} RUp = {ramp_up_v_s:.1f} V/s",
            refresh_controls=True,
        )

    @QtCore.pyqtSlot(str, float)
    def set_channel_ramp_down(self, label: str, ramp_down_v_s: float) -> None:
        self._apply_manual(
            lambda: self.backend.set_channel_ramp_down_rates({label: float(ramp_down_v_s)}),
            f"Set {label} RDW = {ramp_down_v_s:.1f} V/s",
            refresh_controls=True,
        )

    @QtCore.pyqtSlot(str, bool)
    def set_channel_power(self, label: str, on: bool) -> None:
        self._apply_manual(
            lambda: (self.backend.power_on_channels if on else self.backend.power_off_channels)([label]),
            f"{label} powered {'ON' if on else 'OFF'}",
        )

    @QtCore.pyqtSlot(bool)
    def set_all_power(self, on: bool) -> None:
        self._apply_manual(
            lambda: (self.backend.power_on_channels if on else self.backend.power_off_channels)(list(CHANNEL_LABELS)),
            f"All channels powered {'ON' if on else 'OFF'}",
        )

    def _apply_manual(self, action, message: str, *, refresh_controls: bool = False) -> None:
        if self.backend is None:
            self.error.emit("Connect to a backend before manual control.")
            return
        if self.scan_active:
            self.error.emit("Manual control is disabled during a scan.")
            return
        try:
            action()
            self.log_message.emit(message)
            self.channel_status.emit(message)
            # Monitors (VMon/IMon) refresh on the next poll tick — don't pay a slow
            # read_all_channels() per command, which compounds on rapid clicks.
            if refresh_controls:
                self.control_refresh.emit(self.backend.read_channel_controls())
        except Exception as exc:  # pragma: no cover - hardware-dependent
            detail = f"FAILED: {message} — {exc}"
            self.channel_status.emit(detail)
            self.error.emit(f"Manual control failed: {message} — {exc}")

    def _disconnect_backend(self) -> None:
        if self.backend is None:
            return
        try:
            self.backend.safe_shutdown(CHANNEL_LABELS)
            self.backend.power_off_channels(CHANNEL_LABELS)
        finally:
            self.backend.disconnect()
            self.backend = None
            self.backend_name = ""


class MainWindow(QtWidgets.QMainWindow):
    connect_requested = QtCore.pyqtSignal(str, object)
    disconnect_requested = QtCore.pyqtSignal()
    refresh_requested = QtCore.pyqtSignal()
    refresh_controls_requested = QtCore.pyqtSignal()
    start_scan_requested = QtCore.pyqtSignal(object)
    set_voltage_requested = QtCore.pyqtSignal(str, float)
    set_ramp_up_requested = QtCore.pyqtSignal(str, float)
    set_ramp_down_requested = QtCore.pyqtSignal(str, float)
    set_power_requested = QtCore.pyqtSignal(str, bool)
    set_all_power_requested = QtCore.pyqtSignal(bool)

    def __init__(self, base_dir: Path) -> None:
        super().__init__()
        self.base_dir = base_dir
        self.controller = ScanController()
        self.connected_backend = False
        self.scan_running = False
        self._refresh_in_flight = False  # cap outstanding poll refreshes at one

        self.setWindowTitle("THGEM Exercise 3.B School GUI")
        self.resize(1360, 880)

        self.worker_thread = QtCore.QThread(self)
        self.worker = ScanWorker(base_dir)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.start()

        self.connect_requested.connect(self.worker.connect_backend)
        self.disconnect_requested.connect(self.worker.disconnect_backend)
        self.refresh_requested.connect(self.worker.refresh_channels)
        self.refresh_controls_requested.connect(self.worker.refresh_channel_controls)
        self.start_scan_requested.connect(self.worker.start_scan)
        self.set_voltage_requested.connect(self.worker.set_channel_voltage)
        self.set_ramp_up_requested.connect(self.worker.set_channel_ramp_up)
        self.set_ramp_down_requested.connect(self.worker.set_channel_ramp_down)
        self.set_power_requested.connect(self.worker.set_channel_power)
        self.set_all_power_requested.connect(self.worker.set_all_power)

        self.worker.connected.connect(self._on_connected)
        self.worker.disconnected.connect(self._on_disconnected)
        self.worker.channel_refresh.connect(self._on_channel_refresh)
        self.worker.control_refresh.connect(self._on_control_refresh)
        self.worker.scan_prepared.connect(self._on_scan_prepared)
        self.worker.point_recorded.connect(self._on_point_recorded)
        self.worker.scan_finished.connect(self._on_scan_finished)
        self.worker.error.connect(self._on_error)
        self.worker.channel_status.connect(self._set_channels_status)
        self.worker.log_message.connect(self._append_log)

        self.poll_timer = QtCore.QTimer(self)
        self.poll_timer.setInterval(1000)
        self.poll_timer.timeout.connect(self._queue_refresh)

        self._build_ui()
        self._populate_serial_ports()
        self._update_scan_summary()
        self._set_connection_state(False)
        self._set_scan_state(False)

    def _build_ui(self) -> None:
        self.tabs = QtWidgets.QTabWidget()
        self.setCentralWidget(self.tabs)
        self.tabs.addTab(self._build_setup_tab(), "Setup")
        self.tabs.addTab(self._build_channels_tab(), "Channels")
        self.tabs.addTab(self._build_scan_tab(), "Scan")
        self.tabs.addTab(self._build_viewer_tab(), "Viewer")
        self.statusBar().showMessage("Ready.")

    def _build_setup_tab(self) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setSpacing(12)
        layout.addWidget(self._build_connection_group())
        layout.addStretch(1)
        return tab

    def _build_connection_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("Connection")
        layout = QtWidgets.QGridLayout(group)

        self.backend_combo = QtWidgets.QComboBox()
        self.backend_combo.addItems(["Simulation", "CAEN USB-VCP"])
        self.backend_combo.currentTextChanged.connect(self._on_backend_selection_changed)

        self.com_label = QtWidgets.QLabel("COM port")
        self.com_combo = QtWidgets.QComboBox()
        self.refresh_ports_button = QtWidgets.QPushButton("Refresh Ports")
        self.refresh_ports_button.clicked.connect(self._populate_serial_ports)

        self.connect_button = QtWidgets.QPushButton("Connect")
        self.connect_button.clicked.connect(self._queue_connect)
        self.disconnect_button = QtWidgets.QPushButton("Disconnect")
        self.disconnect_button.clicked.connect(self._queue_disconnect)

        self.backend_hint_label = QtWidgets.QLabel(
            "Simulation works everywhere. CAEN USB-VCP defaults to the legacy direct serial path for "
            "the N1471H lab supply; wrapper transports remain available for comparison on the "
            "Windows lab PC."
        )
        self.backend_hint_label.setWordWrap(True)

        self.hardware_settings_group = QtWidgets.QGroupBox("Advanced hardware settings")
        hardware_layout = QtWidgets.QGridLayout(self.hardware_settings_group)

        self.transport_combo = QtWidgets.QComboBox()
        self.transport_combo.addItems(list(CAEN_TRANSPORT_OPTIONS))
        self.transport_combo.currentTextChanged.connect(self._on_transport_selection_changed)

        self.board_number_label = QtWidgets.QLabel("Board / LBus")
        self.board_number_spin = QtWidgets.QSpinBox()
        self.board_number_spin.setRange(0, 31)
        self.board_number_spin.setValue(USB_VCP_BOARD_NUMBER)

        self.model_label = QtWidgets.QLabel("Model")
        self.model_combo = QtWidgets.QComboBox()
        for model_name in CAEN_WRAPPER_MODEL_OPTIONS:
            self.model_combo.addItem(CAEN_WRAPPER_MODEL_LABELS[model_name], model_name)
        self.model_combo.setCurrentIndex(self.model_combo.findData(CAEN_WRAPPER_MODEL_N1471))

        self.current_source_label = QtWidgets.QLabel("Current source")
        self.current_source_combo = QtWidgets.QComboBox()
        for source_name in CAEN_WRAPPER_CURRENT_SOURCE_OPTIONS:
            self.current_source_combo.addItem(source_name, source_name)
        self.current_source_combo.setCurrentText(CAEN_WRAPPER_CURRENT_SOURCE_AUTO)

        self.baud_combo = QtWidgets.QComboBox()
        self.baud_combo.addItems(list(USB_VCP_BAUD_OPTIONS))
        self.baud_combo.setCurrentText(str(USB_VCP_BAUD))

        self.data_bits_combo = QtWidgets.QComboBox()
        self.data_bits_combo.addItems(list(USB_VCP_DATA_BITS_OPTIONS))
        self.data_bits_combo.setCurrentText(str(USB_VCP_DATA_BITS))

        self.stop_bits_combo = QtWidgets.QComboBox()
        self.stop_bits_combo.addItems(list(USB_VCP_STOP_BITS_OPTIONS))
        self.stop_bits_combo.setCurrentText(str(USB_VCP_STOP_BITS))

        self.parity_combo = QtWidgets.QComboBox()
        self.parity_combo.addItems(list(USB_VCP_PARITY_OPTIONS))
        self.parity_combo.setCurrentText(str(USB_VCP_PARITY))

        self.hardware_hint_label = QtWidgets.QLabel(
            "Direct serial uses COM/baud/data/stop/parity. Wrapper tuple is "
            "COM_baud_data_stop_parity_board; logger-aligned defaults are COMx_9600_8_1_none_0. "
            "Model/current-source are wrapper-only troubleshooting controls."
        )
        self.hardware_hint_label.setWordWrap(True)

        hardware_layout.addWidget(QtWidgets.QLabel("Transport"), 0, 0)
        hardware_layout.addWidget(self.transport_combo, 0, 1)
        hardware_layout.addWidget(self.board_number_label, 0, 2)
        hardware_layout.addWidget(self.board_number_spin, 0, 3)
        hardware_layout.addWidget(self.model_label, 1, 0)
        hardware_layout.addWidget(self.model_combo, 1, 1)
        hardware_layout.addWidget(self.current_source_label, 1, 2)
        hardware_layout.addWidget(self.current_source_combo, 1, 3)
        hardware_layout.addWidget(QtWidgets.QLabel("Baud"), 2, 0)
        hardware_layout.addWidget(self.baud_combo, 2, 1)
        hardware_layout.addWidget(QtWidgets.QLabel("Data bits"), 2, 2)
        hardware_layout.addWidget(self.data_bits_combo, 2, 3)
        hardware_layout.addWidget(QtWidgets.QLabel("Stop bits"), 3, 0)
        hardware_layout.addWidget(self.stop_bits_combo, 3, 1)
        hardware_layout.addWidget(QtWidgets.QLabel("Parity"), 3, 2)
        hardware_layout.addWidget(self.parity_combo, 3, 3)
        hardware_layout.addWidget(self.hardware_hint_label, 4, 0, 1, 4)
        hardware_layout.setColumnStretch(1, 1)
        hardware_layout.setColumnStretch(3, 1)

        layout.addWidget(QtWidgets.QLabel("Backend"), 0, 0)
        layout.addWidget(self.backend_combo, 0, 1)
        layout.addWidget(self.com_label, 0, 2)
        layout.addWidget(self.com_combo, 0, 3)
        layout.addWidget(self.refresh_ports_button, 0, 4)
        layout.addWidget(self.connect_button, 0, 5)
        layout.addWidget(self.disconnect_button, 0, 6)
        layout.addWidget(self.backend_hint_label, 1, 0, 1, 7)
        layout.addWidget(self.hardware_settings_group, 2, 0, 1, 7)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)
        return group

    def _build_scan_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("Scan Control")
        layout = QtWidgets.QGridLayout(group)
        self._loading_preset = False

        self.preset_combo = QtWidgets.QComboBox()
        self.preset_combo.addItem("Custom")
        self.preset_combo.addItems(self.controller.preset_names())

        def _spin(minimum, maximum, decimals, step, value, suffix):
            box = QtWidgets.QDoubleSpinBox()
            box.setRange(minimum, maximum)
            box.setDecimals(decimals)
            box.setSingleStep(step)
            box.setSuffix(suffix)
            box.setKeyboardTracking(False)
            box.setValue(value)
            return box

        self.vstart_spin = _spin(0.0, 5000.0, 0, 25.0, VTHGEM1_START_V, " V")
        self.vstop_spin = _spin(0.0, 5000.0, 0, 25.0, VTHGEM1_STOP_V, " V")
        self.vstep_spin = _spin(1.0, 1000.0, 0, 5.0, VTHGEM1_STEP_V, " V")
        self.wait_spin = _spin(0.0, 600.0, 1, 0.5, DEFAULT_WAIT_SECONDS, " s")
        self.drift_field_spin = _spin(-50.0, 50.0, 2, 0.1, 0.0, " kV/cm")
        self.drift_gap_spin = _spin(0.01, 100.0, 2, 0.1, DRIFT_GAP_CM, " cm")
        self.induction_field_spin = _spin(-50.0, 50.0, 2, 0.1, 1.0, " kV/cm")
        self.induction_gap_spin = _spin(0.01, 100.0, 2, 0.05, INDUCTION_GAP_CM, " cm")
        self._scan_spins = (
            self.vstart_spin, self.vstop_spin, self.vstep_spin, self.wait_spin,
            self.drift_field_spin, self.drift_gap_spin, self.induction_field_spin, self.induction_gap_spin,
        )

        self.uv_check = QtWidgets.QCheckBox("UV lamp ON (recorded as metadata)")
        self.uv_check.setChecked(True)
        self.start_button = QtWidgets.QPushButton("Start Scan")
        self.abort_button = QtWidgets.QPushButton("Abort")

        self.scan_summary_label = QtWidgets.QLabel()
        self.scan_summary_label.setWordWrap(True)
        self.output_path_label = QtWidgets.QLabel(f"CSV output: {self.base_dir / 'measurements'}")
        self.output_path_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)

        # Wire signals only after every widget exists, so the setValue() calls
        # above (during construction) cannot fire the change handlers.
        self.preset_combo.currentTextChanged.connect(self._on_preset_selected)
        for box in self._scan_spins:
            box.valueChanged.connect(self._on_scan_param_changed)
        self.uv_check.toggled.connect(self._on_scan_param_changed)
        self.start_button.clicked.connect(self._queue_start_scan)
        self.abort_button.clicked.connect(self._request_abort)

        r = 0
        layout.addWidget(QtWidgets.QLabel("Preset"), r, 0)
        layout.addWidget(self.preset_combo, r, 1)
        layout.addWidget(self.start_button, r, 2)
        layout.addWidget(self.abort_button, r, 3)
        r += 1
        layout.addWidget(QtWidgets.QLabel("V_THGEM1 start"), r, 0)
        layout.addWidget(self.vstart_spin, r, 1)
        layout.addWidget(QtWidgets.QLabel("stop"), r, 2)
        layout.addWidget(self.vstop_spin, r, 3)
        r += 1
        layout.addWidget(QtWidgets.QLabel("V_THGEM1 step"), r, 0)
        layout.addWidget(self.vstep_spin, r, 1)
        layout.addWidget(QtWidgets.QLabel("Wait / point"), r, 2)
        layout.addWidget(self.wait_spin, r, 3)
        r += 1
        layout.addWidget(QtWidgets.QLabel("Drift field"), r, 0)
        layout.addWidget(self.drift_field_spin, r, 1)
        layout.addWidget(QtWidgets.QLabel("Drift gap (C↔T1)"), r, 2)
        layout.addWidget(self.drift_gap_spin, r, 3)
        r += 1
        layout.addWidget(QtWidgets.QLabel("Induction field"), r, 0)
        layout.addWidget(self.induction_field_spin, r, 1)
        layout.addWidget(QtWidgets.QLabel("Induction gap (B1↔T2)"), r, 2)
        layout.addWidget(self.induction_gap_spin, r, 3)
        r += 1
        layout.addWidget(self.uv_check, r, 0, 1, 4)
        r += 1
        layout.addWidget(self.scan_summary_label, r, 0, 1, 4)
        r += 1
        layout.addWidget(self.output_path_label, r, 0, 1, 4)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)
        return group

    def _build_channels_tab(self) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        hint = QtWidgets.QLabel(
            "Setpoints apply as you type (Enter or click away). Manual control is disabled during a scan."
        )
        hint.setStyleSheet("color: grey; font-style: italic;")
        layout.addWidget(hint)

        columns = ["Channel", "Polarity", "Pw", "VSet [V]", "VMon [V]", "IMon [μA]", "RUp [V/s]", "RDW [V/s]", "Status"]
        col = {name: i for i, name in enumerate(columns)}
        self.channel_table = QtWidgets.QTableWidget(len(CHANNEL_DEFINITIONS), len(columns))
        self.channel_table.setHorizontalHeaderLabels(columns)
        self.channel_table.verticalHeader().setVisible(False)
        self.channel_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.channel_table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.channel_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)

        self.channel_cells: dict[str, dict[str, object]] = {}
        self._last_vset: dict[str, float] = {}
        self._last_ramp_up: dict[str, float] = {}
        self._last_ramp_down: dict[str, float] = {}
        self._pending_power: dict[str, bool] = {}  # label -> intended power, until a read-back confirms
        for row, channel in enumerate(CHANNEL_DEFINITIONS):
            label = channel.label
            polarity = "negative" if channel.polarity == "-" else "positive"
            cells: dict[str, object] = {}
            for key, column, text in (
                ("channel", col["Channel"], label),
                ("polarity", col["Polarity"], polarity),
                ("voltage", col["VMon [V]"], "0.0"),
                ("current", col["IMon [μA]"], "0.0000"),
                ("status", col["Status"], "N/A"),
            ):
                item = QtWidgets.QTableWidgetItem(text)
                item.setTextAlignment(QtCore.Qt.AlignCenter)
                self.channel_table.setItem(row, column, item)
                cells[key] = item

            power_btn = QtWidgets.QPushButton("OFF")
            power_btn.setCheckable(True)
            power_btn.clicked.connect(lambda checked, lbl=label: self._on_power_clicked(lbl, checked))
            self.channel_table.setCellWidget(row, col["Pw"], power_btn)
            cells["power"] = power_btn

            spin = QtWidgets.QDoubleSpinBox()
            spin.setRange(0.0, 1500.0)
            spin.setDecimals(1)
            spin.setSuffix(" V")
            spin.setKeyboardTracking(False)
            spin.editingFinished.connect(lambda lbl=label: self._on_vset_edited(lbl))
            self.channel_table.setCellWidget(row, col["VSet [V]"], spin)
            cells["vset"] = spin
            self._last_vset[label] = float(spin.value())

            rup_spin = QtWidgets.QDoubleSpinBox()
            rup_spin.setRange(0.0, 500.0)
            rup_spin.setDecimals(1)
            rup_spin.setSuffix(" V/s")
            rup_spin.setKeyboardTracking(False)
            rup_spin.editingFinished.connect(lambda lbl=label: self._on_ramp_up_edited(lbl))
            self.channel_table.setCellWidget(row, col["RUp [V/s]"], rup_spin)
            cells["ramp_up"] = rup_spin
            self._last_ramp_up[label] = float(rup_spin.value())

            rdw_spin = QtWidgets.QDoubleSpinBox()
            rdw_spin.setRange(0.0, 500.0)
            rdw_spin.setDecimals(1)
            rdw_spin.setSuffix(" V/s")
            rdw_spin.setKeyboardTracking(False)
            rdw_spin.editingFinished.connect(lambda lbl=label: self._on_ramp_down_edited(lbl))
            self.channel_table.setCellWidget(row, col["RDW [V/s]"], rdw_spin)
            cells["ramp_down"] = rdw_spin
            self._last_ramp_down[label] = float(rdw_spin.value())

            self.channel_cells[label] = cells

        layout.addWidget(self.channel_table)

        button_row = QtWidgets.QHBoxLayout()
        self.channels_hint_label = QtWidgets.QLabel("Writes are applied immediately when editing finishes.")
        self.channels_hint_label.setStyleSheet("color: grey; font-style: italic;")
        button_row.addWidget(self.channels_hint_label)
        button_row.addStretch(1)
        self.all_on_button = QtWidgets.QPushButton("All ON")
        self.all_off_button = QtWidgets.QPushButton("All OFF")
        self.refresh_setpoints_button = QtWidgets.QPushButton("Refresh Setpoints")
        self.all_on_button.clicked.connect(lambda: self._on_all_power_clicked(True))
        self.all_off_button.clicked.connect(lambda: self._on_all_power_clicked(False))
        self.refresh_setpoints_button.clicked.connect(self._queue_refresh_controls)
        button_row.addWidget(self.all_on_button)
        button_row.addWidget(self.all_off_button)
        button_row.addWidget(self.refresh_setpoints_button)
        self.channels_status_label = QtWidgets.QLabel("")
        self.channels_status_label.setStyleSheet("color: #555;")
        button_row.addWidget(self.channels_status_label)
        layout.addLayout(button_row)
        return tab

    def _build_scan_tab(self) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.addWidget(self._build_scan_group())
        layout.addWidget(self._build_log_group(), stretch=1)
        return tab

    def _build_viewer_tab(self) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)

        self.viewer = PlotlyViewer()

        toolbar = QtWidgets.QHBoxLayout()
        load_button = QtWidgets.QPushButton("Load CSV…")
        load_button.clicked.connect(self._load_viewer_csv)
        clear_button = QtWidgets.QPushButton("Clear")
        clear_button.clicked.connect(self.viewer.clear)
        toolbar.addWidget(load_button)
        toolbar.addWidget(clear_button)
        toolbar.addSpacing(16)
        toolbar.addWidget(QtWidgets.QLabel("Channels:"))
        self.viewer_channel_checks: dict[str, QtWidgets.QCheckBox] = {}
        for label in CHANNEL_LABELS:
            check = QtWidgets.QCheckBox(label)
            check.setChecked(True)
            check.toggled.connect(lambda on, lbl=label: self.viewer.set_channel_visible(lbl, on))
            self.viewer_channel_checks[label] = check
            toolbar.addWidget(check)
        toolbar.addSpacing(16)
        self.follow_check = QtWidgets.QCheckBox("Follow active scan")
        self.follow_check.toggled.connect(self.viewer.set_follow)
        toolbar.addWidget(self.follow_check)
        toolbar.addStretch(1)

        layout.addLayout(toolbar)
        layout.addWidget(self.viewer, stretch=1)
        return tab

    def _load_viewer_csv(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Load scan CSV", str(self.base_dir / "measurements"),
            "CSV files (*.csv);;All files (*)",
        )
        if paths:
            self.viewer.load_files(paths)

    def _build_log_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("Run Log")
        layout = QtWidgets.QVBoxLayout(group)
        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)
        layout.addWidget(self.log_view)
        return group

    def _on_backend_selection_changed(self) -> None:
        is_hardware = self.backend_combo.currentText() == "CAEN USB-VCP"
        self.com_label.setVisible(is_hardware)
        self.com_combo.setVisible(is_hardware)
        self.refresh_ports_button.setVisible(is_hardware)
        self.hardware_settings_group.setVisible(is_hardware)
        self._on_transport_selection_changed()

    def _on_transport_selection_changed(self) -> None:
        direct_serial = self.transport_combo.currentText() == CAEN_TRANSPORT_DIRECT_SERIAL
        hardware_selected = self.backend_combo.currentText() == "CAEN USB-VCP"
        wrapper_controls_enabled = (
            hardware_selected and not direct_serial and not self.connected_backend and not self.scan_running
        )

        self.board_number_label.setEnabled(not direct_serial)
        self.board_number_spin.setEnabled(wrapper_controls_enabled)
        self.model_label.setEnabled(not direct_serial)
        self.model_combo.setEnabled(wrapper_controls_enabled)
        self.current_source_label.setEnabled(not direct_serial)
        self.current_source_combo.setEnabled(wrapper_controls_enabled)
        if direct_serial:
            self.hardware_hint_label.setText(
                "Direct serial uses COM/baud/data/stop/parity only. Board/LBus is ignored for this "
                "transport. Model and current source only affect wrapper troubleshooting. Wrapper tuple: "
                "COM_baud_data_stop_parity_board."
            )
        else:
            self.hardware_hint_label.setText(
                "Wrapper tuple: COM_baud_data_stop_parity_board. Use wrapper auto, caen-libs, or raw "
                "wrapper only when comparing against the DLL path. Model/current source apply only to "
                "wrapper transports."
            )

    def _populate_serial_ports(self) -> None:
        current_text = self.com_combo.currentText()
        ports = list_serial_ports()
        self.com_combo.clear()
        if ports:
            self.com_combo.addItems(ports)
            index = self.com_combo.findText(current_text)
            if index >= 0:
                self.com_combo.setCurrentIndex(index)
        else:
            self.com_combo.addItem("COM1")

    def _current_scan_parameters(self) -> ScanParameters:
        return ScanParameters(
            label=self.preset_combo.currentText(),
            vthgem1_start_v=self.vstart_spin.value(),
            vthgem1_stop_v=self.vstop_spin.value(),
            vthgem1_step_v=self.vstep_spin.value(),
            drift_gap_cm=self.drift_gap_spin.value(),
            drift_field_kv_cm=self.drift_field_spin.value(),
            induction_gap_cm=self.induction_gap_spin.value(),
            induction_field_kv_cm=self.induction_field_spin.value(),
            wait_seconds=self.wait_spin.value(),
            uv_expected=self.uv_check.isChecked(),
        )

    def _update_scan_summary(self) -> None:
        self.scan_summary_label.setText(self._current_scan_parameters().describe())

    def _on_preset_selected(self, name: str) -> None:
        if name and name != "Custom":
            params = self.controller.preset(name)
            self._loading_preset = True
            try:
                self.vstart_spin.setValue(params.vthgem1_start_v)
                self.vstop_spin.setValue(params.vthgem1_stop_v)
                self.vstep_spin.setValue(params.vthgem1_step_v)
                self.wait_spin.setValue(params.wait_seconds)
                self.drift_field_spin.setValue(params.drift_field_kv_cm)
                self.drift_gap_spin.setValue(params.drift_gap_cm)
                self.induction_field_spin.setValue(params.induction_field_kv_cm)
                self.induction_gap_spin.setValue(params.induction_gap_cm)
                self.uv_check.setChecked(params.uv_expected)
            finally:
                self._loading_preset = False
        self._update_scan_summary()

    def _on_scan_param_changed(self, *_args) -> None:
        if self._loading_preset:
            return
        if self.preset_combo.currentText() != "Custom":
            self.preset_combo.blockSignals(True)
            self.preset_combo.setCurrentText("Custom")
            self.preset_combo.blockSignals(False)
        self._update_scan_summary()

    def _set_scan_inputs_enabled(self, enabled: bool) -> None:
        self.preset_combo.setEnabled(enabled)
        self.uv_check.setEnabled(enabled)
        for box in self._scan_spins:
            box.setEnabled(enabled)

    def _queue_connect(self) -> None:
        backend_name = self.backend_combo.currentText()
        settings = None
        if backend_name == "CAEN USB-VCP":
            settings = self._current_usb_vcp_settings()
        self.connect_requested.emit(backend_name, settings)

    def _queue_disconnect(self) -> None:
        self.disconnect_requested.emit()

    def _queue_refresh(self) -> None:
        # Skip if the previous refresh is still being processed: on slow hardware
        # a full read can take longer than the poll interval, and queuing more
        # would back up the worker — delaying manual commands by minutes.
        if self.connected_backend and not self.scan_running and not self._refresh_in_flight:
            self._refresh_in_flight = True
            self.refresh_requested.emit()

    def _queue_refresh_controls(self) -> None:
        if self.connected_backend and not self.scan_running:
            self.refresh_controls_requested.emit()

    def _queue_start_scan(self) -> None:
        self.start_scan_requested.emit(self._current_scan_parameters())

    def _request_abort(self) -> None:
        if not self.scan_running:
            return
        self.worker.request_abort()
        self.statusBar().showMessage("Abort requested...")
        self._append_log("Abort requested.")

    def _on_vset_edited(self, label: str) -> None:
        value = float(self.channel_cells[label]["vset"].value())
        if abs(self._last_vset.get(label, 0.0) - value) < 1e-9:
            return
        self._last_vset[label] = value
        self.set_voltage_requested.emit(label, value)

    def _on_ramp_up_edited(self, label: str) -> None:
        value = float(self.channel_cells[label]["ramp_up"].value())
        if abs(self._last_ramp_up.get(label, 0.0) - value) < 1e-9:
            return
        self._last_ramp_up[label] = value
        self.set_ramp_up_requested.emit(label, value)

    def _on_ramp_down_edited(self, label: str) -> None:
        value = float(self.channel_cells[label]["ramp_down"].value())
        if abs(self._last_ramp_down.get(label, 0.0) - value) < 1e-9:
            return
        self._last_ramp_down[label] = value
        self.set_ramp_down_requested.emit(label, value)

    def _on_power_clicked(self, label: str, checked: bool) -> None:
        # Optimistic feedback: reflect the intent in the button immediately; the
        # read-back confirms it (reconciled in _on_channel_refresh) so the toggle
        # feels instant even when the hardware round-trip is slow.
        self._pending_power[label] = checked
        self._sync_power_button(self.channel_cells[label]["power"], checked)
        self.set_power_requested.emit(label, checked)

    def _on_all_power_clicked(self, on: bool) -> None:
        for label, cells in self.channel_cells.items():
            self._pending_power[label] = on
            self._sync_power_button(cells["power"], on)
        self.set_all_power_requested.emit(on)

    def _on_connected(self, success: bool, message: str, snapshots: object, controls: object) -> None:
        if not success:
            self._set_connection_state(False)
            self.statusBar().showMessage(message, 6000)
            self._append_log(message)
            QtWidgets.QMessageBox.warning(self, "Connection failed", message)
            return

        self.connected_backend = True
        self._set_connection_state(True)
        self._append_log(message)
        self.statusBar().showMessage(message, 4000)
        self._on_channel_refresh(snapshots)
        self._on_control_refresh(controls)
        self._set_channels_status("Connected.")
        self._refresh_in_flight = False
        self.poll_timer.start()

    def _on_disconnected(self, message: str) -> None:
        self.connected_backend = False
        self.scan_running = False
        self._refresh_in_flight = False
        self.poll_timer.stop()
        self._set_connection_state(False)
        self._set_scan_state(False)
        self._append_log(message)
        self._set_channels_status(message)
        self.statusBar().showMessage(message, 4000)

    def _on_channel_refresh(self, snapshots: object) -> None:
        self._refresh_in_flight = False  # a poll/command read came back; allow the next poll
        if not isinstance(snapshots, (list, tuple)):
            return
        for snapshot in snapshots:
            if not isinstance(snapshot, ChannelSnapshot):
                continue
            cells = self.channel_cells.get(snapshot.label)
            if cells is None:
                continue
            signed_voltage = snapshot.vmon_v if snapshot.polarity == "+" else -snapshot.vmon_v
            cells["voltage"].setText(f"{signed_voltage:+.1f}")
            cells["current"].setText(f"{snapshot.imon_ua:+.4f}")
            cells["status"].setText(snapshot.status_text)
            self._apply_status_color(cells["status"], snapshot)
            intended = self._pending_power.get(snapshot.label)
            if intended is None:
                self._sync_power_button(cells["power"], snapshot.is_on)
            elif snapshot.is_on == intended:
                # hardware confirmed the optimistic state — stop overriding it
                del self._pending_power[snapshot.label]
                self._sync_power_button(cells["power"], snapshot.is_on)
            # else: command not applied yet — keep the optimistic button state

    def _on_control_refresh(self, controls: object) -> None:
        if not isinstance(controls, (list, tuple)):
            return
        for control in controls:
            if not isinstance(control, ChannelControlState):
                continue
            cells = self.channel_cells.get(control.label)
            if cells is None:
                continue
            for key, last_map, value in (
                ("vset", self._last_vset, float(control.vset_v)),
                ("ramp_up", self._last_ramp_up, float(control.ramp_up_v_s)),
                ("ramp_down", self._last_ramp_down, float(control.ramp_down_v_s)),
            ):
                spin = cells[key]
                spin.blockSignals(True)
                spin.setValue(value)
                spin.blockSignals(False)
                last_map[control.label] = value

    def _sync_power_button(self, button: QtWidgets.QPushButton, on: bool) -> None:
        button.blockSignals(True)
        button.setChecked(on)
        button.setText("ON" if on else "OFF")
        button.setStyleSheet("background:#3a8a3a; color:white;" if on else "")
        button.blockSignals(False)

    def _apply_status_color(self, item: QtWidgets.QTableWidgetItem, snapshot: ChannelSnapshot) -> None:
        item.setBackground(QtGui.QColor(status_color_hex(snapshot.status_code, snapshot.is_on)))

    def _set_channels_status(self, message: str) -> None:
        self.channels_status_label.setText(message)

    def _on_scan_prepared(self, params: object, csv_path: str) -> None:
        self.viewer.set_active_csv(csv_path)
        self.output_path_label.setText(f"CSV output: {csv_path}")
        self.scan_running = True
        self.poll_timer.stop()
        self._set_scan_state(True)
        self._append_log(f"Scan started ({params.describe()}). Writing CSV to {csv_path}")
        self.statusBar().showMessage("Scan running...")

    def _on_point_recorded(self, record: object) -> None:
        self._on_channel_refresh(list(record.channel_snapshots().values()))

    def _on_scan_finished(
        self,
        success: bool,
        aborted: bool,
        message: str,
        final_snapshots: object,
    ) -> None:
        self.scan_running = False
        self._set_scan_state(False)
        self.viewer.notify_scan_finished()
        if self.connected_backend:
            self.poll_timer.start()
        self._on_channel_refresh(final_snapshots)
        self._append_log(message)
        self.statusBar().showMessage(message, 6000)
        if not success and not aborted:
            QtWidgets.QMessageBox.warning(self, "Scan failed", message)

    def _on_error(self, message: str) -> None:
        # A manual command may have failed; drop optimistic power states so the
        # next read-back shows the true hardware state.
        self._pending_power.clear()
        self._refresh_in_flight = False  # release the poll guard if a refresh errored
        self._append_log(message)
        self.statusBar().showMessage(message, 6000)
        QtWidgets.QMessageBox.warning(self, "Error", message)

    def _append_log(self, message: str) -> None:
        timestamp = QtCore.QDateTime.currentDateTime().toString("HH:mm:ss")
        self.log_view.appendPlainText(f"[{timestamp}] {message}")

    def _current_usb_vcp_settings(self) -> UsbVcpSettings:
        return UsbVcpSettings(
            com_port=self.com_combo.currentText().strip(),
            transport=self.transport_combo.currentText(),
            baud=int(self.baud_combo.currentText()),
            data_bits=int(self.data_bits_combo.currentText()),
            stop_bits=self.stop_bits_combo.currentText(),
            parity=self.parity_combo.currentText(),
            board_number=int(self.board_number_spin.value()),
            wrapper_model=str(self.model_combo.currentData()),
            wrapper_current_source=str(self.current_source_combo.currentData()),
        )

    def _set_connection_state(self, connected: bool) -> None:
        self.connected_backend = connected
        self.connect_button.setEnabled(not connected and not self.scan_running)
        self.disconnect_button.setEnabled(connected and not self.scan_running)
        self.start_button.setEnabled(connected and not self.scan_running)
        self.backend_combo.setEnabled(not connected and not self.scan_running)
        self.com_combo.setEnabled(not connected and not self.scan_running)
        self.refresh_ports_button.setEnabled(not connected and not self.scan_running)
        self.transport_combo.setEnabled(not connected and not self.scan_running)
        self.board_number_spin.setEnabled(not connected and not self.scan_running)
        self.model_combo.setEnabled(not connected and not self.scan_running)
        self.current_source_combo.setEnabled(not connected and not self.scan_running)
        self.baud_combo.setEnabled(not connected and not self.scan_running)
        self.data_bits_combo.setEnabled(not connected and not self.scan_running)
        self.stop_bits_combo.setEnabled(not connected and not self.scan_running)
        self.parity_combo.setEnabled(not connected and not self.scan_running)
        self._set_scan_inputs_enabled(not self.scan_running)
        self._set_manual_controls_enabled(connected and not self.scan_running)
        self._on_backend_selection_changed()

    def _set_scan_state(self, running: bool) -> None:
        self.scan_running = running
        self.start_button.setEnabled(self.connected_backend and not running)
        self.abort_button.setEnabled(running)
        self.connect_button.setEnabled(not self.connected_backend and not running)
        self.disconnect_button.setEnabled(self.connected_backend and not running)
        self.backend_combo.setEnabled(not self.connected_backend and not running)
        self.com_combo.setEnabled(not self.connected_backend and not running)
        self.refresh_ports_button.setEnabled(not self.connected_backend and not running)
        self.transport_combo.setEnabled(not self.connected_backend and not running)
        self.board_number_spin.setEnabled(not self.connected_backend and not running)
        self.model_combo.setEnabled(not self.connected_backend and not running)
        self.current_source_combo.setEnabled(not self.connected_backend and not running)
        self.baud_combo.setEnabled(not self.connected_backend and not running)
        self.data_bits_combo.setEnabled(not self.connected_backend and not running)
        self.stop_bits_combo.setEnabled(not self.connected_backend and not running)
        self.parity_combo.setEnabled(not self.connected_backend and not running)
        self._set_scan_inputs_enabled(not running)
        self._set_manual_controls_enabled(self.connected_backend and not running)
        self._on_transport_selection_changed()

    def _set_manual_controls_enabled(self, enabled: bool) -> None:
        for cells in self.channel_cells.values():
            cells["vset"].setEnabled(enabled)
            cells["power"].setEnabled(enabled)
            cells["ramp_up"].setEnabled(enabled)
            cells["ramp_down"].setEnabled(enabled)
        self.all_on_button.setEnabled(enabled)
        self.all_off_button.setEnabled(enabled)
        self.refresh_setpoints_button.setEnabled(enabled)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self.scan_running:
            self.worker.request_abort()
            self.statusBar().showMessage("Abort requested. Close again after the scan stops.")
            event.ignore()
            return

        if self.connected_backend:
            QtCore.QMetaObject.invokeMethod(
                self.worker,
                "disconnect_backend",
                QtCore.Qt.BlockingQueuedConnection,
            )

        self.worker_thread.quit()
        self.worker_thread.wait(2000)
        super().closeEvent(event)
