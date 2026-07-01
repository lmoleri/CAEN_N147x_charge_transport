# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the THGEM Exercise GUI (one-folder Windows bundle).
# Build from inside 2026_Python_version/:
#     pyinstaller --noconfirm THGEM_GUI.spec
# Output: dist/THGEM_GUI/THGEM_GUI.exe  (+ an _internal/ support folder)
#
# The CAEN HV Wrapper DLL is NOT bundled (it ships with CAEN's own installer).
# The embedded Plotly Viewer runs on QtWebEngine, which in a frozen bundle needs
# three things beyond plotly's JS: its resource .pak files, the QtWebEngineProcess
# helper executable, and a runtime hook (hooks/rthook_webengine.py) that points Qt
# at that helper and re-execs it for Chromium's --type= subprocesses. Without the
# hook the helper never starts and the embedded plot renders blank. This packaging
# mirrors weizmann-atlas/CAEN-Plotly-Viewer-From-Log.

import glob
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs

# caen_libs (Python package; the native CAEN DLL is a runtime prerequisite on HW).
caen_datas, caen_binaries, caen_hidden = collect_all("caen_libs")

# Data: plotly's JS bundle (plotly.min.js, served by the Viewer) + ALL PyQt5 data —
# the QtWebEngine resource .pak files, icudtl.dat and translations Chromium needs to
# render the embedded page.
datas = []
datas += collect_data_files("plotly")
datas += collect_data_files("PyQt5")
datas += caen_datas

# Binaries: PyQt5 dynamic libs + the QtWebEngineProcess helper. collect_dynamic_libs
# grabs only .dll/.so, so the helper executable is added explicitly below.
binaries = []
binaries += collect_dynamic_libs("PyQt5")
binaries += caen_binaries

import PyQt5 as _pyqt5

_pyqt5_dir = os.path.dirname(_pyqt5.__file__)
_proc_name = "QtWebEngineProcess.exe" if sys.platform == "win32" else "QtWebEngineProcess"
for _proc in glob.glob(os.path.join(_pyqt5_dir, "**", _proc_name), recursive=True):
    _rel_dir = os.path.relpath(os.path.dirname(_proc), _pyqt5_dir)
    binaries.append((_proc, os.path.join("PyQt5", _rel_dir)))


def _strip_qml(entries):
    # Qt QML is unused by this Widgets+WebEngine app and its deeply-nested paths blow
    # past the Windows 260-char MAX_PATH limit during COLLECT. Filter on the dest.
    return [item for item in entries if "qml" not in item[1].replace("\\", "/").lower().split("/")]


datas = _strip_qml(datas)
binaries = _strip_qml(binaries)

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        # QtWebEngineWidgets pulls in these at the C-extension level; PyInstaller's
        # hook does not always collect them, so list them explicitly.
        'PyQt5.QtWebEngineWidgets',
        'PyQt5.QtWebEngineCore',
        'PyQt5.QtWebChannel',
        'PyQt5.QtPrintSupport',
        'PyQt5.QtNetwork',
        'PyQt5.QtSvg',
        'caen_libs.caenhvwrapper',
    ] + caen_hidden,
    hookspath=['hooks'],
    hooksconfig={},
    runtime_hooks=['hooks/rthook_webengine.py'],
    excludes=['matplotlib', 'tkinter', 'PyQt6', 'PySide2', 'PySide6'],
    noarchive=False,
)

# Post-analysis: drop any QML data/binaries the hooks re-added (TOC dest name = e[0]).
a.datas = [e for e in a.datas if 'qml' not in e[0].replace('\\', '/').lower().split('/')]
a.binaries = [e for e in a.binaries if 'qml' not in e[0].replace('\\', '/').lower().split('/')]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='THGEM_GUI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX corrupts Qt DLLs and causes crashes
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='THGEM_GUI',
)
