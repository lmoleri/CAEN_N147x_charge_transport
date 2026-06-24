from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

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
    ChannelControlState,
    ChannelSnapshot,
    UsbVcpSettings,
    status_color_hex,
)
from main_window import MainWindow


class MainWindowTabbedShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self._serial_ports_patcher = mock.patch("main_window.list_serial_ports", return_value=[])
        self._serial_ports_patcher.start()
        self.window = MainWindow(Path(self._tmp.name))

    def tearDown(self) -> None:
        self.window.close()
        self.window.worker_thread.quit()
        self.window.worker_thread.wait(2000)
        self._serial_ports_patcher.stop()
        self._tmp.cleanup()

    def test_three_tabs_in_order(self) -> None:
        titles = [self.window.tabs.tabText(i) for i in range(self.window.tabs.count())]
        self.assertEqual(titles, ["Setup", "Channels", "Scan"])

    def test_channel_grid_lists_every_channel(self) -> None:
        self.assertEqual(set(self.window.channel_cells), set(CHANNEL_LABELS))
        self.assertEqual(self.window.channel_table.rowCount(), len(CHANNEL_LABELS))

    def test_channel_grid_columns_match_geco_style_layout(self) -> None:
        headers = [
            self.window.channel_table.horizontalHeaderItem(i).text()
            for i in range(self.window.channel_table.columnCount())
        ]
        self.assertEqual(
            headers,
            ["Channel", "Polarity", "Pw", "VSet [V]", "VMon [V]", "IMon [μA]", "RUp [V/s]", "RDW [V/s]", "Status"],
        )

    def test_channel_refresh_updates_grid_cells(self) -> None:
        self.window._on_channel_refresh(
            [
                ChannelSnapshot("C", 0, "-", 151.0, -0.2, True, 1, "ON"),
                ChannelSnapshot("T1", 1, "-", 1.0, -0.3, True, 3, "ON Ramp↑"),
                ChannelSnapshot("B1", 2, "+", 149.0, 0.4, True, 1 << 7, "TRIP"),
                ChannelSnapshot("T2", 3, "+", 349.0, 0.5, False, 0, "OFF"),
            ]
        )
        c_cells = self.window.channel_cells["C"]
        self.assertEqual(c_cells["voltage"].text(), "-151.0")  # negative polarity -> signed
        self.assertEqual(c_cells["current"].text(), "-0.2000")
        self.assertEqual(c_cells["power"].text(), "ON")
        self.assertEqual(c_cells["status"].text(), "ON")
        self.assertEqual(c_cells["status"].background().color().name(), status_color_hex(1, True))
        self.assertEqual(self.window.channel_cells["T1"]["status"].text(), "ON Ramp↑")
        self.assertEqual(self.window.channel_cells["B1"]["status"].text(), "TRIP")
        self.assertEqual(self.window.channel_cells["T2"]["power"].text(), "OFF")

    def test_channels_grid_has_manual_controls(self) -> None:
        for label in CHANNEL_LABELS:
            cells = self.window.channel_cells[label]
            self.assertIsInstance(cells["vset"], QtWidgets.QDoubleSpinBox)
            self.assertIsInstance(cells["ramp_up"], QtWidgets.QDoubleSpinBox)
            self.assertIsInstance(cells["ramp_down"], QtWidgets.QDoubleSpinBox)
            self.assertIsInstance(cells["power"], QtWidgets.QPushButton)
            self.assertTrue(cells["power"].isCheckable())
        self.assertTrue(hasattr(self.window, "all_on_button"))
        self.assertTrue(hasattr(self.window, "all_off_button"))
        self.assertTrue(hasattr(self.window, "refresh_setpoints_button"))

    def test_manual_controls_disabled_when_disconnected(self) -> None:
        self.assertFalse(self.window.channel_cells["C"]["vset"].isEnabled())
        self.assertFalse(self.window.channel_cells["C"]["ramp_up"].isEnabled())
        self.assertFalse(self.window.channel_cells["C"]["ramp_down"].isEnabled())
        self.assertFalse(self.window.channel_cells["C"]["power"].isEnabled())
        self.assertFalse(self.window.all_on_button.isEnabled())
        self.assertFalse(self.window.refresh_setpoints_button.isEnabled())

    def test_control_refresh_seeds_editable_boxes_from_hardware(self) -> None:
        self.window._on_control_refresh(
            [
                ChannelControlState("C", 0, 120.0, 10.0, 11.0),
                ChannelControlState("T1", 1, 220.0, 20.0, 21.0),
                ChannelControlState("B1", 2, 320.0, 30.0, 31.0),
                ChannelControlState("T2", 3, 420.0, 40.0, 41.0),
            ]
        )

        c_cells = self.window.channel_cells["C"]
        self.assertAlmostEqual(c_cells["vset"].value(), 120.0)
        self.assertAlmostEqual(c_cells["ramp_up"].value(), 10.0)
        self.assertAlmostEqual(c_cells["ramp_down"].value(), 11.0)

    def test_channels_footer_status_updates(self) -> None:
        self.window._set_channels_status("Setpoints refreshed.")
        self.assertEqual(self.window.channels_status_label.text(), "Setpoints refreshed.")

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

        worker.set_channel_ramp_up("C", 12.0)
        self.assertAlmostEqual(worker.backend._channel_state["C"]["ramp_up_v_s"], 12.0)

        worker.set_channel_ramp_down("C", 8.0)
        self.assertAlmostEqual(worker.backend._channel_state["C"]["ramp_down_v_s"], 8.0)

        worker.set_channel_power("C", True)
        self.assertTrue(worker.backend._channel_state["C"]["is_on"])

        worker.set_all_power(False)
        self.assertTrue(all(not st["is_on"] for st in worker.backend._channel_state.values()))

        worker.backend.disconnect()


if __name__ == "__main__":
    unittest.main()
