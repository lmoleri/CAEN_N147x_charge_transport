from __future__ import annotations

import sys
from pathlib import Path


def _selftest(app) -> int:
    """Headless render check used to validate a (frozen) bundle: load the Plotly
    view, push a few points, and confirm Plotly created the traces inside the
    embedded QtWebEngine. Prints SELFTEST OK/FAIL and returns an exit code.
    Run with:  THGEM_GUI --selftest  (set QT_QPA_PLATFORM=offscreen for headless).
    """
    import time

    from PyQt5 import QtCore

    from caen_interface import CHANNEL_LABELS
    from plotly_view import PlotlyScanView
    from scan_controller import ScanController

    controller = ScanController()
    field_configs = list(controller.field_configs_for_mode(controller.mode_names()[0]))[:1]
    view = PlotlyScanView()
    view.resize(420, 300)
    view.show()
    view.reset_tabs(field_configs)
    page = view._pages[field_configs[0].label]

    deadline = time.time() + 40
    while time.time() < deadline and not page._ready:
        app.processEvents()
        time.sleep(0.02)

    class _Snap:
        def __init__(self, ua: float) -> None:
            self.imon_ua = ua

    class _Rec:
        def __init__(self, label: str) -> None:
            self.subscan_label = label
            self.v_thgem1_v = 100.0

        def channel_snapshots(self):
            return {label: _Snap(float(i)) for i, label in enumerate(CHANNEL_LABELS)}

    for _ in range(3):
        view.append_record(_Rec(field_configs[0].label))
    settle = time.time() + 3
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
    ok = isinstance(lens, list) and len(lens) == 4 and all(v == 3 for v in lens)
    print(f"SELFTEST {'OK' if ok else 'FAIL'} (page_ready={page._ready}, trace_lengths={lens})")
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
        # Lightweight frozen-bundle gate for CI: the heavy deps imported above and a
        # QApplication exists; confirm plotly is bundled too, WITHOUT creating a
        # QtWebEngine view (headless CI runners crash building a GL/web context).
        import plotly  # noqa: F401
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
