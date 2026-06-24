from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PyQt5 import QtWidgets

from caen_interface import (
    CAEN_TRANSPORT_DIRECT_SERIAL,
    CAEN_TRANSPORT_OPTIONS,
    CAEN_WRAPPER_CURRENT_SOURCE_AUTO,
    CAEN_WRAPPER_CURRENT_SOURCE_OPTIONS,
    CAEN_WRAPPER_MODEL_LABELS,
    CAEN_WRAPPER_MODEL_N1471,
    CAEN_WRAPPER_MODEL_OPTIONS,
    CHANNEL_LABELS,
    CAEN_TRANSPORT_RAW_WRAPPER,
    USB_VCP_BAUD_OPTIONS,
    ChannelSnapshot,
    UsbVcpSettings,
)
from main_window import MainWindow


class MainWindowTabbedShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.window = MainWindow(Path(self._tmp.name))

    def tearDown(self) -> None:
        self.window.close()
        self.window.worker_thread.quit()
        self.window.worker_thread.wait(2000)
        self._tmp.cleanup()

    def test_three_tabs_in_order(self) -> None:
        titles = [self.window.tabs.tabText(i) for i in range(self.window.tabs.count())]
        self.assertEqual(titles, ["Setup", "Channels", "Scan"])

    def test_channel_grid_lists_every_channel(self) -> None:
        self.assertEqual(set(self.window.channel_cells), set(CHANNEL_LABELS))
        self.assertEqual(self.window.channel_table.rowCount(), len(CHANNEL_LABELS))

    def test_channel_refresh_updates_grid_cells(self) -> None:
        self.window._on_channel_refresh(
            [
                ChannelSnapshot("C", 0, "-", 151.0, -0.2, True, 0, "OK"),
                ChannelSnapshot("T1", 1, "-", 1.0, -0.3, True, 0, "OK"),
                ChannelSnapshot("B1", 2, "+", 149.0, 0.4, True, 0, "OK"),
                ChannelSnapshot("T2", 3, "+", 349.0, 0.5, False, 0, "OK"),
            ]
        )
        c_cells = self.window.channel_cells["C"]
        self.assertEqual(c_cells["voltage"].text(), "-151.0")  # negative polarity -> signed
        self.assertEqual(c_cells["current"].text(), "-0.2000")
        self.assertEqual(c_cells["power"].text(), "ON")
        self.assertEqual(self.window.channel_cells["T2"]["power"].text(), "OFF")

    def test_channels_grid_has_manual_controls(self) -> None:
        for label in CHANNEL_LABELS:
            cells = self.window.channel_cells[label]
            self.assertIsInstance(cells["vset"], QtWidgets.QDoubleSpinBox)
            self.assertIsInstance(cells["power"], QtWidgets.QPushButton)
            self.assertTrue(cells["power"].isCheckable())
        self.assertTrue(hasattr(self.window, "all_on_button"))
        self.assertTrue(hasattr(self.window, "all_off_button"))

    def test_manual_controls_disabled_when_disconnected(self) -> None:
        self.assertFalse(self.window.channel_cells["C"]["vset"].isEnabled())
        self.assertFalse(self.window.channel_cells["C"]["power"].isEnabled())
        self.assertFalse(self.window.all_on_button.isEnabled())

    def test_hardware_settings_defaults_match_usb_vcp_defaults(self) -> None:
        self.window.backend_combo.setCurrentText("CAEN USB-VCP")

        settings = self.window._current_usb_vcp_settings()

        self.assertIsInstance(settings, UsbVcpSettings)
        self.assertEqual(settings.transport, CAEN_TRANSPORT_DIRECT_SERIAL)
        self.assertEqual(settings.wrapper_model, CAEN_WRAPPER_MODEL_N1471)
        self.assertEqual(settings.wrapper_current_source, CAEN_WRAPPER_CURRENT_SOURCE_AUTO)
        self.assertEqual(settings.build_argument(), "COM1_9600_8_1_none_0")

    def test_hardware_transport_options_match_expected_order(self) -> None:
        self.window.backend_combo.setCurrentText("CAEN USB-VCP")

        values = [self.window.transport_combo.itemText(i) for i in range(self.window.transport_combo.count())]

        self.assertEqual(values, list(CAEN_TRANSPORT_OPTIONS))

    def test_hardware_settings_use_logger_baud_whitelist(self) -> None:
        self.window.backend_combo.setCurrentText("CAEN USB-VCP")

        values = [self.window.baud_combo.itemText(i) for i in range(self.window.baud_combo.count())]

        self.assertEqual(values, list(USB_VCP_BAUD_OPTIONS))

    def test_wrapper_only_controls_are_disabled_for_direct_serial(self) -> None:
        self.window.backend_combo.setCurrentText("CAEN USB-VCP")

        self.assertFalse(self.window.board_number_spin.isEnabled())
        self.assertFalse(self.window.model_combo.isEnabled())
        self.assertFalse(self.window.current_source_combo.isEnabled())

    def test_wrapper_only_controls_enable_for_wrapper_transport(self) -> None:
        self.window.backend_combo.setCurrentText("CAEN USB-VCP")

        self.window.transport_combo.setCurrentText(CAEN_TRANSPORT_RAW_WRAPPER)

        self.assertTrue(self.window.board_number_spin.isEnabled())
        self.assertTrue(self.window.model_combo.isEnabled())
        self.assertTrue(self.window.current_source_combo.isEnabled())

    def test_wrapper_model_options_match_expected_order(self) -> None:
        self.window.backend_combo.setCurrentText("CAEN USB-VCP")

        values = [self.window.model_combo.itemText(i) for i in range(self.window.model_combo.count())]

        self.assertEqual(values, [CAEN_WRAPPER_MODEL_LABELS[model] for model in CAEN_WRAPPER_MODEL_OPTIONS])

    def test_wrapper_current_source_options_match_expected_order(self) -> None:
        self.window.backend_combo.setCurrentText("CAEN USB-VCP")

        values = [self.window.current_source_combo.itemText(i) for i in range(self.window.current_source_combo.count())]

        self.assertEqual(values, list(CAEN_WRAPPER_CURRENT_SOURCE_OPTIONS))

    def test_hardware_settings_collect_custom_serial_tuple(self) -> None:
        self.window.backend_combo.setCurrentText("CAEN USB-VCP")
        self.window.com_combo.clear()
        self.window.com_combo.addItem("9")
        self.window.com_combo.setCurrentText("9")
        self.window.transport_combo.setCurrentText(CAEN_TRANSPORT_RAW_WRAPPER)
        self.window.board_number_spin.setValue(2)
        self.window.model_combo.setCurrentIndex(self.window.model_combo.findData("N1470"))
        self.window.current_source_combo.setCurrentText("IMonH")
        self.window.baud_combo.setCurrentText("57600")
        self.window.data_bits_combo.setCurrentText("7")
        self.window.stop_bits_combo.setCurrentText("2")
        self.window.parity_combo.setCurrentText("Even")

        settings = self.window._current_usb_vcp_settings()

        self.assertEqual(settings.transport, CAEN_TRANSPORT_RAW_WRAPPER)
        self.assertEqual(settings.wrapper_model, "N1470")
        self.assertEqual(settings.wrapper_current_source, "IMonH")
        self.assertEqual(settings.build_argument(), "COM9_57600_7_2_even_2")

    def test_worker_manual_control_applies_to_simulation_backend(self) -> None:
        from main_window import ScanWorker

        worker = ScanWorker(Path(self._tmp.name))
        worker.connect_backend("Simulation", None)
        self.assertIsNotNone(worker.backend)

        worker.set_channel_voltage("C", 234.0)
        self.assertAlmostEqual(worker.backend._channel_state["C"]["voltage_v"], 234.0)

        worker.set_channel_power("C", True)
        self.assertTrue(worker.backend._channel_state["C"]["is_on"])

        worker.set_all_power(False)
        self.assertTrue(all(not st["is_on"] for st in worker.backend._channel_state.values()))

        worker.backend.disconnect()


if __name__ == "__main__":
    unittest.main()
