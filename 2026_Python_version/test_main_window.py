from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PyQt5 import QtWidgets

from caen_interface import CHANNEL_LABELS, ChannelSnapshot
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

    def test_worker_manual_control_applies_to_simulation_backend(self) -> None:
        from main_window import ScanWorker

        worker = ScanWorker(Path(self._tmp.name))
        worker.connect_backend("Simulation", "")
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
