from __future__ import annotations

import math
import statistics
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable

from caen_interface import (
    CHANNEL_BY_LABEL,
    BaseCaenInterface,
    FieldConfig,
    RunPointRecord,
)

# Defaults (also used to seed the editable Scan-tab fields).
VTHGEM1_START_V = 150.0   # gain-scan B1 (+) sweep range, in V
VTHGEM1_STOP_V = 1500.0
VTHGEM1_STEP_V = 25.0
T1_HELD_DEFAULT_V = 300.0  # THGEM top face T1 (negative polarity) — held magnitude
B1_HELD_DEFAULT_V = 400.0  # THGEM bottom face B1 (positive polarity) — held magnitude

DRIFT_GAP_CM = 0.5
INDUCTION_GAP_CM = 0.1  # gap between THGEM1 bottom (B1) and the top electrode (T2)
MIN_HV_V = 1.0
DEFAULT_WAIT_SECONDS = 7.0

# Each scan point reads the currents this many times; the recorded IMon is the mean
# and the error bar is the standard deviation across the reads (the measurement noise).
READS_PER_POINT = 5


def _aggregate_imon(reads: list) -> "tuple[dict[str, float], dict[str, float]]":
    """Per-channel mean and sample standard deviation of IMon across several reads of
    the same scan point. The mean is the plotted value; the std is its error bar."""
    by_label: dict[str, list[float]] = {}
    for snapshots in reads:
        for snapshot in snapshots:
            by_label.setdefault(snapshot.label, []).append(snapshot.imon_ua)
    means: dict[str, float] = {}
    errs: dict[str, float] = {}
    for label, samples in by_label.items():
        means[label] = statistics.fmean(samples)
        errs[label] = statistics.stdev(samples) if len(samples) > 1 else 0.0
    return means, errs


class ScanVariable(Enum):
    """The physical quantity a scan program sweeps."""

    THGEM_VOLTAGE = "thgem_voltage"
    DRIFT_FIELD = "drift_field"
    INDUCTION_FIELD = "induction_field"


@dataclass(frozen=True)
class VariableSpec:
    symbol: str         # e.g. "V_THGEM1"
    unit: str           # "V" or "kV/cm"
    record_column: str  # the RunPointRecord/CSV column that carries the swept value
    axis_title: str     # Plotly x-axis title


SCAN_VARIABLE_SPECS: dict[ScanVariable, VariableSpec] = {
    ScanVariable.THGEM_VOLTAGE: VariableSpec("B1", "V", "v_thgem1_v", "THGEM1 voltage [V]"),
    ScanVariable.DRIFT_FIELD: VariableSpec("E_drift", "kV/cm", "e_drift_kv_cm", "Drift field [kV/cm]"),
    ScanVariable.INDUCTION_FIELD: VariableSpec("E_induction", "kV/cm", "e_transfer_kv_cm", "Induction field [kV/cm]"),
}


def variable_spec_for(value: str | ScanVariable) -> VariableSpec:
    """Resolve a spec from a ScanVariable or its stored string value.

    Falls back to the THGEM-voltage spec for unknown/legacy values, so the Viewer
    can still open CSVs written before the ``scan_variable`` column existed.
    """
    if isinstance(value, ScanVariable):
        return SCAN_VARIABLE_SPECS[value]
    try:
        return SCAN_VARIABLE_SPECS[ScanVariable(value)]
    except ValueError:
        return SCAN_VARIABLE_SPECS[ScanVariable.THGEM_VOLTAGE]


