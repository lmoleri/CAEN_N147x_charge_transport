# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the THGEM Exercise GUI (one-folder Windows bundle).
# Build from inside 2026_Python_version/:
#     pyinstaller --noconfirm THGEM_GUI.spec
# Output: dist/THGEM_GUI/THGEM_GUI.exe  (+ an _internal/ support folder)
#
# The CAEN driver (CAENHVWrapper.dll) is NOT bundled. For real hardware,
# drop it next to THGEM_GUI.exe; the Simulation backend needs nothing.

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=['pyqtgraph'],
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
