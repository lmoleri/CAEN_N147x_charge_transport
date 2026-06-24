"""Live scan plots rendered with Plotly inside a Qt WebEngine view.

It exposes the same surface
(``reset_tabs`` / ``activate_subscan`` / ``append_record``) so the ``ScanWorker``
wiring in ``main_window`` is unchanged.

The Plotly page (and the bundled plotly.js) is served over a 127.0.0.1 loopback
HTTP server rather than via ``file://`` / ``setHtml``: QtWebEngine cannot load
local content reliably once the app is frozen by PyInstaller. This mirrors the
approach used by weizmann-atlas/CAEN-Plotly-Viewer-From-Log.
"""

from __future__ import annotations

import json
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PyQt5 import QtCore, QtWidgets
from PyQt5.QtWebEngineWidgets import QWebEngineView

from caen_interface import CHANNEL_LABELS, FieldConfig, RunPointRecord

CHANNEL_COLORS = {
    "C": "#d9534f",
    "T1": "#f0ad4e",
    "B1": "#0275d8",
    "T2": "#5cb85c",
}


def _build_page_html() -> str:
    labels = list(CHANNEL_LABELS)
    colors = [CHANNEL_COLORS[label] for label in labels]
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<script src="/plotly.min.js"></script>
<style>
 html,body{{margin:0;height:100%;font-family:-apple-system,Segoe UI,sans-serif;background:#fff}}
 #sub{{padding:4px 10px;color:#555;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
 #graph{{width:100%;height:calc(100% - 26px)}}
</style></head>
<body>
<div id="sub">&nbsp;</div><div id="graph"></div>
<script>
const LABELS = {json.dumps(labels)};
const COLORS = {json.dumps(colors)};
const traces = LABELS.map((l, i) => ({{
   x: [], y: [], mode: 'lines+markers', name: l,
   marker: {{color: COLORS[i], size: 6}}, line: {{color: COLORS[i], width: 2}}
}}));
const layout = {{
   margin: {{l: 62, r: 16, t: 10, b: 48}},
   xaxis: {{title: 'THGEM1 voltage [V]', zeroline: false}},
   yaxis: {{title: 'Current [μA]', zeroline: false}},
   showlegend: true, legend: {{x: 1, y: 1, xanchor: 'right', yanchor: 'top'}}
}};
Plotly.newPlot('graph', traces, layout, {{responsive: true, displaylogo: false}});
function setSubtitle(t) {{ document.getElementById('sub').textContent = t; }}
function addRow(x, ys) {{
   const idx = ys.map((_, i) => i);
   Plotly.extendTraces('graph', {{x: ys.map(() => [x]), y: ys.map(v => [v])}}, idx);
}}
window.__plotReady = true;
</script>
</body></html>"""


class _PlotServer:
    """Singleton loopback server that serves plotly.js and the plot page."""

    _instance: "_PlotServer | None" = None

    def __init__(self) -> None:
        import plotly.offline  # lazy: keep the 4.8 MB import off module load

        plotly_js = plotly.offline.get_plotlyjs().encode("utf-8")
        page = _build_page_html().encode("utf-8")

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
                    # QtWebEngine cancels in-flight requests when a view is hidden
                    # or torn down — not an error worth crashing the thread over.
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
    """One channel-vs-voltage plot; buffers points until the page has loaded."""

    def __init__(self, subtitle: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._ready = False
        self._pending: list[tuple[float, list[float | None]]] = []
        self._subtitle = subtitle
        self.loadFinished.connect(self._on_loaded)
        self.load(QtCore.QUrl(_PlotServer.instance().url))

    def _on_loaded(self, ok: bool) -> None:
        if not ok:
            return
        self._ready = True
        self._run(f"setSubtitle({json.dumps(self._subtitle)})")
        for x, ys in self._pending:
            self._run(f"addRow({json.dumps(x)}, {json.dumps(ys)})")
        self._pending.clear()

    def _run(self, js: str) -> None:
        self.page().runJavaScript(js)

    def add_row(self, x: float, ys: list[float | None]) -> None:
        if self._ready:
            self._run(f"addRow({json.dumps(x)}, {json.dumps(ys)})")
        else:
            self._pending.append((x, ys))


def _finite_or_none(value: float) -> float | None:
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None


class PlotlyScanView(QtWidgets.QTabWidget):
    """Live scan plots backed by Plotly (``reset_tabs`` / ``activate_subscan`` / ``append_record``)."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._pages: dict[str, _PlotPage] = {}

    def reset_tabs(self, field_configs: list[FieldConfig] | tuple[FieldConfig, ...]) -> None:
        while self.count():
            widget = self.widget(0)
            self.removeTab(0)
            widget.deleteLater()
        self._pages.clear()

        for field_config in field_configs:
            subtitle = (
                f"UV expected: {'ON' if field_config.uv_expected else 'OFF'} | "
                f"Edrift={field_config.e_drift_kv_cm:+.1f} kV/cm | "
                f"Etransfer={field_config.e_transfer_kv_cm:+.1f} kV/cm"
            )
            page = _PlotPage(subtitle)
            self._pages[field_config.label] = page
            self.addTab(page, field_config.label)

    def activate_subscan(self, subscan_label: str) -> None:
        for index in range(self.count()):
            if self.tabText(index) == subscan_label:
                self.setCurrentIndex(index)
                return

    def append_record(self, record: RunPointRecord) -> None:
        page = self._pages.get(record.subscan_label)
        if page is None:
            return
        snapshots = record.channel_snapshots()
        ys = [
            _finite_or_none(snapshots[label].imon_ua) if label in snapshots else None
            for label in CHANNEL_LABELS
        ]
        page.add_row(_finite_or_none(record.v_thgem1_v), ys)
