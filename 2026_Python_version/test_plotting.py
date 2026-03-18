from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtWidgets

from caen_interface import ChannelSnapshot, FieldConfig, RunPointRecord
from plotting import ScanPlotTabs


class PlottingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_tabs_are_created_per_subscan_and_receive_points(self) -> None:
        widget = ScanPlotTabs()
        field_configs = [
            FieldConfig("Transfer +1 kV/cm", 0.0, 1.0, True),
            FieldConfig("Transfer +2 kV/cm", 0.0, 2.0, True),
        ]
        widget.reset_tabs(field_configs)

        self.assertEqual(widget.count(), 2)
        widget.activate_subscan("Transfer +2 kV/cm")
        self.assertEqual(widget.currentIndex(), 1)

        record = RunPointRecord.from_snapshots(
            mode="Transfer field scan",
            subscan_label="Transfer +2 kV/cm",
            uv_expected=True,
            point_index=1,
            v_thgem1_v=150.0,
            e_drift_kv_cm=0.0,
            e_transfer_kv_cm=2.0,
            timestamp_iso="2026-03-17T18:00:00+02:00",
            snapshots=[
                ChannelSnapshot("C", 0, "-", 151.0, -0.2, True, 0, "OK"),
                ChannelSnapshot("T1", 1, "-", 1.0, -0.3, True, 0, "OK"),
                ChannelSnapshot("B1", 2, "+", 149.0, 0.4, True, 0, "OK"),
                ChannelSnapshot("T2", 3, "+", 349.0, 0.5, True, 0, "OK"),
            ],
        )
        widget.append_record(record)

        bundle = widget._bundles["Transfer +2 kV/cm"]
        self.assertEqual(len(bundle.x_data["C"]), 1)
        self.assertEqual(bundle.x_data["C"][0], 150.0)
        self.assertAlmostEqual(bundle.y_data["T2"][0], 0.5)


if __name__ == "__main__":
    unittest.main()
