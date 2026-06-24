# The tabbed shell and GECO-style channel grid are adapted from the design of
# weizmann-atlas/caen_logger.
from __future__ import annotations

import threading
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets

from caen_interface import (
    CAEN_TRANSPORT_OPTIONS,
    CHANNEL_DEFINITIONS,
    CHANNEL_LABELS,
    BaseCaenInterface,
    CaenUsbVcpInterface,
    ChannelSnapshot,
    FieldConfig,
    SimulationInterface,
    USB_VCP_BAUD,
    USB_VCP_BOARD_NUMBER,
    USB_VCP_DATA_BITS,
    USB_VCP_PARITY,
    USB_VCP_STOP_BITS,
    UsbVcpSettings,
    list_serial_ports,
)
from data_logger import DataLogger
from plotly_view import PlotlyScanView
from scan_controller import ScanCallbacks, ScanController


class ScanWorker(QtCore.QObject):
    connected = QtCore.pyqtSignal(bool, str, object)
    disconnected = QtCore.pyqtSignal(str)
    channel_refresh = QtCore.pyqtSignal(object)
    scan_prepared = QtCore.pyqtSignal(object, str)
    subscan_started = QtCore.pyqtSignal(str)
    point_recorded = QtCore.pyqtSignal(object)
    scan_finished = QtCore.pyqtSignal(bool, bool, str, object)
    error = QtCore.pyqtSignal(str)
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
            self.connected.emit(True, f"Connected to {backend.connection_name()}.", snapshots)
        except Exception as exc:  # pragma: no cover - hardware-dependent
            self.backend = None
            self.backend_name = ""
            self.connected.emit(False, str(exc), [])

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

    @QtCore.pyqtSlot(str)
    def start_scan(self, mode: str) -> None:
        if self.backend is None:
            self.error.emit("Connect to a backend before starting a scan.")
            return
        if self.scan_active:
            self.error.emit("A scan is already running.")
            return

        logger = DataLogger(self.base_dir / "measurements")
        csv_path = logger.open_run(mode)
        field_configs = list(self.controller.field_configs_for_mode(mode))
        self.scan_prepared.emit(field_configs, str(csv_path))

        callbacks = ScanCallbacks(
            on_subscan_started=lambda field_config: self.subscan_started.emit(field_config.label),
            on_point_recorded=self.point_recorded.emit,
            on_channel_refresh=self.channel_refresh.emit,
            on_status_message=self.log_message.emit,
        )

        self.abort_event.clear()
        self.scan_active = True
        try:
            result = self.controller.run_recipe(self.backend, mode, logger, callbacks, self.abort_event)
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

    def _apply_manual(self, action, message: str) -> None:
        if self.backend is None:
            self.error.emit("Connect to a backend before manual control.")
            return
        if self.scan_active:
            self.error.emit("Manual control is disabled during a scan.")
            return
        try:
            action()
            self.log_message.emit(message)
            self.channel_refresh.emit(self.backend.read_all_channels())
        except Exception as exc:  # pragma: no cover - hardware-dependent
            self.error.emit(f"Manual control failed: {exc}")

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
    start_scan_requested = QtCore.pyqtSignal(str)
    set_voltage_requested = QtCore.pyqtSignal(str, float)
    set_power_requested = QtCore.pyqtSignal(str, bool)
    set_all_power_requested = QtCore.pyqtSignal(bool)

    def __init__(self, base_dir: Path) -> None:
        super().__init__()
        self.base_dir = base_dir
        self.controller = ScanController()
        self.connected_backend = False
        self.scan_running = False

        self.setWindowTitle("THGEM Exercise 3.B School GUI")
        self.resize(1360, 880)

        self.worker_thread = QtCore.QThread(self)
        self.worker = ScanWorker(base_dir)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.start()

        self.connect_requested.connect(self.worker.connect_backend)
        self.disconnect_requested.connect(self.worker.disconnect_backend)
        self.refresh_requested.connect(self.worker.refresh_channels)
        self.start_scan_requested.connect(self.worker.start_scan)
        self.set_voltage_requested.connect(self.worker.set_channel_voltage)
        self.set_power_requested.connect(self.worker.set_channel_power)
        self.set_all_power_requested.connect(self.worker.set_all_power)

        self.worker.connected.connect(self._on_connected)
        self.worker.disconnected.connect(self._on_disconnected)
        self.worker.channel_refresh.connect(self._on_channel_refresh)
        self.worker.scan_prepared.connect(self._on_scan_prepared)
        self.worker.subscan_started.connect(self._on_subscan_started)
        self.worker.point_recorded.connect(self._on_point_recorded)
        self.worker.scan_finished.connect(self._on_scan_finished)
        self.worker.error.connect(self._on_error)
        self.worker.log_message.connect(self._append_log)

        self.poll_timer = QtCore.QTimer(self)
        self.poll_timer.setInterval(1000)
        self.poll_timer.timeout.connect(self._queue_refresh)

        self._build_ui()
        self._populate_serial_ports()
        self._update_recipe_summary()
        self._set_connection_state(False)
        self._set_scan_state(False)

    def _build_ui(self) -> None:
        self.tabs = QtWidgets.QTabWidget()
        self.setCentralWidget(self.tabs)
        self.tabs.addTab(self._build_setup_tab(), "Setup")
        self.tabs.addTab(self._build_channels_tab(), "Channels")
        self.tabs.addTab(self._build_scan_tab(), "Scan")
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
            "Simulation works everywhere. CAEN USB-VCP uses the CAEN HV Wrapper library "
            "(install it from caen.it on the Windows lab PC)."
        )
        self.backend_hint_label.setWordWrap(True)

        self.hardware_settings_group = QtWidgets.QGroupBox("Advanced hardware settings")
        hardware_layout = QtWidgets.QGridLayout(self.hardware_settings_group)

        self.transport_combo = QtWidgets.QComboBox()
        self.transport_combo.addItems(list(CAEN_TRANSPORT_OPTIONS))

        self.board_number_spin = QtWidgets.QSpinBox()
        self.board_number_spin.setRange(0, 31)
        self.board_number_spin.setValue(USB_VCP_BOARD_NUMBER)

        self.baud_spin = QtWidgets.QSpinBox()
        self.baud_spin.setRange(300, 921600)
        self.baud_spin.setValue(USB_VCP_BAUD)
        self.baud_spin.setSingleStep(300)

        self.data_bits_spin = QtWidgets.QSpinBox()
        self.data_bits_spin.setRange(5, 8)
        self.data_bits_spin.setValue(USB_VCP_DATA_BITS)

        self.stop_bits_spin = QtWidgets.QSpinBox()
        self.stop_bits_spin.setRange(0, 2)
        self.stop_bits_spin.setValue(USB_VCP_STOP_BITS)

        self.parity_spin = QtWidgets.QSpinBox()
        self.parity_spin.setRange(0, 4)
        self.parity_spin.setValue(USB_VCP_PARITY)

        self.hardware_hint_label = QtWidgets.QLabel(
            "USB-VCP tuple: COM_baud_data_stop_parity_board. Keep the defaults unless "
            "you are matching a known-working legacy setup."
        )
        self.hardware_hint_label.setWordWrap(True)

        hardware_layout.addWidget(QtWidgets.QLabel("Transport"), 0, 0)
        hardware_layout.addWidget(self.transport_combo, 0, 1)
        hardware_layout.addWidget(QtWidgets.QLabel("Board"), 0, 2)
        hardware_layout.addWidget(self.board_number_spin, 0, 3)
        hardware_layout.addWidget(QtWidgets.QLabel("Baud"), 1, 0)
        hardware_layout.addWidget(self.baud_spin, 1, 1)
        hardware_layout.addWidget(QtWidgets.QLabel("Data bits"), 1, 2)
        hardware_layout.addWidget(self.data_bits_spin, 1, 3)
        hardware_layout.addWidget(QtWidgets.QLabel("Stop bits"), 2, 0)
        hardware_layout.addWidget(self.stop_bits_spin, 2, 1)
        hardware_layout.addWidget(QtWidgets.QLabel("Parity"), 2, 2)
        hardware_layout.addWidget(self.parity_spin, 2, 3)
        hardware_layout.addWidget(self.hardware_hint_label, 3, 0, 1, 4)
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

        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(self.controller.mode_names())
        self.mode_combo.currentTextChanged.connect(self._update_recipe_summary)

        self.start_button = QtWidgets.QPushButton("Start Scan")
        self.start_button.clicked.connect(self._queue_start_scan)
        self.abort_button = QtWidgets.QPushButton("Abort")
        self.abort_button.clicked.connect(self._request_abort)

        self.recipe_summary_label = QtWidgets.QLabel()
        self.recipe_summary_label.setWordWrap(True)
        self.output_path_label = QtWidgets.QLabel(f"CSV output: {self.base_dir / 'measurements'}")
        self.output_path_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.uv_note_label = QtWidgets.QLabel(
            "Reference mode is intended to be taken with the UV lamp OFF. All other modes assume UV ON."
        )
        self.uv_note_label.setWordWrap(True)

        layout.addWidget(QtWidgets.QLabel("Recipe"), 0, 0)
        layout.addWidget(self.mode_combo, 0, 1)
        layout.addWidget(self.start_button, 0, 2)
        layout.addWidget(self.abort_button, 0, 3)
        layout.addWidget(self.recipe_summary_label, 1, 0, 1, 4)
        layout.addWidget(self.output_path_label, 2, 0, 1, 4)
        layout.addWidget(self.uv_note_label, 3, 0, 1, 4)
        layout.setColumnStretch(1, 1)
        return group

    def _build_channels_tab(self) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        hint = QtWidgets.QLabel(
            "Setpoints apply on Enter / focus-out. Manual control is disabled during a scan."
        )
        hint.setStyleSheet("color: grey; font-style: italic;")
        layout.addWidget(hint)

        columns = ["Channel", "Polarity", "VMon [V]", "IMon [nA]", "VSet [V]", "Power", "Status"]
        col = {name: i for i, name in enumerate(columns)}
        self.channel_table = QtWidgets.QTableWidget(len(CHANNEL_DEFINITIONS), len(columns))
        self.channel_table.setHorizontalHeaderLabels(columns)
        self.channel_table.verticalHeader().setVisible(False)
        self.channel_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.channel_table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.channel_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)

        self.channel_cells: dict[str, dict[str, object]] = {}
        self._last_vset: dict[str, float] = {}
        for row, channel in enumerate(CHANNEL_DEFINITIONS):
            label = channel.label
            polarity = "negative" if channel.polarity == "-" else "positive"
            cells: dict[str, object] = {}
            for key, column, text in (
                ("channel", col["Channel"], label),
                ("polarity", col["Polarity"], polarity),
                ("voltage", col["VMon [V]"], "0.0"),
                ("current", col["IMon [nA]"], "0.000"),
                ("status", col["Status"], "N/A"),
            ):
                item = QtWidgets.QTableWidgetItem(text)
                item.setTextAlignment(QtCore.Qt.AlignCenter)
                self.channel_table.setItem(row, column, item)
                cells[key] = item

            spin = QtWidgets.QDoubleSpinBox()
            spin.setRange(0.0, 1500.0)
            spin.setDecimals(1)
            spin.setSuffix(" V")
            spin.setKeyboardTracking(False)
            spin.editingFinished.connect(lambda lbl=label: self._on_vset_edited(lbl))
            self.channel_table.setCellWidget(row, col["VSet [V]"], spin)
            cells["vset"] = spin
            self._last_vset[label] = float(spin.value())

            power_btn = QtWidgets.QPushButton("OFF")
            power_btn.setCheckable(True)
            power_btn.clicked.connect(lambda checked, lbl=label: self._on_power_clicked(lbl, checked))
            self.channel_table.setCellWidget(row, col["Power"], power_btn)
            cells["power"] = power_btn

            self.channel_cells[label] = cells

        layout.addWidget(self.channel_table)

        button_row = QtWidgets.QHBoxLayout()
        button_row.addStretch(1)
        self.all_on_button = QtWidgets.QPushButton("All ON")
        self.all_off_button = QtWidgets.QPushButton("All OFF")
        self.all_on_button.clicked.connect(lambda: self.set_all_power_requested.emit(True))
        self.all_off_button.clicked.connect(lambda: self.set_all_power_requested.emit(False))
        button_row.addWidget(self.all_on_button)
        button_row.addWidget(self.all_off_button)
        layout.addLayout(button_row)
        return tab

    def _build_scan_tab(self) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.addWidget(self._build_scan_group())

        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        splitter.addWidget(self._build_plot_group())
        splitter.addWidget(self._build_log_group())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([600, 160])
        layout.addWidget(splitter, stretch=1)
        return tab

    def _build_plot_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("Current vs THGEM1 Voltage")
        layout = QtWidgets.QVBoxLayout(group)
        self.plot_tabs = PlotlyScanView()
        layout.addWidget(self.plot_tabs)
        return group

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

    def _update_recipe_summary(self) -> None:
        mode = self.mode_combo.currentText()
        self.recipe_summary_label.setText(self.controller.describe_mode(mode))

    def _queue_connect(self) -> None:
        backend_name = self.backend_combo.currentText()
        settings = None
        if backend_name == "CAEN USB-VCP":
            settings = self._current_usb_vcp_settings()
        self.connect_requested.emit(backend_name, settings)

    def _queue_disconnect(self) -> None:
        self.disconnect_requested.emit()

    def _queue_refresh(self) -> None:
        if self.connected_backend and not self.scan_running:
            self.refresh_requested.emit()

    def _queue_start_scan(self) -> None:
        self.start_scan_requested.emit(self.mode_combo.currentText())

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

    def _on_power_clicked(self, label: str, checked: bool) -> None:
        self.set_power_requested.emit(label, checked)

    def _on_connected(self, success: bool, message: str, snapshots: object) -> None:
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
        self.poll_timer.start()

    def _on_disconnected(self, message: str) -> None:
        self.connected_backend = False
        self.scan_running = False
        self.poll_timer.stop()
        self._set_connection_state(False)
        self._set_scan_state(False)
        self._append_log(message)
        self.statusBar().showMessage(message, 4000)

    def _on_channel_refresh(self, snapshots: object) -> None:
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
            cells["current"].setText(f"{snapshot.imon_na:+.4f}")
            cells["status"].setText(snapshot.status_text)
            self._apply_status_color(cells["status"], snapshot)
            self._sync_power_button(cells["power"], snapshot.is_on)

    def _sync_power_button(self, button: QtWidgets.QPushButton, on: bool) -> None:
        button.blockSignals(True)
        button.setChecked(on)
        button.setText("ON" if on else "OFF")
        button.setStyleSheet("background:#3a8a3a; color:white;" if on else "")
        button.blockSignals(False)

    def _apply_status_color(self, item: QtWidgets.QTableWidgetItem, snapshot: ChannelSnapshot) -> None:
        if snapshot.status_code != 0:
            item.setBackground(QtGui.QColor("#f2dede"))   # alarm
        elif snapshot.is_on:
            item.setBackground(QtGui.QColor("#dff0d8"))   # on & ok
        else:
            item.setBackground(QtGui.QColor("#eeeeee"))   # off

    def _on_scan_prepared(self, field_configs: object, csv_path: str) -> None:
        if isinstance(field_configs, (list, tuple)):
            self.plot_tabs.reset_tabs(list(field_configs))
        self.output_path_label.setText(f"CSV output: {csv_path}")
        self.scan_running = True
        self.poll_timer.stop()
        self._set_scan_state(True)
        self._append_log(f"Scan started. Writing CSV to {csv_path}")
        self.statusBar().showMessage("Scan running...")

    def _on_subscan_started(self, subscan_label: str) -> None:
        self.plot_tabs.activate_subscan(subscan_label)
        self._append_log(f"Sub-scan: {subscan_label}")

    def _on_point_recorded(self, record: object) -> None:
        self.plot_tabs.append_record(record)
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
        if self.connected_backend:
            self.poll_timer.start()
        self._on_channel_refresh(final_snapshots)
        self._append_log(message)
        self.statusBar().showMessage(message, 6000)
        if not success and not aborted:
            QtWidgets.QMessageBox.warning(self, "Scan failed", message)

    def _on_error(self, message: str) -> None:
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
            baud=int(self.baud_spin.value()),
            data_bits=int(self.data_bits_spin.value()),
            stop_bits=int(self.stop_bits_spin.value()),
            parity=int(self.parity_spin.value()),
            board_number=int(self.board_number_spin.value()),
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
        self.baud_spin.setEnabled(not connected and not self.scan_running)
        self.data_bits_spin.setEnabled(not connected and not self.scan_running)
        self.stop_bits_spin.setEnabled(not connected and not self.scan_running)
        self.parity_spin.setEnabled(not connected and not self.scan_running)
        self.mode_combo.setEnabled(not self.scan_running)
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
        self.baud_spin.setEnabled(not self.connected_backend and not running)
        self.data_bits_spin.setEnabled(not self.connected_backend and not running)
        self.stop_bits_spin.setEnabled(not self.connected_backend and not running)
        self.parity_spin.setEnabled(not self.connected_backend and not running)
        self.mode_combo.setEnabled(not running)
        self._set_manual_controls_enabled(self.connected_backend and not running)

    def _set_manual_controls_enabled(self, enabled: bool) -> None:
        for cells in self.channel_cells.values():
            cells["vset"].setEnabled(enabled)
            cells["power"].setEnabled(enabled)
        self.all_on_button.setEnabled(enabled)
        self.all_off_button.setEnabled(enabled)

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
