from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    try:
        from PyQt5 import QtWidgets
    except ImportError as exc:  # pragma: no cover - dependency-driven
        print(f"PyQt5 is required to run this application: {exc}")
        return 1

    from main_window import MainWindow

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("THGEM Exercise 3.B School GUI")
    app.setOrganizationName("Detector School")

    window = MainWindow(Path(__file__).resolve().parent)
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