@dataclass(frozen=True)
class ScanParameters:
    """A single-curve scan that sweeps one quantity while holding the others.

    Geometry:  C ──drift gap── T1 ─[THGEM1]─ B1 ──induction gap── T2
    The THGEM faces are explicit, settable biases (magnitudes): **T1** (negative
    polarity) and **B1** (positive polarity). Derived quantities:
      ΔV_THGEM    = V_B1 − V_T1 = |B1| + |T1|          (THGEM multiplication voltage)
      E_drift     = relates the cathode C to T1 / drift_gap
      E_induction = relates the top electrode T2 to B1 / induction_gap

    ``scan_variable`` selects what is swept over ``start → stop`` in ``step``
    increments: the **gain** program sweeps **B1** (holding T1); the field programs
    sweep their field. The other quantities are held at the values below. T1 is
    always held. :meth:`solve` turns each point into all four electrode magnitudes.
    """

    label: str = "THGEM voltage"
    scan_variable: ScanVariable = ScanVariable.THGEM_VOLTAGE
    start: float = VTHGEM1_START_V
    stop: float = VTHGEM1_STOP_V
    step: float = VTHGEM1_STEP_V
    t1_v: float = T1_HELD_DEFAULT_V             # THGEM top (−) magnitude, always held
    b1_v: float = B1_HELD_DEFAULT_V             # THGEM bottom (+) magnitude, held unless swept (gain)
    drift_field_kv_cm: float = 0.0              # held when not the swept variable
    induction_field_kv_cm: float = 1.0          # held when not the swept variable
    drift_gap_cm: float = DRIFT_GAP_CM
    induction_gap_cm: float = INDUCTION_GAP_CM
    wait_seconds: float = DEFAULT_WAIT_SECONDS
    uv_expected: bool = True

    @property
    def spec(self) -> VariableSpec:
        return SCAN_VARIABLE_SPECS[self.scan_variable]

    def unit(self) -> str:
        return self.spec.unit

    def axis_title(self) -> str:
        return self.spec.axis_title

    def scan_values(self) -> tuple[float, ...]:
        start, stop = float(self.start), float(self.stop)
        step = abs(float(self.step))
        if step <= 0:
            return (start,) if start == stop else (start, stop)
        direction = 1.0 if stop >= start else -1.0
        count = int(math.floor(abs(stop - start) / step + 1e-9)) + 1
        return tuple(round(start + direction * step * i, 6) for i in range(count))

    def point_state(self, swept_value: float) -> tuple[float, float, float, float]:
        """Return (T1, B1, E_drift, E_induction) at a swept value: the held values
        with the swept variable overridden. The gain program sweeps B1 (the positive
        THGEM face); the field programs sweep their field. T1 is always held."""
        t1 = self.t1_v
        b1 = self.b1_v
        e_drift = self.drift_field_kv_cm
        e_induction = self.induction_field_kv_cm
        if self.scan_variable is ScanVariable.THGEM_VOLTAGE:
            b1 = swept_value
        elif self.scan_variable is ScanVariable.DRIFT_FIELD:
            e_drift = swept_value
        else:
            e_induction = swept_value
        return t1, b1, e_drift, e_induction

    def solve(self, swept_value: float) -> dict[str, float]:
        """Electrode magnitudes (V). Polarity is applied elsewhere: C/T1 are negative,
        B1/T2 positive. C tracks E_drift off T1; T2 tracks E_induction off B1."""
        t1, b1, e_drift, e_induction = self.point_state(swept_value)
        t1_v = max(MIN_HV_V, t1)
        b1_v = max(MIN_HV_V, b1)
        c_v = max(MIN_HV_V, t1 + e_drift * 1000.0 * self.drift_gap_cm)
        t2_v = max(MIN_HV_V, b1 + e_induction * 1000.0 * self.induction_gap_cm)
        return {"C": c_v, "T1": t1_v, "B1": b1_v, "T2": t2_v}

    def signed_solve(self, swept_value: float) -> dict[str, float]:
        """:meth:`solve` with each magnitude given its electrode's polarity sign."""
        return {
            label: (mag if CHANNEL_BY_LABEL[label].polarity == "+" else -mag)
            for label, mag in self.solve(swept_value).items()
        }

    def thgem_voltage_at(self, swept_value: float) -> float:
        """ΔV across the THGEM = V_B1 − V_T1 = |B1| + |T1| (applied magnitudes)."""
        solved = self.solve(swept_value)
        return solved["B1"] + solved["T1"]

    def field_config_at(self, swept_value: float) -> FieldConfig:
        # The Simulation backend reads e_drift/e_transfer to model the current.
        _, _, e_drift, e_induction = self.point_state(swept_value)
        return FieldConfig(self.label, e_drift, e_induction, self.uv_expected)

    def legend_label(self) -> str:
        """Distinguish curves within a same-program overlay by the held quantities."""
        delta_v = self.b1_v + self.t1_v
        if self.scan_variable is ScanVariable.THGEM_VOLTAGE:
            return f"Ed={self.drift_field_kv_cm:g}, Ei={self.induction_field_kv_cm:g} kV/cm"
        if self.scan_variable is ScanVariable.DRIFT_FIELD:
            return f"V_THGEM1={delta_v:g} V, Ei={self.induction_field_kv_cm:g} kV/cm"
        return f"V_THGEM1={delta_v:g} V, Ed={self.drift_field_kv_cm:g} kV/cm"

    def describe(self) -> str:
        spec = self.spec
        parts = [
            f"{spec.symbol} {self.start:g} → {self.stop:g} {spec.unit} "
            f"step {self.step:g} {spec.unit} ({len(self.scan_values())} pts)"
        ]
        parts.append(f"T1 −{self.t1_v:g} V (held)")
        if self.scan_variable is not ScanVariable.THGEM_VOLTAGE:
            parts.append(f"B1 +{self.b1_v:g} V (held) → ΔV {self.b1_v + self.t1_v:g} V")
        if self.scan_variable is not ScanVariable.DRIFT_FIELD:
            parts.append(f"E_drift {self.drift_field_kv_cm:g} kV/cm (gap {self.drift_gap_cm:g} cm)")
        if self.scan_variable is not ScanVariable.INDUCTION_FIELD:
            parts.append(f"E_induction {self.induction_field_kv_cm:g} kV/cm (gap {self.induction_gap_cm:g} cm)")
        parts.append(f"wait {self.wait_seconds:g} s/pt | UV {'ON' if self.uv_expected else 'OFF'}")
        return " | ".join(parts)


