# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the THGEM Exercise GUI (one-folder Windows bundle).
# Build from inside 2026_Python_version/:
#     pyinstaller --noconfirm THGEM_GUI.spec
# Output: dist/THGEM_GUI/THGEM_GUI.exe  (+ an _internal/ support folder)
#
# The CAEN HV Wrapper DLL is NOT bundled (it ships with CAEN's own installer).
# For real hardware, install it on the lab PC; the Simulation backend needs
# nothing. QtWebEngine (for the Plotly plots) is pulled in by PyInstaller's PyQt5
# hooks via the QtWebEngineWidgets hidden import below.

from PyInstaller.utils.hooks import collect_all

# Bundle the caen_libs package (Python files; the native CAEN DLL is not in the
# wheel and remains a runtime prerequisite on the hardware PC).
caen_datas, caen_binaries, caen_hidden = collect_all("caen_libs")

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=caen_binaries,
    datas=caen_datas,
    hiddenimports=['PyQt5.QtWebEngineWidgets', 'caen_libs.caenhvwrapper'] + caen_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'tkinter', 'PyQt6', 'PySide2', 'PySide6'],
    noarchive=False,
)

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
    upx=False,
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
