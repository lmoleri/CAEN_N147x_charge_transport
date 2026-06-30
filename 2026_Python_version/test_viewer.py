from __future__ import annotations

import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PyQt5 import QtWidgets

from caen_interface import CHANNEL_LABELS, SimulationInterface
from data_logger import DataLogger
from plotly_view import PlotlyViewer, series_from_csv
from scan_controller import ScanCallbacks, ScanController, ScanParameters, ScanVariable


def _run_to_csv(td: str, params: ScanParameters) -> Path:
    backend = SimulationInterface(seed=7)
    backend.connect()
    backend.set_ramp_rates(300.0, 300.0)
    backend.power_on_channels(CHANNEL_LABELS)  # the scan drives only ON channels
    logger = DataLogger(Path(td))
    csv_path = logger.open_run(params.label)
    ScanController().run_scan(backend, params, logger, ScanCallbacks(), threading.Event())
    logger.close()
    return csv_path


class SeriesFromCsvTests(unittest.TestCase):
    def test_thgem_scan_x_is_voltage(self) -> None:
        with TemporaryDirectory() as td:
            params = ScanParameters(
                label="THGEM", scan_variable=ScanVariable.THGEM_VOLTAGE,
                start=150, stop=250, step=50, t1_v=50.0, drift_field_kv_cm=0.4, induction_field_kv_cm=2.0,
                wait_seconds=0.0,
            )
            series = series_from_csv(_run_to_csv(td, params))

        # gain sweeps B1 (150→250); recorded/plotted x is ΔV = B1 + T1(50)
        self.assertEqual(series["x"], [200.0, 250.0, 300.0])
        self.assertEqual(series["axis_title"], "THGEM1 voltage [V]")
        self.assertEqual(set(series["channels"]), set(CHANNEL_LABELS))
        self.assertEqual(len(series["channels"]["C"]), 3)
        self.assertIn("Ed=0.4", series["label"])  # held fields in the legend
        self.assertIn("Ei=2", series["label"])

    def test_drift_scan_x_is_drift_field(self) -> None:
        with TemporaryDirectory() as td:
            params = ScanParameters(
                label="Drift", scan_variable=ScanVariable.DRIFT_FIELD,
                start=0.0, stop=1.0, step=0.5, t1_v=300.0, b1_v=400.0, induction_field_kv_cm=1.0,
                wait_seconds=0.0,
            )
            series = series_from_csv(_run_to_csv(td, params))

        self.assertEqual(series["x"], [0.0, 0.5, 1.0])
        self.assertEqual(series["axis_title"], "Drift field [kV/cm]")
        self.assertIn("V=700", series["label"])  # held quantities, not the swept E_drift
        self.assertIn("Ei=1", series["label"])

    def test_legacy_csv_without_scan_variable_defaults_to_voltage(self) -> None:
        with TemporaryDirectory() as td:
            path = Path(td) / "legacy.csv"
            path.write_text(
                "mode,v_thgem1_v,e_drift_kv_cm,e_transfer_kv_cm,"
                "c_imon_ua,t1_imon_ua,b1_imon_ua,t2_imon_ua\n"
                "Old,150,0,1,0.10,0.20,0.30,0.40\n"
                "Old,200,0,1,0.15,0.25,0.35,0.45\n",
                encoding="utf-8",
            )
            series = series_from_csv(path)

        self.assertEqual(series["x"], [150.0, 200.0])
        self.assertEqual(series["axis_title"], "THGEM1 voltage [V]")
        self.assertEqual(series["channels"]["C"], [0.10, 0.15])


class PlotlyViewerLazyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_constructs_without_instantiating_webengine(self) -> None:
        viewer = PlotlyViewer()
        # The QWebEngineView must NOT exist at construction — it crashes on a
        # headless/GPU-less runner; it is created lazily on the first render.
        self.assertIsNone(viewer._page)
        viewer.deleteLater()


if __name__ == "__main__":
    unittest.main()
