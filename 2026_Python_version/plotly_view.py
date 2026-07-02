"""Current-vs-swept-variable viewer: builds a complete Plotly figure as HTML and
loads it in a Qt WebEngine view.

The viewer loads scan **CSV files** and plots them (overlaying several, with
per-channel toggles), and can *follow* an active scan's CSV for a near-live view.
Plotting is decoupled from the scan: the Scan tab just writes CSV.

Rendering mirrors weizmann-atlas/CAEN-Plotly-Viewer-From-Log: the whole figure is
built with ``plotly`` and embedded in a self-contained HTML document
(``fig.to_html(full_html=True, include_plotlyjs=True)``); that document is served
over a 127.0.0.1 loopback HTTP server and the web view simply ``load()``s it. The
plot therefore renders **on page load** — we do not inject traces with
``runJavaScript`` (which silently draws nothing if the injection ever misfires, and
was why the frozen build showed a blank page). ``file://`` / ``setHtml`` are avoided
because QtWebEngine can't load local content reliably once frozen by PyInstaller.

The ``QWebEngineView`` is created **lazily** on first render — constructing the
widget (in tests or on a headless/GPU-less runner) never instantiates QtWebEngine,
which crashes on a display-less machine.
"""

from __future__ import annotations

import base64
import csv as _csv
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from PyQt5 import QtCore, QtWidgets
from PyQt5.QtWebEngineWidgets import QWebEnginePage, QWebEngineView

from caen_interface import CHANNEL_LABELS
from scan_controller import variable_spec_for

CHANNEL_COLORS = {
    "C": "#d9534f",
    "T1": "#f0ad4e",
    "B1": "#0275d8",
    "T2": "#5cb85c",
}

# QtWebEngine 5.15 ships an OLD Chromium (~87). Recent plotly.js bundles call
# newer JS methods (e.g. Array/String.prototype.at, Chromium 92) during load — on
# old Chromium that throws, the plotly IIFE never assigns window.Plotly, and the
# plot stays blank. Polyfill the missing methods BEFORE plotly.js so it runs.
_COMPAT_POLYFILL = (
    "<script>(function(){"
    "function _at(n){n=Math.trunc(n)||0;if(n<0)n+=this.length;"
    "return (n<0||n>=this.length)?undefined:this[n];}"
    "if(!Array.prototype.at){Object.defineProperty(Array.prototype,'at',"
    "{value:_at,writable:true,configurable:true});}"
    "if(!String.prototype.at){Object.defineProperty(String.prototype,'at',"
    "{value:_at,writable:true,configurable:true});}"
    "if(!Object.hasOwn){Object.hasOwn=function(o,k){"
    "return Object.prototype.hasOwnProperty.call(o,k);};}"
    "})();</script>"
)

# plotly.js is a UMD bundle: if a module loader (AMD `define`, CommonJS `module`)
# is present it registers there instead of the global `window.Plotly`, leaving
# `Plotly` undefined and the figure blank. Neutralise loaders so the global branch
# runs. Both are injected into <head> ahead of the inlined plotly.js.
_LOADER_PREAMBLE = (
    _COMPAT_POLYFILL
    + "<script>window.define=undefined;window.exports=undefined;window.module=undefined;</script>"
)


class _PlotServer:
    """Singleton loopback server that serves the current figure HTML."""

    _instance: "_PlotServer | None" = None

    def __init__(self) -> None:
        self._html_lock = threading.Lock()
        self._html_bytes = b"<!DOCTYPE html><html><head><meta charset='utf-8'></head><body></body></html>"
        server = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:  # silence default stderr logging
                pass

            def do_GET(self) -> None:  # noqa: N802 - http.server API
                with server._html_lock:
                    body = server._html_bytes
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def set_html(self, html_bytes: bytes) -> None:
        with self._html_lock:
            self._html_bytes = html_bytes

    @property
    def url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}/plot.html"

    @classmethod
    def instance(cls) -> "_PlotServer":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


