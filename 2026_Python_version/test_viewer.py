from __future__ import annotations

import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PyQt5 import QtWidgets

from caen_interface import CHANNEL_LABELS, SimulationInterface
from data_logger import DataLogger
from plotly_view import PlotlyViewer, series_from_csv
from scan_controller import ScanCallbacks, ScanController, ScanParameters


class SeriesFromCsvTests(unittest.TestCase):
    def test_parses_scan_csv_into_per_channel_series(self) -> None:
        with TemporaryDirectory() as td:
            backend = SimulationInterface(seed=7)
            backend.connect()
            backend.set_ramp_rates(300.0, 300.0)
            logger = DataLogger(Path(td))
            params = ScanParameters(
                label="Drift", vthgem1_start_v=150, vthgem1_stop_v=250, vthgem1_step_v=50,
                drift_field_kv_cm=0.4, induction_field_kv_cm=2.0, wait_seconds=0.0,
            )
            csv_path = logger.open_run(params.label)
            ScanController().run_scan(backend, params, logger, ScanCallbacks(), threading.Event())
            logger.close()
            series = series_from_csv(csv_path)

        self.assertEqual(series["x"], [150.0, 200.0, 250.0])
        self.assertEqual(set(series["channels"]), set(CHANNEL_LABELS))
        self.assertEqual(len(series["channels"]["C"]), 3)
        self.assertIn("Ed=0.4", series["label"])
        self.assertIn("Ei=2", series["label"])


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
