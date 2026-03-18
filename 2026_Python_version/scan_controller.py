from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping, Sequence

from caen_interface import (
    CHANNEL_LABELS,
    BaseCaenInterface,
    FieldConfig,
    RunPointRecord,
)

VTHGEM1_START_V = 150
VTHGEM1_STOP_V = 1500
VTHGEM1_STEP_V = 25

DRIFT_GAP_CM = 0.5
TRANSFER_GAP_CM = 0.1
MIN_HV_V = 1.0
T1_ANCHOR_V = 1.0
DEFAULT_WAIT_SECONDS = 7.0


@dataclass(frozen=True)
class ScanCallbacks:
    on_subscan_started: Callable[[FieldConfig], None] | None = None
    on_point_recorded: Callable[[RunPointRecord], None] | None = None
    on_channel_refresh: Callable[[object], None] | None = None
    on_status_message: Callable[[str], None] | None = None


@dataclass(frozen=True)
class RunResult:
    success: bool
    aborted: bool
    message: str


class ScanController:
    def __init__(
        self,
        *,
        wait_seconds: float = DEFAULT_WAIT_SECONDS,
        scan_values: Sequence[int] | None = None,
    ) -> None:
        self.wait_seconds = float(wait_seconds)
        self.scan_values = tuple(
            scan_values
            if scan_values is not None
            else range(VTHGEM1_START_V, VTHGEM1_STOP_V + VTHGEM1_STEP_V, VTHGEM1_STEP_V)
        )
        self._recipes = {
            "Reference": (
                FieldConfig("Reference", 0.0, 0.0, False),
            ),
            "Collection scan": (
                FieldConfig("Collection", 0.0, 0.0, True),
            ),
            "Transfer field scan": (
                FieldConfig("Transfer -1 kV/cm", 0.0, -1.0, True),
                FieldConfig("Transfer +1 kV/cm", 0.0, 1.0, True),
                FieldConfig("Transfer +2 kV/cm", 0.0, 2.0, True),
                FieldConfig("Transfer +3 kV/cm", 0.0, 3.0, True),
            ),
            "Drift field scan": (
                FieldConfig("Drift 0.0 kV/cm", 0.0, 1.0, True),
                FieldConfig("Drift 0.2 kV/cm", 0.2, 1.0, True),
                FieldConfig("Drift 0.4 kV/cm", 0.4, 1.0, True),
                FieldConfig("Drift 0.6 kV/cm", 0.6, 1.0, True),
                FieldConfig("Drift 0.8 kV/cm", 0.8, 1.0, True),
                FieldConfig("Drift 1.0 kV/cm", 1.0, 1.0, True),
            ),
        }

    def mode_names(self) -> list[str]:
        return list(self._recipes.keys())

    def field_configs_for_mode(self, mode: str) -> tuple[FieldConfig, ...]:
        return self._recipes[mode]

    def points_per_subscan(self) -> int:
        return len(self.scan_values)

    def solve_configuration(
        self,
        v_thgem1_v: float,
        e_drift_kv_cm: float,
        e_transfer_kv_cm: float,
    ) -> dict[str, float]:
        t1_v = T1_ANCHOR_V
        b1_v = max(MIN_HV_V, v_thgem1_v - t1_v)
        c_v = max(MIN_HV_V, t1_v + e_drift_kv_cm * 1000.0 * DRIFT_GAP_CM)
        t2_v = max(MIN_HV_V, b1_v + e_transfer_kv_cm * 1000.0 * TRANSFER_GAP_CM)
        return {
            "C": c_v,
            "T1": t1_v,
            "B1": b1_v,
            "T2": t2_v,
        }

    def describe_mode(self, mode: str) -> str:
        field_configs = self.field_configs_for_mode(mode)
        fields_summary = ", ".join(
            f"{config.label}: Edrift={config.e_drift_kv_cm:+.1f} kV/cm, "
            f"Etransfer={config.e_transfer_kv_cm:+.1f} kV/cm"
            for config in field_configs
        )
        uv_summary = "OFF" if not any(config.uv_expected for config in field_configs) else "ON"
        return (
            f"Vthgem1 {VTHGEM1_START_V} -> {VTHGEM1_STOP_V} V in {VTHGEM1_STEP_V} V steps | "
            f"wait {self.wait_seconds:.0f} s/point | UV expected {uv_summary} | {fields_summary}"
        )

    def run_recipe(
        self,
        interface: BaseCaenInterface,
        mode: str,
        data_logger,
        callbacks: ScanCallbacks,
        abort_event: threading.Event,
    ) -> RunResult:
        field_configs = self.field_configs_for_mode(mode)
        interface.power_on_channels(CHANNEL_LABELS)

        for field_config in field_configs:
            if callbacks.on_subscan_started is not None:
                callbacks.on_subscan_started(field_config)

            for point_index, v_thgem1_v in enumerate(self.scan_values, start=1):
                if abort_event.is_set():
                    self._handle_abort(interface, callbacks)
                    return RunResult(False, True, "Scan aborted safely.")

                interface.set_measurement_context(field_config, float(v_thgem1_v))
                solved_voltages = self.solve_configuration(
                    float(v_thgem1_v),
                    field_config.e_drift_kv_cm,
                    field_config.e_transfer_kv_cm,
                )
                interface.set_channel_voltages(solved_voltages)

                if not self._wait_for_settle(abort_event):
                    self._handle_abort(interface, callbacks)
                    return RunResult(False, True, "Scan aborted safely.")

                snapshots = interface.read_all_channels()
                if callbacks.on_channel_refresh is not None:
                    callbacks.on_channel_refresh(snapshots)

                timestamp_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
                record = RunPointRecord.from_snapshots(
                    mode=mode,
                    subscan_label=field_config.label,
                    uv_expected=field_config.uv_expected,
                    point_index=point_index,
                    v_thgem1_v=float(v_thgem1_v),
                    e_drift_kv_cm=field_config.e_drift_kv_cm,
                    e_transfer_kv_cm=field_config.e_transfer_kv_cm,
                    timestamp_iso=timestamp_iso,
                    snapshots=snapshots,
                )
                data_logger.write_record(record)
                if callbacks.on_point_recorded is not None:
                    callbacks.on_point_recorded(record)

        return RunResult(True, False, f"{mode} completed.")

    def _wait_for_settle(self, abort_event: threading.Event) -> bool:
        if self.wait_seconds <= 0:
            return not abort_event.is_set()

        deadline = time.monotonic() + self.wait_seconds
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