class _LoggingWebPage(QWebEnginePage):
    """A page that records JS console messages — including uncaught errors — so a
    plot that loads but doesn't render can be diagnosed instead of failing silently."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.console_messages: list[str] = []

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):  # noqa: N802
        self.console_messages.append(f"[{int(level)}] {message} (line {line_number})")


class _PlotPage(QWebEngineView):
    """Loads the figure HTML served by the loopback server.

    Tracks the real ``loadFinished`` outcome so a failed load (loopback blocked by
    antivirus/firewall, the WebEngine render process not starting, etc.) can be
    surfaced instead of silently leaving a blank view — this is exactly what made
    the frozen build's failure indistinguishable from "loaded but didn't paint".
    Also captures JS console messages for diagnostics (see ``--selftest``).
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._log_page = _LoggingWebPage(self)
        self.setPage(self._log_page)
        self.on_result: Callable[[bool], None] | None = None
        self.loadFinished.connect(self._on_load_finished)

    def console_messages(self) -> list[str]:
        return list(self._log_page.console_messages)

    def _on_load_finished(self, ok: bool) -> None:
        if self.on_result is not None:
            self.on_result(ok)

    def display(self, html_bytes: bytes) -> None:
        server = _PlotServer.instance()
        server.set_html(html_bytes)
        self.load(QtCore.QUrl(server.url))


def _finite_or_none(value) -> float | None:
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None


def _safe_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _legend_for(path: Path, scan_variable: str, mode: str, v_thgem1, e_drift, e_induction) -> str:
    """Build a curve label from the *held* quantities (not the swept one), so an
    overlay of same-program scans is distinguished by what was held constant."""
    parts: list[str] = []
    if mode:
        parts.append(str(mode))
    if scan_variable == "drift_field":
        if v_thgem1 is not None and e_induction is not None:
            parts.append(f"V={v_thgem1:g} V, Ei={e_induction:g} kV/cm")
    elif scan_variable == "induction_field":
        if v_thgem1 is not None and e_drift is not None:
            parts.append(f"V={v_thgem1:g} V, Ed={e_drift:g} kV/cm")
    else:  # thgem_voltage (and legacy CSVs without the column)
        if e_drift is not None and e_induction is not None:
            parts.append(f"Ed={e_drift:g}, Ei={e_induction:g} kV/cm")
    return " · ".join(parts) if parts else path.stem


def series_from_csv(path) -> dict:
    """Parse a scan CSV into per-channel current series vs the swept variable.

    The x-axis follows the ``scan_variable`` column (THGEM voltage, drift field, or
    induction field); CSVs written before that column existed default to V_THGEM1.
    Pure (no Qt) so it is unit-testable headless.

    Returns ``{"x", "channels", "errors", "label", "axis_title", "path"}``. ``errors``
    holds the per-point IMon uncertainty from the ``*_imon_err_ua`` columns (all-None
    for CSVs written before error bars existed).
    """
    path = Path(path)
    xs: list[float] = []
    channels: dict[str, list[float | None]] = {label: [] for label in CHANNEL_LABELS}
    errors: dict[str, list[float | None]] = {label: [] for label in CHANNEL_LABELS}
    scan_variable = "thgem_voltage"
    spec = variable_spec_for(scan_variable)
    mode = ""
    held_v = held_ed = held_ei = None
    captured = False
    with path.open("r", newline="", encoding="utf-8") as handle:
        for row in _csv.DictReader(handle):
            if not captured:
                scan_variable = row.get("scan_variable") or "thgem_voltage"
                spec = variable_spec_for(scan_variable)
                mode = row.get("mode") or row.get("subscan_label") or ""
                held_v = _safe_float(row.get("v_thgem1_v"))
                held_ed = _safe_float(row.get("e_drift_kv_cm"))
                held_ei = _safe_float(row.get("e_transfer_kv_cm"))
                captured = True
            x = _safe_float(row.get(spec.record_column))
            if x is None:
                continue
            xs.append(x)
            for label in CHANNEL_LABELS:
                channels[label].append(_safe_float(row.get(f"{label.lower()}_imon_ua")))
                errors[label].append(_safe_float(row.get(f"{label.lower()}_imon_err_ua")))
    return {
        "x": xs,
        "channels": channels,
        "errors": errors,
        "label": _legend_for(path, scan_variable, mode, held_v, held_ed, held_ei),
        "axis_title": spec.axis_title,
        "path": str(path),
    }


