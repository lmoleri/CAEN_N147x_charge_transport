from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable

from caen_interface import (
    CHANNEL_LABELS,
    BaseCaenInterface,
    FieldConfig,
    RunPointRecord,
)

# Defaults (also used to seed the editable Scan-tab fields).
VTHGEM1_START_V = 150.0
VTHGEM1_STOP_V = 1500.0
VTHGEM1_STEP_V = 25.0
VTHGEM1_HOLD_V = 700.0  # operating THGEM voltage held during a field scan

DRIFT_GAP_CM = 0.5
INDUCTION_GAP_CM = 0.1  # gap between THGEM1 bottom (B1) and the top electrode (T2)
MIN_HV_V = 1.0
T1_ANCHOR_V = 1.0
DEFAULT_WAIT_SECONDS = 7.0


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
    ScanVariable.THGEM_VOLTAGE: VariableSpec("V_THGEM1", "V", "v_thgem1_v", "THGEM1 voltage [V]"),
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
    """A single-curve scan that sweeps one quantity while holding the other two.

    Geometry:  C ──drift gap── T1 ─[THGEM1]─ B1 ──induction gap── T2
      V_THGEM1    = V_B1 − V_T1                        (THGEM multiplication voltage)
      E_drift     = (V_C  − V_T1) / drift_gap          (cathode ↔ THGEM top)
      E_induction = (V_T2 − V_B1) / induction_gap      (THGEM bottom ↔ top electrode)

    ``scan_variable`` selects which of the three is swept over ``start → stop`` in
    ``step`` increments; the other two are held at the values below. Every point is
    turned into all four electrode voltages by :meth:`solve`, so e.g. a drift scan
    moves only the cathode (C) while V_THGEM1 and the induction electrode hold.
    """

    label: str = "THGEM voltage"
    scan_variable: ScanVariable = ScanVariable.THGEM_VOLTAGE
    start: float = VTHGEM1_START_V
    stop: float = VTHGEM1_STOP_V
    step: float = VTHGEM1_STEP_V
    v_thgem1_v: float = VTHGEM1_HOLD_V          # held when sweeping a field
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

    def point_fields(self, swept_value: float) -> tuple[float, float, float]:
        """Return (V_THGEM1, E_drift, E_induction) at a swept value: the held trio
        with the swept variable overridden."""
        v_thgem1 = self.v_thgem1_v
        e_drift = self.drift_field_kv_cm
        e_induction = self.induction_field_kv_cm
        if self.scan_variable is ScanVariable.THGEM_VOLTAGE:
            v_thgem1 = swept_value
        elif self.scan_variable is ScanVariable.DRIFT_FIELD:
            e_drift = swept_value
        else:
            e_induction = swept_value
        return v_thgem1, e_drift, e_induction

    def solve(self, swept_value: float) -> dict[str, float]:
        v_thgem1, e_drift, e_induction = self.point_fields(swept_value)
        t1_v = T1_ANCHOR_V
        b1_v = max(MIN_HV_V, v_thgem1 - t1_v)
        c_v = max(MIN_HV_V, t1_v + e_drift * 1000.0 * self.drift_gap_cm)
        t2_v = max(MIN_HV_V, b1_v + e_induction * 1000.0 * self.induction_gap_cm)
        return {"C": c_v, "T1": t1_v, "B1": b1_v, "T2": t2_v}

    def field_config_at(self, swept_value: float) -> FieldConfig:
        # The Simulation backend reads e_drift/e_transfer to model the current.
        _, e_drift, e_induction = self.point_fields(swept_value)
        return FieldConfig(self.label, e_drift, e_induction, self.uv_expected)

    def legend_label(self) -> str:
        """Distinguish curves within a same-program overlay by the held quantities."""
        if self.scan_variable is ScanVariable.THGEM_VOLTAGE:
            return f"Ed={self.drift_field_kv_cm:g}, Ei={self.induction_field_kv_cm:g} kV/cm"
        if self.scan_variable is ScanVariable.DRIFT_FIELD:
            return f"V_THGEM1={self.v_thgem1_v:g} V, Ei={self.induction_field_kv_cm:g} kV/cm"
        return f"V_THGEM1={self.v_thgem1_v:g} V, Ed={self.drift_field_kv_cm:g} kV/cm"

    def describe(self) -> str:
        spec = self.spec
        parts = [
            f"{spec.symbol} {self.start:g} → {self.stop:g} {spec.unit} "
            f"step {self.step:g} {spec.unit} ({len(self.scan_values())} pts)"
        ]
        if self.scan_variable is not ScanVariable.THGEM_VOLTAGE:
            parts.append(f"V_THGEM1 {self.v_thgem1_v:g} V (held)")
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
        v_thgem1_v=VTHGEM1_HOLD_V, drift_field_kv_cm=0.0, induction_field_kv_cm=1.0,
    ),
    "Drift field scan": ScanParameters(
        label="Drift field",
        scan_variable=ScanVariable.DRIFT_FIELD,
        start=0.0, stop=2.0, step=0.1,
        v_thgem1_v=VTHGEM1_HOLD_V, drift_field_kv_cm=0.0, induction_field_kv_cm=1.0,
    ),
    "Induction field scan": ScanParameters(
        label="Induction field",
        scan_variable=ScanVariable.INDUCTION_FIELD,
        start=0.0, stop=4.0, step=0.2,
        v_thgem1_v=VTHGEM1_HOLD_V, drift_field_kv_cm=0.5, induction_field_kv_cm=0.0,
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
        interface.power_on_channels(CHANNEL_LABELS)
        if callbacks.on_scan_started is not None:
            callbacks.on_scan_started(params)

        for point_index, swept_value in enumerate(params.scan_values(), start=1):
            if abort_event.is_set():
                self._handle_abort(interface, callbacks)
                return RunResult(False, True, "Scan aborted safely.")

            v_thgem1_v, e_drift, e_induction = params.point_fields(float(swept_value))
            interface.set_measurement_context(params.field_config_at(float(swept_value)), v_thgem1_v)
            interface.set_channel_voltages(params.solve(float(swept_value)))

            if not self._wait_for_settle(params.wait_seconds, abort_event):
                self._handle_abort(interface, callbacks)
                return RunResult(False, True, "Scan aborted safely.")

            snapshots = interface.read_all_channels()
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
            )
            data_logger.write_record(record)
            if callbacks.on_point_recorded is not None:
                callbacks.on_point_recorded(record)

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

    def _handle_abort(self, interface: BaseCaenInterface, callbacks: ScanCallbacks) -> None:
        if callbacks.on_status_message is not None:
            callbacks.on_status_message("Abort requested. Returning all channels to 1 V.")
        interface.safe_shutdown(CHANNEL_LABELS)

        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            snapshots = interface.read_all_channels()
            if callbacks.on_channel_refresh is not None:
                callbacks.on_channel_refresh(snapshots)
            if all(abs(snapshot.vmon_v) <= 5.0 for snapshot in snapshots):
                break
            time.sleep(0.25)

        interface.power_off_channels(CHANNEL_LABELS)
        snapshots = interface.read_all_channels()
        if callbacks.on_channel_refresh is not None:
            callbacks.on_channel_refresh(snapshots)
        if callbacks.on_status_message is not None:
            callbacks.on_status_message("Abort sequence complete. Channels are OFF.")