# Named programs pre-fill the editable Scan-tab fields. Each sweeps its own
# quantity; overlay several with the plot's "persist" toggle to build a family.
PRESETS: dict[str, ScanParameters] = {
    "THGEM voltage (gain)": ScanParameters(
        label="THGEM voltage",
        scan_variable=ScanVariable.THGEM_VOLTAGE,
        start=VTHGEM1_START_V, stop=VTHGEM1_STOP_V, step=VTHGEM1_STEP_V,
        t1_v=T1_HELD_DEFAULT_V, b1_v=B1_HELD_DEFAULT_V,
        drift_field_kv_cm=0.0, induction_field_kv_cm=1.0,
    ),
    "Drift field scan": ScanParameters(
        label="Drift field",
        scan_variable=ScanVariable.DRIFT_FIELD,
        start=0.0, stop=2.0, step=0.1,
        t1_v=T1_HELD_DEFAULT_V, b1_v=B1_HELD_DEFAULT_V,
        drift_field_kv_cm=0.0, induction_field_kv_cm=1.0,
    ),
    "Induction field scan": ScanParameters(
        label="Induction field",
        scan_variable=ScanVariable.INDUCTION_FIELD,
        start=0.0, stop=4.0, step=0.2,
        t1_v=T1_HELD_DEFAULT_V, b1_v=B1_HELD_DEFAULT_V,
        drift_field_kv_cm=0.5, induction_field_kv_cm=0.0,
    ),
}

DEFAULT_PRESET = "THGEM voltage (gain)"


@dataclass(frozen=True)
class ScanCallbacks:
    on_scan_started: Callable[[ScanParameters], None] | None = None
    on_point_recorded: Callable[[RunPointRecord], None] | None = None
    on_channel_refresh: Callable[[object], None] | None = None
    on_status_message: Callable[[str], None] | None = None


@dataclass(frozen=True)
class RunResult:
    success: bool
    aborted: bool
    message: str