def build_figure(series_list, visible):
    """Build the Plotly figure: one ``lines+markers`` trace per visible channel per
    series. Pure (only plotly, no Qt) so trace counts/names/data are unit-testable."""
    import plotly.graph_objects as go  # lazy: keep the ~0.5 s import off module load

    fig = go.Figure()
    multi = len(series_list) > 1
    for series in series_list:
        for label in CHANNEL_LABELS:
            if label not in visible:
                continue
            ys = [_finite_or_none(v) for v in series["channels"].get(label, [])]
            name = f"{label} · {series['label']}" if multi else label
            color = CHANNEL_COLORS.get(label, "#888888")
            scatter = {
                "x": list(series["x"]), "y": ys, "mode": "lines+markers", "name": name,
                "line": {"color": color, "width": 2}, "marker": {"color": color, "size": 5},
            }
            # Show the measured IMon uncertainty as vertical error bars when present.
            errs_raw = series.get("errors", {}).get(label, [])
            errs = [e if (isinstance(e, (int, float)) and math.isfinite(e) and e > 0) else 0.0 for e in errs_raw]
            if len(errs) == len(ys) and any(e > 0 for e in errs):
                scatter["error_y"] = {"type": "data", "array": errs, "visible": True, "thickness": 1}
            fig.add_scatter(**scatter)

    axis_title = series_list[-1].get("axis_title", "THGEM1 voltage [V]") if series_list else "THGEM1 voltage [V]"
    subtitle = " | ".join(s["label"] for s in series_list)
    fig.update_layout(
        margin={"l": 64, "r": 16, "t": 34 if subtitle else 10, "b": 48},
        xaxis_title=axis_title,
        yaxis_title="Current [μA]",
        title=({"text": subtitle, "x": 0.0, "xanchor": "left", "font": {"size": 12}} if subtitle else None),
        showlegend=True,
        legend={"x": 1, "y": 1, "xanchor": "right", "yanchor": "top"},
        template="plotly_white",
    )
    return fig


def figure_html(series_list, visible) -> str:
    """A self-contained Plotly HTML document (figure + inlined plotly.js). The plot
    is fully described by the returned HTML, so it renders on load (no runJavaScript).
    Unit-testable headless."""
    html = build_figure(series_list, visible).to_html(
        full_html=True,
        include_plotlyjs=True,
        div_id="graph",
        default_width="100%",
        default_height="100%",
        config={"responsive": True, "displaylogo": False},
    )
    return html.replace("<head>", "<head>" + _LOADER_PREAMBLE, 1)


