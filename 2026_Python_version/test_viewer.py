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


class _FakePage:
    """Duck-typed stand-in for _PlotPage: only hide()/show() are exercised by
    PlotlyViewer._on_page_load_finished, tracked here without touching Qt."""

    def __init__(self) -> None:
        self.hidden = False

    def hide(self) -> None:
        self.hidden = True

    def show(self) -> None:
        self.hidden = False


def _run_to_csv(td: str, params: ScanParameters, on_labels=CHANNEL_LABELS) -> Path:
    backend = SimulationInterface(seed=7)
    backend.connect()
    backend.set_ramp_rates(300.0, 300.0)
    backend.power_on_channels(list(on_labels))  # the scan drives only ON channels
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

        # gain sweeps B1 (150→250); plotted x is the MEASURED ΔV = |V_B1| + |V_T1(50)|,
        # so it tracks the nominal 200/250/300 within the VMon read noise, with x errors.
        for got, want in zip(series["x"], [200.0, 250.0, 300.0]):
            self.assertAlmostEqual(got, want, delta=1.0)
        self.assertEqual(len(series["xerr"]), 3)
        self.assertTrue(all(e is not None and e >= 0 for e in series["xerr"]))
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

        # x is the MEASURED drift field (|V_C|-|V_T1|)/(1000·gap), ≈ nominal 0/0.5/1.0.
        for got, want in zip(series["x"], [0.0, 0.5, 1.0]):
            self.assertAlmostEqual(got, want, delta=0.05)
        self.assertEqual(len(series["xerr"]), 3)
        self.assertEqual(series["axis_title"], "Drift field [kV/cm]")
        self.assertIn("V=700", series["label"])  # held quantities, not the swept E_drift
        self.assertIn("Ei=1", series["label"])

    def test_off_channels_are_marked_absent_and_not_plotted(self) -> None:
        with TemporaryDirectory() as td:
            params = ScanParameters(
                label="THGEM", scan_variable=ScanVariable.THGEM_VOLTAGE,
                start=400, stop=500, step=50, wait_seconds=0.0,
            )
            series = series_from_csv(_run_to_csv(td, params, on_labels=["B1", "T2"]))
        self.assertEqual(series["present"], {"B1", "T2"})  # C, T1 were OFF the whole scan
        fig = build_figure([series], set(CHANNEL_LABELS))  # visible=all, but present filters
        self.assertEqual({trace.name for trace in fig.data}, {"B1", "T2"})

    def test_series_includes_measured_error_bars(self) -> None:
        with TemporaryDirectory() as td:
            params = ScanParameters(
                label="THGEM", scan_variable=ScanVariable.THGEM_VOLTAGE,
                start=400, stop=500, step=50, wait_seconds=0.0,
            )
            series = series_from_csv(_run_to_csv(td, params))
        self.assertEqual(set(series["errors"]), set(CHANNEL_LABELS))
        self.assertEqual(len(series["errors"]["C"]), len(series["x"]))
        self.assertTrue(any((e or 0) > 0 for e in series["errors"]["B1"]))  # measured spread

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

    def test_load_failure_surfaces_error_instead_of_blank(self) -> None:
        # A duck-typed stand-in for the page: _on_page_load_finished only calls
        # hide()/show() on it, so this avoids constructing a real QWebEngineView
        # (which crashes headless/GPU-less) while still exercising the real logic.
        viewer = PlotlyViewer()
        viewer._page = _FakePage()
        viewer._on_page_load_finished(False)
        self.assertIn("Plot failed to load", viewer._placeholder.text())
        self.assertFalse(viewer._placeholder.isHidden())  # error text shown
        self.assertTrue(viewer._page.hidden)  # blank page hidden behind it
        viewer.deleteLater()

    def test_load_success_restores_page_visibility(self) -> None:
        viewer = PlotlyViewer()
        viewer._page = _FakePage()
        viewer._page.hidden = True
        viewer._placeholder.setText("Plot failed to load in the embedded browser.")
        viewer._on_page_load_finished(True)
        self.assertTrue(viewer._placeholder.isHidden())
        self.assertFalse(viewer._page.hidden)
        viewer.deleteLater()

    def test_save_html_writes_selfcontained_document(self) -> None:
        # Seed series directly (no load_files → no QWebEngineView); save_html is pure.
        viewer = PlotlyViewer()
        viewer._files = [{
            "x": [200.0, 250.0],
            "channels": {label: [1.0, 2.0] for label in CHANNEL_LABELS},
            "errors": {label: [0.1, 0.1] for label in CHANNEL_LABELS},
            "label": "THGEM", "axis_title": "THGEM1 voltage [V]", "path": "/tmp/x.csv",
        }]
        self.assertTrue(viewer.has_plot())
        with TemporaryDirectory() as td:
            out = Path(td) / "plot.html"
            viewer.save_html(out)
            text = out.read_text(encoding="utf-8")
        self.assertIn("Plotly.newPlot", text)
        self.assertIn('id="graph"', text)
        viewer.deleteLater()

    def test_save_png_without_a_rendered_page_reports_failure(self) -> None:
        viewer = PlotlyViewer()
        result: dict = {}
        viewer.save_png("/tmp/should-not-be-written.png",
                        lambda ok, msg: result.update(ok=ok, msg=msg))
        self.assertFalse(result["ok"])  # no page yet → immediate, headless-safe failure
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

    def test_build_figure_adds_error_bars_when_present(self) -> None:
        series = self._series([200.0, 250.0])
        series["errors"] = {label: [0.1, 0.2] for label in CHANNEL_LABELS}
        trace = build_figure([series], {"C"}).data[0]
        self.assertTrue(trace.error_y.visible)
        self.assertEqual(tuple(trace.error_y.array), (0.1, 0.2))

    def test_build_figure_omits_error_bars_without_errors(self) -> None:
        # _series has no "errors" key (as legacy/old CSVs and manual dicts) → no bars.
        trace = build_figure([self._series([200.0, 250.0])], {"C"}).data[0]
        self.assertIsNone(trace.error_y.array)

    def test_build_figure_markers_are_enlarged(self) -> None:
        trace = build_figure([self._series([200.0, 250.0])], {"C"}).data[0]
        self.assertGreaterEqual(trace.marker.size, 8)

    def test_build_figure_log_y_uses_log_axis_and_absolute_values(self) -> None:
        series = self._series([200.0, 250.0])
        series["channels"]["C"] = [-3.0, -4.0]  # C is recorded negative (polarity)
        fig = build_figure([series], {"C"}, y_log=True)
        self.assertEqual(fig.layout.yaxis.type, "log")
        self.assertEqual(tuple(fig.data[0].y), (3.0, 4.0))  # |I| so it shows on a log axis

    def test_build_figure_linear_keeps_signed_values(self) -> None:
        series = self._series([200.0, 250.0])
        series["channels"]["C"] = [-3.0, -4.0]
        fig = build_figure([series], {"C"}, y_log=False)
        self.assertEqual(fig.layout.yaxis.type, "linear")
        self.assertEqual(tuple(fig.data[0].y), (-3.0, -4.0))

    def test_build_figure_adds_x_error_bars_when_present(self) -> None:
        series = self._series([200.0, 250.0])
        series["xerr"] = [0.5, 0.7]
        trace = build_figure([series], {"C"}).data[0]
        self.assertTrue(trace.error_x.visible)
        self.assertEqual(tuple(trace.error_x.array), (0.5, 0.7))

    def test_build_figure_present_set_filters_channels(self) -> None:
        series = self._series([200.0])
        series["present"] = {"B1"}
        fig = build_figure([series], set(CHANNEL_LABELS))
        self.assertEqual({trace.name for trace in fig.data}, {"B1"})

    def test_figure_html_embeds_plot_and_data_on_load(self) -> None:
        # The whole figure is in the served HTML, so it renders on page load with no
        # runJavaScript (which was why the frozen Viewer was blank).
        html = figure_html([self._series([200.0, 250.0, 314.0])], set(CHANNEL_LABELS))
        self.assertIn("Plotly.newPlot", html)
        self.assertIn('id="graph"', html)  # --selftest reads document.getElementById('graph').data
        self.assertIn("314", html)  # x data embedded in the document

    def test_figure_html_polyfills_old_chromium_before_plotly(self) -> None:
        # QtWebEngine 5.15 = Chromium 87, which lacks Array.prototype.at (Chromium 92)
        # that plotly.js calls at load. The polyfill must appear BEFORE plotly runs, or
        # the bundle throws and window.Plotly is never defined (blank plot).
        html = figure_html([self._series([1.0, 2.0])], set(CHANNEL_LABELS))
        poly = html.find("Array.prototype.at")
        newplot = html.find("Plotly.newPlot")
        self.assertGreaterEqual(poly, 0)
        self.assertLess(poly, newplot)

    def test_figure_html_empty_is_valid(self) -> None:
        self.assertIn("Plotly.newPlot", figure_html([], set(CHANNEL_LABELS)))


if __name__ == "__main__":
    unittest.main()
