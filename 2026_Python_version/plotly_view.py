"""Live scan plot rendered with Plotly inside a Qt WebEngine view.

A single accumulating plot of current vs THGEM1 voltage. Each scan run adds the
four channel traces (C/T1/B1/T2). With *persist* enabled, successive runs are
overlaid and auto-legended (by their drift/induction field settings) so families
of curves can be built up one run at a time; otherwise each run clears the plot.

The Plotly page (and the bundled plotly.js) is served over a 127.0.0.1 loopback
HTTP server rather than via ``file://`` / ``setHtml``: QtWebEngine cannot load
local content reliably once the app is frozen by PyInstaller.
"""

from __future__ import annotations

import json
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PyQt5 import QtCore, QtWidgets
from PyQt5.QtWebEngineWidgets import QWebEngineView

from caen_interface import CHANNEL_LABELS

CHANNEL_COLORS = {
    "C": "#d9534f",
    "T1": "#f0ad4e",
    "B1": "#0275d8",
    "T2": "#5cb85c",
}

_PAGE_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<script src="/plotly.min.js"></script>
<style>
 html,body{margin:0;height:100%;font-family:-apple-system,Segoe UI,sans-serif;background:#fff}
 #sub{padding:4px 10px;color:#555;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 #graph{width:100%;height:calc(100% - 26px)}
</style></head>
<body>
<div id="sub">&nbsp;</div><div id="graph"></div>
<script>
const LAYOUT = {
   margin: {l: 64, r: 16, t: 10, b: 48},
   xaxis: {title: 'THGEM1 voltage [V]', zeroline: false},
   yaxis: {title: 'Current [μA]', zeroline: false},
   showlegend: true, legend: {x: 1, y: 1, xanchor: 'right', yanchor: 'top'}
};
Plotly.newPlot('graph', [], LAYOUT, {responsive: true, displaylogo: false});
function clearAll() { Plotly.react('graph', [], LAYOUT); }
function addSeries(name, color) {
   Plotly.addTraces('graph', [{x: [], y: [], mode: 'lines+markers', name: name,
      line: {color: color, width: 2}, marker: {color: color, size: 6}}]);
}
function addRowAt(indices, x, ys) {
   Plotly.extendTraces('graph', {x: ys.map(() => [x]), y: ys.map(v => [v])}, indices);
}
function setSubtitle(t) { document.getElementById('sub').textContent = t; }
window.__plotReady = true;
</script>
</body></html>"""


class _PlotServer:
    """Singleton loopback server that serves plotly.js and the plot page."""

    _instance: "_PlotServer | None" = None

    def __init__(self) -> None:
        import plotly.offline  # lazy: keep the 4.8 MB import off module load

        plotly_js = plotly.offline.get_plotlyjs().encode("utf-8")
        page = _PAGE_HTML.encode("utf-8")

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:  # silence default stderr logging
                pass

            def do_GET(self) -> None:  # noqa: N802 - http.server API
                if self.path.startswith("/plotly.min.js"):
                    body, ctype = plotly_js, "application/javascript"
                else:
                    body, ctype = page, "text/html; charset=utf-8"
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}/plot"

    @classmethod
    def instance(cls) -> "_PlotServer":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


class _PlotPage(QWebEngineView):
    """Holds the Plotly page; buffers JS calls until the page has loaded."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._ready = False
        self._pending: list[str] = []
        self.loadFinished.connect(self._on_loaded)
        self.load(QtCore.QUrl(_PlotServer.instance().url))

    def _on_loaded(self, ok: bool) -> None:
        if not ok:
            return
        self._ready = True
        for js in self._pending:
            self.page().runJavaScript(js)
        self._pending.clear()

    def run_js(self, js: str) -> None:
        if self._ready:
            self.page().runJavaScript(js)
        else:
            self._pending.append(js)


def _finite_or_none(value: float) -> float | None:
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None


class PlotlyScanView(QtWidgets.QWidget):
    """Single accumulating current-vs-THGEM1-voltage plot with a persist/overlay mode."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._page = _PlotPage()
        layout.addWidget(self._page)
        self._persist = False
        self._trace_count = 0
        self._run_indices: dict[str, int] = {}

    def set_persist(self, on: bool) -> None:
        self._persist = bool(on)

    def clear(self) -> None:
        self._trace_count = 0
        self._run_indices = {}
        self._page.run_js("clearAll();")

    def begin_run(self, params) -> None:
        """Start a new scan run. Clears first unless persist is on."""
        if not self._persist:
            self.clear()
        self._run_indices = {}
        suffix = f" · {params.legend_label()}" if self._persist else ""
        for label in CHANNEL_LABELS:
            name = f"{label}{suffix}"
            color = CHANNEL_COLORS.get(label, "#888888")
            self._page.run_js(f"addSeries({json.dumps(name)}, {json.dumps(color)});")
            self._run_indices[label] = self._trace_count
            self._trace_count += 1
        self._page.run_js(f"setSubtitle({json.dumps(params.describe())});")

    def append_record(self, record) -> None:
        if not self._run_indices:
            return
        snapshots = record.channel_snapshots()
        x = _finite_or_none(record.v_thgem1_v)
        ys = [
            _finite_or_none(snapshots[label].imon_ua) if label in snapshots else None
            for label in CHANNEL_LABELS
        ]
        indices = [self._run_indices[label] for label in CHANNEL_LABELS]
        self._page.run_js(f"addRowAt({json.dumps(indices)}, {json.dumps(x)}, {json.dumps(ys)});")