class PlotlyViewer(QtWidgets.QWidget):
    """Plots one or more scan CSVs (current vs the swept variable). The QtWebEngine
    view is created lazily on the first render."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._placeholder = QtWidgets.QLabel(
            'Load a scan CSV (or tick "Follow active scan") to plot current vs the swept variable.'
        )
        self._placeholder.setAlignment(QtCore.Qt.AlignCenter)
        self._placeholder.setStyleSheet("color: grey; font-style: italic;")
        self._layout.addWidget(self._placeholder)
        self._page: _PlotPage | None = None

        self._files: list[dict] = []
        self._live: dict | None = None
        self._visible: set[str] = set(CHANNEL_LABELS)
        self._last_sig: tuple | None = None

        self._follow = False
        self._active_path = None
        self._follow_timer = QtCore.QTimer(self)
        self._follow_timer.setInterval(1000)
        self._follow_timer.timeout.connect(self._poll_active)

    def _ensure_page(self) -> None:
        if self._page is None:
            page = _PlotPage()  # may raise if plotly/QtWebEngine cannot initialise
            page.on_result = self._on_page_load_finished
            self._layout.addWidget(page)
            self._page = page
            self._placeholder.hide()  # only hide once the page really exists

    def _on_page_load_finished(self, ok: bool) -> None:
        if ok:
            # Loaded fine — restore visibility in case a previous load had failed.
            self._placeholder.hide()
            self._page.show()
            return
        # The page never loaded: distinguish this from "loaded but didn't paint"
        # (a GPU/driver rendering issue), which previously looked identical (blank).
        self._page.hide()
        self._placeholder.setText(
            "Plot failed to load in the embedded browser.\n"
            "This can happen if antivirus/firewall blocks the local loopback connection, "
            "or if hardware-accelerated rendering fails on this machine — try setting the "
            "environment variable QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu before launching."
        )
        self._placeholder.show()

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
        self._render()  # empty figure if a page exists, else keeps the placeholder

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

    def _current_series(self) -> list:
        series_list = list(self._files)
        if self._live is not None:
            series_list.append(self._live)
        return series_list

    def has_plot(self) -> bool:
        """True once something has been plotted (used to enable the Save button)."""
        return bool(self._current_series())

    def save_html(self, path) -> None:
        """Write the current plot as a self-contained interactive HTML document."""
        Path(path).write_text(figure_html(self._current_series(), self._visible), encoding="utf-8")

    def save_png(self, path, on_done: "Callable[[bool, str], None]") -> None:
        """Render the current plot to a PNG using the web view's own Plotly (client
        side — no kaleido). Asynchronous: ``on_done(ok, message)`` fires when finished."""
        if self._page is None:
            on_done(False, "There is no rendered plot to save yet.")
            return
        page = self._page.page()
        page.runJavaScript(
            "window.__png=null;"
            "try{Plotly.toImage(document.getElementById('graph'),"
            "{format:'png',width:1200,height:800})"
            ".then(function(u){window.__png=u;})"
            ".catch(function(e){window.__png='ERR:'+e;});}"
            "catch(e){window.__png='ERR:'+e;}"
        )
        attempts = {"n": 0}
        timer = QtCore.QTimer(self)

        def finish(ok: bool, message: str) -> None:
            timer.stop()
            on_done(ok, message)

        def poll() -> None:
            attempts["n"] += 1
            if attempts["n"] > 100:  # ~10 s
                finish(False, "Timed out rendering the PNG in the embedded browser.")
                return

            def got(url) -> None:
                if url is None:
                    return  # promise not resolved yet — keep polling
                if isinstance(url, str) and url.startswith("data:image/png;base64,"):
                    try:
                        Path(path).write_bytes(base64.b64decode(url.split(",", 1)[1]))
                    except Exception as exc:  # pragma: no cover - disk/permission error
                        finish(False, f"Could not write the PNG: {exc}")
                        return
                    finish(True, str(path))
                else:
                    finish(False, f"PNG rendering failed: {url}")

            page.runJavaScript("window.__png", got)

        timer.timeout.connect(poll)
        timer.start(100)

    def _render(self) -> None:
        series_list = self._current_series()
        if not series_list and self._page is None:
            return  # nothing to show yet — keep the placeholder

        # Skip redundant rebuilds (e.g. a 1 s live poll with no new point).
        signature = (
            tuple(len(series["x"]) for series in series_list),
            tuple(series["path"] for series in series_list),
            tuple(sorted(self._visible)),
        )
        if signature == self._last_sig:
            return
        self._last_sig = signature

        try:
            self._ensure_page()
            html = figure_html(series_list, self._visible)
        except Exception as exc:  # plotly/QtWebEngine init or figure build failed
            self._placeholder.setText(f"Plot unavailable: {exc}")
            self._placeholder.show()
            self._last_sig = None
            return
        self._page.display(html.encode("utf-8"))
