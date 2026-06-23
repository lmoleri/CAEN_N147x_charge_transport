from __future__ import annotations

import sys
from pathlib import Path


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
