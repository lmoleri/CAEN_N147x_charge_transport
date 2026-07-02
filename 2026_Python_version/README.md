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

A single window with four tabs:

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
- **Scan** — pick a **scan program**, which selects *which* quantity is swept; the others are held
  at editable values, and every point is turned into all four electrode voltages (C/T1/B1/T2) so the
  relevant electrode actually moves. The THGEM faces are explicit, settable biases — **T1** (top,
  shown with a `−` prefix) and **B1** (bottom, `+`); their difference is the THGEM voltage
  ΔV = |B1|+|T1|.
    - **THGEM voltage (gain)** — sweeps **B1 (+)** while holding **T1 (−)**; holds the drift and
      induction fields. Plotted against ΔV. Units: V.
    - **Drift field scan** — sweeps **E_drift** (moves the cathode C relative to T1); holds T1, B1
      and the induction field. Units: kV/cm.
    - **Induction field scan** — sweeps **E_induction** / transfer field (moves T2 relative to B1);
      holds T1, B1 and the drift field. Units: kV/cm.

  The sweep start/stop/step relabels and switches units with the program; the held box for whatever
  is swept is greyed out (T1 is always editable). A live **electrode-bias preview** shows the signed
  C/T1/B1/T2 values (held value or start→end range) plus the ΔV range, and **Show electrode bias
  table** expands a per-point table of all four biases. Gaps (drift C↔T1, induction B1↔T2),
  wait/point, and the UV-lamp flag are editable. Start/Abort, with a run log; each run is written to
  a CSV under `measurements/`. The scan **never switches HV on by itself** — power on the channels you
  want first (Channels tab); it runs using only the channels that are ON. If some are OFF a
  confirmation appears (those channels are left untouched); if none are ON, Start is blocked. When a
  scan finishes (or is aborted), the scanned channels **ramp back down to 1 V and stay ON** (they are
  never switched off automatically).
- **Viewer** — a Plotly current viewer (in `μA`) whose **x-axis follows the swept variable** (THGEM
  voltage, drift field, or induction field). **Every scan opens its own tab** and follows it **live**
  as points are taken; tabs stay open so you can compare runs. Each point carries an **error bar** —
  the standard deviation of the repeated current readings taken at that point (the measurement noise).
  **Load CSV…** opens a new tab for a saved run (select several files of the *same* program to
  **overlay** them, auto-legended by their held quantities). The channel toggles (C/T1/B1/T2) and
  **Save plot…** act on the current tab, and **Close tab** removes it. **Save plot…** exports the
  current plot as a **PNG image** or a self-contained **interactive HTML** file. Plotting is decoupled
  from acquisition (the scan just writes CSV), mirroring
  [CAEN-Plotly-Viewer-From-Log](https://github.com/weizmann-atlas/CAEN-Plotly-Viewer-From-Log).

## System requirements

- **OS:** Windows 10 or 11 (64-bit) for the packaged `.exe`. Running from source also works on
  macOS/Linux (the Simulation backend needs no hardware).
- **Display:** a real desktop session. The Viewer embeds a Chromium-based web view (QtWebEngine) that
  renders in **software** by default (the app sets `--disable-gpu`), so **no dedicated GPU is needed** —
  but it will not render on a headless/offscreen machine.
- **First launch:** antivirus/firewall may prompt once — the Viewer loads its plot from a `127.0.0.1`
  loopback server (the connection never leaves the machine); allow it. Unzip the bundle to a writable
  folder (Desktop/Documents, not `Program Files`).
- **Building from source:** Python 3.10+ (see [Run from source](#run-from-source) and
  [Build the Windows bundle yourself](#build-the-windows-bundle-yourself)).
- **Real hardware:** the CAEN HV Wrapper library on the Windows lab PC (see
  [Using real CAEN hardware](#using-real-caen-hardware)); Simulation needs nothing extra.
- No image-export dependency is required — **Save plot…** renders the PNG with the web view's own
  Plotly (client-side), so nothing like `kaleido` is bundled.

> Maintenance note: PyQtWebEngine 5.15 ships an old Chromium (~87). Any JavaScript inlined into the
> plot (plotly.js) must avoid or **polyfill** newer JS features (e.g. `Array.prototype.at`, Chromium 92),
> or the embedded plot silently renders blank. The Viewer injects such a polyfill before plotly.js.

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
