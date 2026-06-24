from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Sequence

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

DRIFT_GAP_CM = 0.5
INDUCTION_GAP_CM = 0.1  # gap between THGEM1 bottom (B1) and the top electrode (T2)
MIN_HV_V = 1.0
T1_ANCHOR_V = 1.0
DEFAULT_WAIT_SECONDS = 7.0


@dataclass(frozen=True)
class ScanParameters:
    """A single-curve scan: sweep V_THGEM1 while holding the drift and induction
    fields (and their gaps) constant.

    Geometry:  C ──drift gap── T1 ─[THGEM1]─ B1 ──induction gap── T2
      V_THGEM1   = V_B1 − V_T1                (the swept multiplication voltage)
      E_drift     = (V_C − V_T1) / drift_gap          (held constant)
      E_induction = (V_T2 − V_B1) / induction_gap     (held constant)
    """

    label: str = "Custom"
    vthgem1_start_v: float = VTHGEM1_START_V
    vthgem1_stop_v: float = VTHGEM1_STOP_V
    vthgem1_step_v: float = VTHGEM1_STEP_V
    drift_gap_cm: float = DRIFT_GAP_CM
    drift_field_kv_cm: float = 0.0
    induction_gap_cm: float = INDUCTION_GAP_CM
    induction_field_kv_cm: float = 1.0
    wait_seconds: float = DEFAULT_WAIT_SECONDS
    uv_expected: bool = True

    def scan_values(self) -> tuple[float, ...]:
        start, stop = float(self.vthgem1_start_v), float(self.vthgem1_stop_v)
        step = abs(float(self.vthgem1_step_v))
        if step <= 0:
            return (start,) if start == stop else (start, stop)
        direction = 1.0 if stop >= start else -1.0
        count = int(math.floor(abs(stop - start) / step + 1e-9)) + 1
        return tuple(round(start + direction * step * i, 6) for i in range(count))

    def solve(self, v_thgem1_v: float) -> dict[str, float]:
        t1_v = T1_ANCHOR_V
        b1_v = max(MIN_HV_V, v_thgem1_v - t1_v)
        c_v = max(MIN_HV_V, t1_v + self.drift_field_kv_cm * 1000.0 * self.drift_gap_cm)
        t2_v = max(MIN_HV_V, b1_v + self.induction_field_kv_cm * 1000.0 * self.induction_gap_cm)
        return {"C": c_v, "T1": t1_v, "B1": b1_v, "T2": t2_v}

    def field_config(self) -> FieldConfig:
        # The Simulation backend reads e_drift/e_transfer to model the current.
        return FieldConfig(self.label, self.drift_field_kv_cm, self.induction_field_kv_cm, self.uv_expected)

    def legend_label(self) -> str:
        return f"Ed={self.drift_field_kv_cm:g}, Ei={self.induction_field_kv_cm:g} kV/cm"

    def describe(self) -> str:
        return (
            f"V_THGEM1 {self.vthgem1_start_v:g} → {self.vthgem1_stop_v:g} V "
            f"step {self.vthgem1_step_v:g} V ({len(self.scan_values())} pts) | "
            f"E_drift {self.drift_field_kv_cm:g} kV/cm (gap {self.drift_gap_cm:g} cm) | "
            f"E_induction {self.induction_field_kv_cm:g} kV/cm (gap {self.induction_gap_cm:g} cm) | "
            f"wait {self.wait_seconds:g} s/pt | UV {'ON' if self.uv_expected else 'OFF'}"
        )


# Named presets pre-fill the editable Scan-tab fields. Each is a single curve;
# overlay several with the plot's "persist" toggle to build a family.
PRESETS: dict[str, ScanParameters] = {
    "Reference": ScanParameters(label="Reference", drift_field_kv_cm=0.0, induction_field_kv_cm=0.0, uv_expected=False),
    "Collection scan": ScanParameters(label="Collection", drift_field_kv_cm=0.0, induction_field_kv_cm=1.0),
    "Transfer field scan": ScanParameters(label="Transfer", drift_field_kv_cm=0.0, induction_field_kv_cm=1.0),
    "Drift field scan": ScanParameters(label="Drift", drift_field_kv_cm=0.0, induction_field_kv_cm=1.0),
}


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
        field_config = params.field_config()
        interface.power_on_channels(CHANNEL_LABELS)
        if callbacks.on_scan_started is not None:
            callbacks.on_scan_started(params)

        for point_index, v_thgem1_v in enumerate(params.scan_values(), start=1):
            if abort_event.is_set():
                self._handle_abort(interface, callbacks)
                return RunResult(False, True, "Scan aborted safely.")

            interface.set_measurement_context(field_config, float(v_thgem1_v))
            interface.set_channel_voltages(params.solve(float(v_thgem1_v)))

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
                uv_expected=params.uv_expected,
                point_index=point_index,
                v_thgem1_v=float(v_thgem1_v),
                e_drift_kv_cm=params.drift_field_kv_cm,
                e_transfer_kv_cm=params.induction_field_kv_cm,
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