class ScanController:
    def preset_names(self) -> list[str]:
        return list(PRESETS)

    def preset(self, name: str) -> ScanParameters:
        return PRESETS[name]

    def run_scan(
        self,
        interface: BaseCaenInterface,
        params: ScanParameters,
        data_logger,
        callbacks: ScanCallbacks,
        abort_event: threading.Event,
    ) -> RunResult:
        # The scan never powers HV on: it drives only the channels already ON and
        # leaves the rest untouched (the GUI confirms/blocks before we get here).
        active = [snapshot.label for snapshot in interface.read_all_channels() if snapshot.is_on]
        if not active:
            return RunResult(False, False, "No HV channels are ON; nothing to scan.")
        if callbacks.on_status_message is not None:
            callbacks.on_status_message(f"Scanning channels that are ON: {', '.join(active)}.")
        if callbacks.on_scan_started is not None:
            callbacks.on_scan_started(params)

        for point_index, swept_value in enumerate(params.scan_values(), start=1):
            if abort_event.is_set():
                self._handle_abort(interface, callbacks, active)
                return RunResult(False, True, "Scan aborted safely.")

            _, _, e_drift, e_induction = params.point_state(float(swept_value))
            v_thgem1_v = params.thgem_voltage_at(float(swept_value))
            interface.set_measurement_context(params.field_config_at(float(swept_value)), v_thgem1_v)
            solved = params.solve(float(swept_value))
            interface.set_channel_voltages({label: solved[label] for label in active})

            if not self._wait_for_settle(params.wait_seconds, abort_event):
                self._handle_abort(interface, callbacks, active)
                return RunResult(False, True, "Scan aborted safely.")

            # Read the point several times: the mean is the recorded IMon and the
            # spread (std) becomes the error bar. The last read supplies VMon/status.
            reads = [interface.read_all_channels() for _ in range(READS_PER_POINT)]
            snapshots = reads[-1]
            imon_mean, imon_err = _aggregate_imon(reads)
            if callbacks.on_channel_refresh is not None:
                callbacks.on_channel_refresh(snapshots)

            timestamp_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            record = RunPointRecord.from_snapshots(
                mode=params.label,
                subscan_label=params.label,
                scan_variable=params.scan_variable.value,
                uv_expected=params.uv_expected,
                point_index=point_index,
                v_thgem1_v=v_thgem1_v,
                e_drift_kv_cm=e_drift,
                e_transfer_kv_cm=e_induction,
                timestamp_iso=timestamp_iso,
                snapshots=snapshots,
                imon_by_label=imon_mean,
                imon_err_by_label=imon_err,
            )
            data_logger.write_record(record)
            if callbacks.on_point_recorded is not None:
                callbacks.on_point_recorded(record)

        self._park_channels(
            interface, callbacks, active, power_off=False,
            lead_message="Scan complete. Returning scanned channels to 1 V.",
            done_message="Scan complete. Channels parked at 1 V.",
        )
        return RunResult(True, False, f"{params.label} scan completed.")

    def _wait_for_settle(self, wait_seconds: float, abort_event: threading.Event) -> bool:
        if wait_seconds <= 0:
            return not abort_event.is_set()

        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            if abort_event.is_set():
                return False
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        return not abort_event.is_set()

    def _handle_abort(self, interface: BaseCaenInterface, callbacks: ScanCallbacks, active: list[str]) -> None:
        # Abort brings channels back to 1 V but leaves them ON (like a normal finish).
        self._park_channels(
            interface, callbacks, active, power_off=False,
            lead_message="Abort requested. Returning scanned channels to 1 V.",
            done_message="Abort complete. Channels parked at 1 V.",
        )

    def _park_channels(
        self,
        interface: BaseCaenInterface,
        callbacks: ScanCallbacks,
        active: list[str],
        *,
        power_off: bool,
        lead_message: str,
        done_message: str,
    ) -> None:
        """Ramp the given channels down to 1 V (and optionally power them off),
        refreshing monitors until they settle. Shared by normal completion and abort."""
        if callbacks.on_status_message is not None:
            callbacks.on_status_message(lead_message)
        interface.safe_shutdown(active)

        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            snapshots = interface.read_all_channels()
            if callbacks.on_channel_refresh is not None:
                callbacks.on_channel_refresh(snapshots)
            if all(abs(snapshot.vmon_v) <= 5.0 for snapshot in snapshots):
                break
            time.sleep(0.25)

        if power_off:
            interface.power_off_channels(active)
        snapshots = interface.read_all_channels()
        if callbacks.on_channel_refresh is not None:
            callbacks.on_channel_refresh(snapshots)
        if callbacks.on_status_message is not None:
            callbacks.on_status_message(done_message)
