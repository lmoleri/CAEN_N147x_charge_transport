from __future__ import annotations

import threading
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets

from caen_interface import (
    CHANNEL_DEFINITIONS,
    CHANNEL_LABELS,
    BaseCaenInterface,
    CaenLibsInterface,
    ChannelSnapshot,
    FieldConfig,
    SimulationInterface,
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

    @QtCore.pyqtSlot(str, str)
    def connect_backend(self, backend_name: str, com_port: str) -> None:
        if self.scan_active:
            self.error.emit("Cannot connect while a scan is running.")
            return

        try:
            if self.backend is not None:
                self._disconnect_backend()

            if backend_name == "Simulation":
                backend = SimulationInterface()
            elif backend_name == "CAEN USB-VCP":
                backend = CaenLibsInterface(com_port=com_port)
            else:
                raise RuntimeError(f"Unsupported backend: {backend_name}")

            self.log_message.emit(f"Connecting to {backend_name}...")
            backend.connect()
            backend.set_ramp_rates(300.0, 300.0)
            self.backend = backend
            self.backend_name = backend_name
            snapshots = backend.read_all_channels()
            self.connected.emit(True, f"Connected to {backend_name}.", snapshots)
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
    connect_requested = QtCore.pyqtSignal(str, str)
    disconnect_requested = QtCore.pyqtSignal()
    refresh_requested = QtCore.pyqtSignal()
    start_scan_requested = QtCore.pyqtSignal(str)

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

        layout.addWidget(QtWidgets.QLabel("Backend"), 0, 0)
        layout.addWidget(self.backend_combo, 0, 1)
        layout.addWidget(self.com_label, 0, 2)
        layout.addWidget(self.com_combo, 0, 3)
        layout.addWidget(self.refresh_ports_button, 0, 4)
        layout.addWidget(self.connect_button, 0, 5)
        layout.addWidget(self.disconnect_button, 0, 6)
        layout.addWidget(self.backend_hint_label, 1, 0, 1, 7)
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
        hint = QtWidgets.QLabel("Live channel monitor. Voltages are driven by the scan recipe.")
        hint.setStyleSheet("color: grey; font-style: italic;")
        layout.addWidget(hint)

        columns = ["Channel", "Polarity", "VMon [V]", "IMon [nA]", "Power", "Status"]
        self.channel_table = QtWidgets.QTableWidget(len(CHANNEL_DEFINITIONS), len(columns))
        self.channel_table.setHorizontalHeaderLabels(columns)
        self.channel_table.verticalHeader().setVisible(False)
        self.channel_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.channel_table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.channel_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)

        cell_keys = ("channel", "polarity", "voltage", "current", "power", "status")
        self.channel_cells: dict[str, dict[str, QtWidgets.QTableWidgetItem]] = {}
        for row, channel in enumerate(CHANNEL_DEFINITIONS):
            polarity = "negative" if channel.polarity == "-" else "positive"
            initial = (channel.label, polarity, "0.0", "0.000", "OFF", "N/A")
            cells: dict[str, QtWidgets.QTableWidgetItem] = {}
            for col, (key, text) in enumerate(zip(cell_keys, initial)):
                item = QtWidgets.QTableWidgetItem(text)
                item.setTextAlignment(QtCore.Qt.AlignCenter)
                self.channel_table.setItem(row, col, item)
                cells[key] = item
            self.channel_cells[channel.label] = cells

        layout.addWidget(self.channel_table)
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
        com_port = self.com_combo.currentText().strip()
        self.connect_requested.emit(backend_name, com_port)

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
            cells["power"].setText("ON" if snapshot.is_on else "OFF")
            cells["status"].setText(snapshot.status_text)
            self._apply_status_color(cells["status"], snapshot)

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

    def _set_connection_state(self, connected: bool) -> None:
        self.connected_backend = connected
        self.connect_button.setEnabled(not connected and not self.scan_running)
        self.disconnect_button.setEnabled(connected and not self.scan_running)
        self.start_button.setEnabled(connected and not self.scan_running)
        self.backend_combo.setEnabled(not connected and not self.scan_running)
        self.com_combo.setEnabled(not connected and not self.scan_running)
        self.refresh_ports_button.setEnabled(not connected and not self.scan_running)
        self.mode_combo.setEnabled(not self.scan_running)
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
        self.mode_combo.setEnabled(not running)

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
