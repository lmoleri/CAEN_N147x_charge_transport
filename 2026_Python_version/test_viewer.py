from __future__ import annotations

import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PyQt5 import QtWidgets

from caen_interface import CHANNEL_LABELS, SimulationInterface
from data_logger import DataLogger
from plotly_view import PlotlyViewer, build_figure, figure_html, series_from_csv
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


class PlotlyDataTests(unittest.TestCase):
    def test_plotly_js_data_is_available(self) -> None:
        # The Viewer serves this at runtime; if plotly's package data is missing
        # (e.g. not bundled into the frozen exe) the embedded plot renders blank.
        from plotly import offline

        plotly_js = offline.get_plotlyjs()
        self.assertIsInstance(plotly_js, str)
        self.assertGreater(len(plotly_js), 100_000)


class FigureHtmlTests(unittest.TestCase):
    def _series(self, xs, axis_title="THGEM1 voltage [V]", label="THGEM"):
        return {
            "x": list(xs),
            "channels": {lbl: [11.0 + idx] * len(xs) for idx, lbl in enumerate(CHANNEL_LABELS)},
            "label": label,
            "axis_title": axis_title,
            "path": "/tmp/scan.csv",
        }

    def test_build_figure_one_trace_per_visible_channel(self) -> None:
        fig = build_figure([self._series([200.0, 250.0])], set(CHANNEL_LABELS))
        self.assertEqual(len(fig.data), len(CHANNEL_LABELS))
        self.assertEqual({trace.name for trace in fig.data}, set(CHANNEL_LABELS))
        self.assertEqual(fig.layout.xaxis.title.text, "THGEM1 voltage [V]")

    def test_build_figure_hidden_channels_dropped(self) -> None:
        fig = build_figure([self._series([200.0])], {"C", "B1"})
        self.assertEqual({trace.name for trace in fig.data}, {"C", "B1"})

    def test_build_figure_x_axis_follows_axis_title(self) -> None:
        fig = build_figure([self._series([0.0, 0.5], axis_title="Drift field [kV/cm]")], set(CHANNEL_LABELS))
        self.assertEqual(fig.layout.xaxis.title.text, "Drift field [kV/cm]")

    def test_figure_html_embeds_plot_and_data_on_load(self) -> None:
        # The whole figure is in the served HTML, so it renders on page load with no
        # runJavaScript (which was why the frozen Viewer was blank).
        html = figure_html([self._series([200.0, 250.0, 314.0])], set(CHANNEL_LABELS))
        self.assertIn("Plotly.newPlot", html)
        self.assertIn('id="graph"', html)  # --selftest reads document.getElementById('graph').data
        self.assertIn("314", html)  # x data embedded in the document

    def test_figure_html_empty_is_valid(self) -> None:
        self.assertIn("Plotly.newPlot", figure_html([], set(CHANNEL_LABELS)))


if __name__ == "__main__":
    unittest.main()
