# THGEM Exercise GUI (CAEN N1470 charge transport)

A PyQt5 application for the THGEM charge-transport exercise. It drives a **CAEN N1470**
high-voltage power supply, automates the voltage/field scans, monitors and manually controls all
four channels, plots current vs THGEM1 voltage live, and logs every point to CSV.

The GUI design (tabbed shell + GECO-style channel grid) is adapted from
[weizmann-atlas/caen_logger](https://github.com/weizmann-atlas/caen_logger).

Two backends:

- **Simulation** — works on any OS with no hardware. Use this for teaching/demos.
- **CAEN USB-VCP** — drives the N1470 over USB through the official **CAEN HV Wrapper** library
  on the Windows lab PC. The default **Auto** transport tries the `caen-libs` path first and
  falls back to the raw wrapper DLL path if needed.

## Interface

A single window with three tabs:

- **Setup** — choose the backend (Simulation / CAEN USB-VCP) and COM port, then Connect/Disconnect.
  For hardware, an advanced section exposes the transport mode (`Auto`, `caen-libs`, `raw wrapper`)
  plus logger-style USB-VCP tuple fields (baud, data bits, stop bits, parity, board number).
- **Channels** — a live grid for the four channels (C, T1, B1, T2): VMon, IMon, colour-coded status,
  plus **manual control** — an editable **VSet** (applies on Enter / focus-out), a per-channel
  **Power** toggle, and **All ON / All OFF**. Manual control is disabled while a scan runs.
- **Scan** — pick a recipe (Reference / Collection / Transfer field / Drift field), Start/Abort, and
  watch the live **current vs THGEM1 voltage** plots (Plotly in a Qt WebEngine view) with a run log.

## Run from source

Requires Python 3.10+.

```bash
cd 2026_Python_version
pip install -r requirements.txt
python main.py
```

Pick **Simulation** in the *Setup* tab, then **Connect** — no hardware needed. (Runtime deps:
PyQt5, PyQtWebEngine, plotly, pyserial, caen-libs.)

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

1. Install the **CAEN HV Wrapper** library on the Windows lab PC (download from
   <https://www.caen.it/products/caen-hv-wrapper-library/>). `caen-libs` loads it automatically —
   no DLL needs to be copied next to the exe.
2. Connect the N1470 over USB and note its COM port.
3. In the *Setup* tab choose backend **CAEN USB-VCP**, select the COM port, leave transport on
   **Auto**, and **Connect**. The logger-aligned default USB tuple is `COMx_9600_8_1_none_0`.

> The hardware backend is exercised on the lab PC (it needs the N1470 + the CAEN HV Wrapper).
> Simulation covers everything else, including CI and development.

## CAEN troubleshooting

- **N1470H is still treated as `N1470`** by the CAEN HV Wrapper path in this app. The `H` suffix
  does not currently map to a separate wrapper system type.
- Install **CAEN HV Wrapper Rel. 5.10 or newer** on the Windows lab PC. N1470 support was added in
  Rel. 5.10 (December 2012); **6.x is preferred**.
- If **Auto** fails, force **Transport = caen-libs** and then **Transport = raw wrapper** to compare
  the two code paths directly.
- For N1470/N1471 USB, this app now mirrors `caen_logger`: it sends **no username/password over USB**.
- Keep the default USB-VCP tuple unless you are matching a known-working legacy setup:
  `COM_9600_8_1_none_0`.
- `LOGINFAILED` on USB is usually a COM-port or USB-tuple mismatch, not a username/password problem.
- Connection failures now include the backend attempted and the exact USB-VCP argument string, which
  makes it easier to compare this GUI against an older working setup.

## Where measurements are saved

CSV files are written to a `measurements/` folder next to the executable (or next to `main.py`
when running from source), named `YYYYmmdd_HHMMSS_<mode>.csv`.

## Development

Run the test suite headless:

```bash
cd 2026_Python_version
QT_QPA_PLATFORM=offscreen pytest        # Windows: set QT_QPA_PLATFORM=offscreen && pytest
```
