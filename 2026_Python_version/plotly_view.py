"""File-based current-vs-THGEM1-voltage viewer rendered with Plotly in a Qt
WebEngine view.

The viewer loads scan **CSV files** and plots them (overlaying several, with
per-channel toggles), and can *follow* an active scan's CSV for a near-live view.
Plotting is decoupled from the scan: the Scan tab just writes CSV.

The ``QWebEngineView`` is created **lazily** on first render — constructing the
widget (e.g. in tests, or on a headless CI runner) never instantiates QtWebEngine,
which crashes on a display-less / GPU-less machine.

The Plotly page (and the bundled plotly.js) is served over a 127.0.0.1 loopback
HTTP server rather than ``file://`` / ``setHtml``: QtWebEngine cannot load local
content reliably once the app is frozen by PyInstaller. This mirrors
weizmann-atlas/CAEN-Plotly-Viewer-From-Log.
"""

from __future__ import annotations

import csv as _csv
import json
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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
function addFullSeries(name, color, xs, ys) {
   Plotly.addTraces('graph', [{x: xs, y: ys, mode: 'lines+markers', name: name,
      line: {color: color, width: 2}, marker: {color: color, size: 5}}]);
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


def _finite_or_none(value) -> float | None:
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None


def _safe_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _legend_for(path: Path, mode: str, e_drift, e_induction) -> str:
    parts: list[str] = []
    if mode:
        parts.append(str(mode))
    if e_drift is not None and e_induction is not None:
        parts.append(f"Ed={e_drift:g}, Ei={e_induction:g} kV/cm")
    return " · ".join(parts) if parts else path.stem


def series_from_csv(path) -> dict:
    """Parse a scan CSV into per-channel current-vs-V_THGEM1 series + a legend
    label. Pure (no Qt) so it is unit-testable headless.

    Returns ``{"x": [...], "channels": {label: [...]}, "label": str, "path": str}``.
    """
    path = Path(path)
    xs: list[float] = []
    channels: dict[str, list[float | None]] = {label: [] for label in CHANNEL_LABELS}
    e_drift = e_induction = None
    mode = ""
    with path.open("r", newline="", encoding="utf-8") as handle:
        for row in _csv.DictReader(handle):
            x = _safe_float(row.get("v_thgem1_v"))
            if x is None:
                continue
            xs.append(x)
            for label in CHANNEL_LABELS:
                channels[label].append(_safe_float(row.get(f"{label.lower()}_imon_ua")))
            if e_drift is None:
                e_drift = _safe_float(row.get("e_drift_kv_cm"))
                e_induction = _safe_float(row.get("e_transfer_kv_cm"))
                mode = row.get("mode") or row.get("subscan_label") or ""
    return {"x": xs, "channels": channels, "label": _legend_for(path, mode, e_drift, e_induction), "path": str(path)}


class PlotlyViewer(QtWidgets.QWidget):
    """Plots one or more scan CSVs (current vs V_THGEM1). The QtWebEngine view is
    created lazily on the first render."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._placeholder = QtWidgets.QLabel(
            'Load a scan CSV (or tick "Follow active scan") to plot current vs V_THGEM1.'
        )
        self._placeholder.setAlignment(QtCore.Qt.AlignCenter)
        self._placeholder.setStyleSheet("color: grey; font-style: italic;")
        self._layout.addWidget(self._placeholder)
        self._page: _PlotPage | None = None

        self._files: list[dict] = []
        self._live: dict | None = None
        self._visible: set[str] = set(CHANNEL_LABELS)

        self._follow = False
        self._active_path = None
        self._follow_timer = QtCore.QTimer(self)
        self._follow_timer.setInterval(1000)
        self._follow_timer.timeout.connect(self._poll_active)

    def _ensure_page(self) -> None:
        if self._page is None:
            self._placeholder.hide()
            self._page = _PlotPage()
            self._layout.addWidget(self._page)

    def load_files(self, paths) -> None:
        for path in paths:
            try:
                self._files.append(series_from_csv(path))
            except Exception:  # pragma: no cover - bad/locked file
                continue
        self._render()

    def clear(self) -> None:
        self._files = []
        self._live = None
        if self._page is not None:
            self._page.run_js("clearAll();")
            self._page.run_js('setSubtitle("");')

    def set_channel_visible(self, label: str, visible: bool) -> None:
        if visible:
            self._visible.add(label)
        else:
            self._visible.discard(label)
        self._render()

    def set_follow(self, on: bool) -> None:
        self._follow = bool(on)
        if self._follow and self._active_path:
            self._poll_active()
            self._follow_timer.start()
        else:
            self._follow_timer.stop()
            if not self._follow:
                self._live = None
                self._render()

    def set_active_csv(self, path) -> None:
        """A scan started; track this CSV if 'Follow active scan' is on."""
        self._active_path = path
        self._live = None
        if self._follow:
            self._poll_active()
            self._follow_timer.start()

    def notify_scan_finished(self) -> None:
        self._follow_timer.stop()
        if self._follow and self._active_path:
            self._poll_active()  # one last refresh of the completed curve

    def _poll_active(self) -> None:
        if not self._active_path or not Path(self._active_path).exists():
            return
        try:
            self._live = series_from_csv(self._active_path)
        except Exception:  # pragma: no cover - mid-write read
            return
        self._render()

    def _render(self) -> None:
        series_list = list(self._files)
        if self._live is not None:
            series_list.append(self._live)
        if not series_list:
            if self._page is not None:
                self._page.run_js("clearAll();")
            return
        self._ensure_page()
        self._page.run_js("clearAll();")
        multi = len(series_list) > 1
        for series in series_list:
            for label in CHANNEL_LABELS:
                if label not in self._visible:
                    continue
                ys = [_finite_or_none(v) for v in series["channels"].get(label, [])]
                name = f"{label} · {series['label']}" if multi else label
                color = CHANNEL_COLORS.get(label, "#888888")
                self._page.run_js(
                    f"addFullSeries({json.dumps(name)}, {json.dumps(color)}, "
                    f"{json.dumps(series['x'])}, {json.dumps(ys)});"
                )
        self._page.run_js(f"setSubtitle({json.dumps(' | '.join(s['label'] for s in series_list))});")
