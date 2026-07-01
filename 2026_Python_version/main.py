from __future__ import annotations

import sys
from pathlib import Path


def _selftest(app) -> int:
    """End-to-end render check for a (frozen) bundle: load a small CSV into the
    Viewer and confirm Plotly created the per-channel traces inside the embedded
    QtWebEngine. Needs a real display (QtWebEngine does not render offscreen). Prints
    SELFTEST OK/FAIL and returns an exit code.  Run:  THGEM_GUI --selftest
    """
    import csv
    import tempfile
    import time
    from pathlib import Path

    from PyQt5 import QtCore

    from caen_interface import CHANNEL_LABELS
    from plotly_view import PlotlyViewer

    # A minimal 3-point THGEM-scan CSV (only the columns series_from_csv reads).
    header = ["mode", "scan_variable", "v_thgem1_v"] + [f"{c.lower()}_imon_ua" for c in CHANNEL_LABELS]
    rows = [
        ["Selftest", "thgem_voltage", "200", "0.10", "0.20", "0.30", "0.40"],
        ["Selftest", "thgem_voltage", "250", "0.15", "0.25", "0.35", "0.45"],
        ["Selftest", "thgem_voltage", "300", "0.20", "0.30", "0.40", "0.50"],
    ]
    csv_path = Path(tempfile.mkdtemp()) / "selftest.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)

    view = PlotlyViewer()
    view.resize(480, 320)
    view.show()
    view.load_files([str(csv_path)])

    page = view._page
    if page is None:
        print("SELFTEST FAIL (Viewer page was not created)")
        return 1

    # Wait for the real page-load outcome instead of assuming it succeeded — a
    # failed load (blocked loopback, WebEngine render process not starting, etc.)
    # must be distinguished from "loaded but Chromium didn't paint".
    load_result: dict[str, object] = {}
    page.loadFinished.connect(lambda ok: load_result.setdefault("ok", ok))
    deadline = time.time() + 40
    while time.time() < deadline and "ok" not in load_result:
        app.processEvents()
        time.sleep(0.02)

    if load_result.get("ok") is not True:
        print(f"SELFTEST FAIL (page failed to load: loadFinished={load_result.get('ok')!r})")
        return 1

    settle = time.time() + 1  # let the page finish painting
    while time.time() < settle:
        app.processEvents()
        time.sleep(0.02)

    result: dict[str, object] = {}
    loop = QtCore.QEventLoop()
    page.page().runJavaScript(
        "(document.getElementById('graph')||{}).data ? "
        "document.getElementById('graph').data.map(t=>t.x.length) : null",
        lambda r: (result.__setitem__("r", r), loop.quit()),
    )
    QtCore.QTimer.singleShot(8000, loop.quit)
    loop.exec_()

    lens = result.get("r")
    ok = isinstance(lens, list) and len(lens) == len(CHANNEL_LABELS) and all(v == 3 for v in lens)
    print(f"SELFTEST {'OK' if ok else 'FAIL'} (trace_lengths={lens})")
    return 0 if ok else 1


def main() -> int:
    try:
        from PyQt5 import QtCore, QtWidgets
        # QtWebEngine must be imported before the QApplication is created.
        from PyQt5 import QtWebEngineWidgets  # noqa: F401
    except ImportError as exc:  # pragma: no cover - dependency-driven
        print(f"PyQt5 (with QtWebEngine) is required to run this application: {exc}")
        return 1

    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_ShareOpenGLContexts)

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("THGEM Exercise 3.B School GUI")
    app.setOrganizationName("Detector School")
    app.setStyle("Fusion")

    if "--selfcheck" in sys.argv:
        # Frozen-bundle gate for CI (no QtWebEngine view — headless runners crash
        # building a GL/web context). Confirm plotly's JS *data* is bundled, not just
        # the module: the Viewer serves plotly.offline.get_plotlyjs(), which reads
        # plotly/package_data/plotly.min.js. If that data is missing the embedded plot
        # renders blank, so fail the gate here.
        from plotly import offline
        plotly_js = offline.get_plotlyjs()
        if not isinstance(plotly_js, str) or len(plotly_js) < 100_000:
            size = len(plotly_js) if isinstance(plotly_js, str) else f"type={type(plotly_js).__name__}"
            print(f"SELFCHECK FAIL (plotly.js data missing or too short: {size})")
            return 1
        print("SELFCHECK OK")
        return 0

    if "--selftest" in sys.argv:
        return _selftest(app)

    from main_window import MainWindow

    if getattr(sys, "frozen", False):  # PyInstaller one-folder bundle
        base_dir = Path(sys.executable).resolve().parent
    else:
        base_dir = Path(__file__).resolve().parent

    window = MainWindow(base_dir)
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
