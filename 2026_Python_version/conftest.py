"""Shared pytest setup.

Some tests construct the GUI, which pulls in QtWebEngine via ``plotly_view``.
QtWebEngineWidgets must be imported (and ``AA_ShareOpenGLContexts`` set) before any
``QApplication`` is created, so do it here — pytest imports ``conftest`` before any
test module, guaranteeing the correct ordering.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtCore, QtWidgets
from PyQt5 import QtWebEngineWidgets  # noqa: F401  must precede QApplication

QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_ShareOpenGLContexts)

# Create the single QApplication up front so test ordering can't matter.
_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
