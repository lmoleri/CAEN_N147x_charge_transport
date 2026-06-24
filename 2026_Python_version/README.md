# THGEM Exercise GUI (CAEN N1471H / N147x charge transport)

A PyQt5 application for the THGEM charge-transport exercise. It drives a **CAEN N1471H**
high-voltage power supply, automates the voltage/field scans, monitors and manually controls all
four channels, plots current vs THGEM1 voltage live, and logs every point to CSV. The wrapper
troubleshooting path still supports the closely related **N1470** family.

The GUI design (tabbed shell + GECO-style channel grid) is adapted from
[weizmann-atlas/caen_logger](https://github.com/weizmann-atlas/caen_logger).

Two backends:

- **Simulation** — works on any OS with no hardware. Use this for teaching/demos.
- **CAEN USB-VCP** — drives the N1471H / N1470 family over USB. The default transport is now the
  legacy **direct serial** path inferred from the older `N1471A` VISA-style LabVIEW stack
  bundled in this repo. Wrapper transports remain available for comparison:
  `wrapper auto` (`caen-libs` then raw wrapper), `caen-libs`, and `raw wrapper`.

## Interface

A single window with three tabs:

- **Setup** — choose the backend (Simulation / CAEN USB-VCP) and COM port, then Connect/Disconnect.
  For hardware, an advanced section exposes the transport mode (`direct serial`, `wrapper auto`,
  `caen-libs`, `raw wrapper`) plus logger-style USB serial fields (baud, data bits, stop bits,
  parity). The wrapper transports additionally expose a **Model** selector
  (`N1471H / N1471`, `N1470H / N1470`) and **Current source** selector
  (`Auto`, `IMonL`, `IMonH`, `IMon`). Board/LBus, model, and current source are wrapper-only.
- **Channels** — a fixed GECO-style live grid for the four THGEM channels (C, T1, B1, T2) with
  `Pw`, editable `VSet`, live `VMon`, live `IMon`, editable `RUp`, editable `RDW`, and decoded
  status text. `VSet`, `RUp`, and `RDW` apply on Enter / focus-out, `Pw` remains a per-channel
  toggle, and `All ON`, `All OFF`, plus `Refresh Setpoints` reseed the editable boxes from the
  hardware. A footer on this tab shows the latest manual action or failure. Manual control is
  disabled while a scan runs.
- **Scan** — pick a recipe (Reference / Collection / Transfer field / Drift field), Start/Abort, and
  watch the live **current vs THGEM1 voltage** plots (Plotly in a Qt WebEngine view, in `μA`) with
  a run log.

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
2. Connect the N1471H over USB and note its COM port.
3. In the *Setup* tab choose backend **CAEN USB-VCP**, select the COM port, leave transport on
   **direct serial**, and **Connect**. The logger-aligned serial defaults are `COMx_9600_8_1_none`.
   If you need to compare against the CAEN wrapper stack, switch to a wrapper transport and leave
   **Model = N1471H / N1471** plus **Current source = Auto** unless you are troubleshooting.

> The hardware backend is exercised on the lab PC (it needs the N1471H + the CAEN HV Wrapper).
> Simulation covers everything else, including CI and development.

## CAEN troubleshooting

- For the actual lab unit, leave **Model = N1471H / N1471** when testing wrapper transports.
- For **N1471H/N1470-style USB devices**, try **Transport = direct serial** first. This backend is
  intended to mirror the older VISA-based control path from the legacy LabVIEW software bundled in
  this repo.
- Install **CAEN HV Wrapper Rel. 5.10 or newer** on the Windows lab PC. N1470/N1471 USB wrapper
  support dates back to Rel. 5.10 (December 2012); **6.x is preferred**.
- If **wrapper auto** fails, force **Transport = caen-libs** and then **Transport = raw wrapper** to
  compare the two wrapper code paths directly.
- For N1470/N1471 USB, this app now mirrors `caen_logger`: it sends **no username/password over USB**.
- Keep the default serial settings unless you are matching a known-working legacy setup:
  `COM_9600_8_1_none` for direct serial, or `COM_9600_8_1_none_0` for wrapper transports.
- On the wrapper path, **Current source = Auto** prefers `IMonL` for N1471H and `IMon` for N1470.
  Force `IMonL`, `IMonH`, or `IMon` only when comparing what the CAEN wrapper reports.
- Repeated USB wrapper failures such as `LOGINFAILED` or CAEN `4100 Connection failed` are a strong
  cue to switch to **direct serial**, not to add username/password fields.
- The **raw wrapper** backend still uses the CAEN **N14xx family** system code (`N1470 = 6`) for both
  N1470 and N1471H. That is expected and is shown explicitly in diagnostics.
- Wrapper failures continue to include the backend attempted and the exact USB-VCP argument string;
  direct serial failures include the exact COM/baud/data/stop/parity settings so the two paths are
  easy to compare.
- Channel status is decoded into readable labels such as `ON`, `Ramp↑`, `OVC`, and `TRIP` rather than
  shown only as raw alarm codes.
- `IMon` is displayed and recorded in **μA** throughout the app and CSV output.

## Where measurements are saved

CSV files are written to a `measurements/` folder next to the executable (or next to `main.py`
when running from source), named `YYYYmmdd_HHMMSS_<mode>.csv`.

## Development

Run the test suite headless:

```bash
cd 2026_Python_version
QT_QPA_PLATFORM=offscreen pytest        # Windows: set QT_QPA_PLATFORM=offscreen && pytest
```
