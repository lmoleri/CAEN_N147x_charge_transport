# THGEM Exercise GUI (CAEN N1470 charge transport)

A PyQt5 application for the THGEM charge-transport exercise. It drives a **CAEN N1470**
high-voltage power supply, automates the voltage/field scans, monitors all four channels,
plots current vs THGEM1 voltage in real time, and logs every point to CSV.

Two backends:

- **Simulation** — works on any OS with no hardware. Use this for teaching/demos.
- **CAEN USB-VCP** — Windows only. Talks to the N1470 over USB through `CAENHVWrapper.dll`.

## Run from source

Requires Python 3.10+.

```bash
cd 2026_Python_version
pip install -r requirements.txt
python main.py
```

Pick **Simulation** in the *Backend* selector, then **Connect** — no hardware needed.

## Get the prebuilt Windows bundle

Every push to `main` builds a one-folder Windows bundle in CI. Download it from the
**Actions** tab → latest *Build Windows executable* run → `THGEM_GUI-windows` artifact.
Tagged releases (`vX.Y`) also attach the zip to the GitHub Release.

Unzip it (Desktop or Documents — avoid `Program Files`, which is not writable without admin),
then run `THGEM_GUI\THGEM_GUI.exe`.

## Build the Windows bundle yourself

On a Windows machine with Python on PATH:

```bat
cd 2026_Python_version
build.bat
```

This creates a venv, installs dependencies, and runs PyInstaller against
[`THGEM_GUI.spec`](THGEM_GUI.spec). The result is `dist\THGEM_GUI\THGEM_GUI.exe`.

> PyInstaller cannot cross-compile — a Windows `.exe` must be built on Windows (your PC or the
> CI runner), not on macOS/Linux.

## Using real CAEN hardware

1. Copy **`CAENHVWrapper.dll`** (from CAEN's software package) next to `THGEM_GUI.exe`
   — or, when running from source, next to `main.py`.
2. Connect the N1470 over USB and note its COM port.
3. In the GUI choose backend **CAEN USB-VCP**, select the COM port, and **Connect**.

The DLL is vendor-licensed, so it is not committed to this repo or bundled into the build.

## Where measurements are saved

CSV files are written to a `measurements/` folder next to the executable (or next to `main.py`
when running from source), named `YYYYmmdd_HHMMSS_<mode>.csv`.

## Development

Run the test suite headless:

```bash
cd 2026_Python_version
QT_QPA_PLATFORM=offscreen pytest        # Windows: set QT_QPA_PLATFORM=offscreen && pytest
```
