from __future__ import annotations

import csv
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from caen_interface import CHANNEL_LABELS, SimulationInterface
from data_logger import DataLogger
from scan_controller import ScanCallbacks, ScanController, ScanParameters, ScanVariable


class ScanExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.output_dir = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _run(self, params: ScanParameters) -> tuple[int, list]:
        backend = SimulationInterface(seed=7)
        backend.connect()
        backend.set_ramp_rates(300.0, 300.0)
        backend.power_on_channels(CHANNEL_LABELS)  # the scan drives only ON channels

        controller = ScanController()
        logger = DataLogger(self.output_dir)
        csv_path = logger.open_run(params.label)
        records: list = []
        result = controller.run_scan(
            backend, params, logger, ScanCallbacks(on_point_recorded=records.append), threading.Event()
        )
        logger.close()

        self.assertTrue(result.success)
        self.assertFalse(result.aborted)
        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            row_count = sum(1 for _ in csv.DictReader(handle))
        return row_count, records

    def test_thgem_scan_row_count_matches_scan_values(self) -> None:
        params = ScanParameters(label="THGEM", scan_variable=ScanVariable.THGEM_VOLTAGE, wait_seconds=0.0)
        row_count, records = self._run(params)  # 150→1500 step 25 = 55 pts
        self.assertEqual(row_count, 55)
        self.assertEqual(len(records), 55)
        self.assertTrue(all(record.scan_variable == "thgem_voltage" for record in records))
        self.assertTrue(all(record.subscan_label == "THGEM" for record in records))

    def test_custom_range_changes_point_count(self) -> None:
        params = ScanParameters(
            label="Short", scan_variable=ScanVariable.THGEM_VOLTAGE,
            start=200, stop=300, step=50, t1_v=50.0, wait_seconds=0.0,
        )
        row_count, records = self._run(params)
        self.assertEqual(row_count, 3)  # B1 = 200, 250, 300
        # recorded v_thgem1_v = ΔV = B1 + T1(50)
        self.assertEqual(records[0].v_thgem1_v, 250.0)
        self.assertEqual(records[-1].v_thgem1_v, 350.0)

    def test_drift_scan_sweeps_field_and_holds_thgem_voltage(self) -> None:
        params = ScanParameters(
            label="Drift", scan_variable=ScanVariable.DRIFT_FIELD,
            start=0.0, stop=1.0, step=0.5, t1_v=300.0, b1_v=400.0, induction_field_kv_cm=1.0, wait_seconds=0.0,
        )
        row_count, records = self._run(params)
        self.assertEqual(row_count, 3)  # 0.0, 0.5, 1.0
        self.assertEqual([round(r.e_drift_kv_cm, 3) for r in records], [0.0, 0.5, 1.0])
        self.assertTrue(all(r.v_thgem1_v == 700.0 for r in records))  # ΔV = B1+T1 held
        self.assertTrue(all(r.e_transfer_kv_cm == 1.0 for r in records))  # induction held
        self.assertTrue(all(r.scan_variable == "drift_field" for r in records))

    def test_induction_scan_records_carry_swept_field(self) -> None:
        params = ScanParameters(
            label="Induction", scan_variable=ScanVariable.INDUCTION_FIELD,
            start=0.0, stop=2.0, step=1.0, t1_v=300.0, b1_v=400.0, drift_field_kv_cm=0.5, wait_seconds=0.0,
        )
        _, records = self._run(params)
        self.assertEqual([round(r.e_transfer_kv_cm, 3) for r in records], [0.0, 1.0, 2.0])
        self.assertTrue(all(r.e_drift_kv_cm == 0.5 for r in records))  # drift held
        self.assertTrue(all(r.scan_variable == "induction_field" for r in records))

    def test_abort_stops_scan_and_parks_channels_at_1v_left_on(self) -> None:
        backend = SimulationInterface(seed=11)
        backend.connect()
        backend.set_ramp_rates(300.0, 300.0)
        backend.power_on_channels(CHANNEL_LABELS)

        controller = ScanController()
        logger = DataLogger(self.output_dir)
        params = ScanParameters(label="THGEM", scan_variable=ScanVariable.THGEM_VOLTAGE, wait_seconds=0.2)
        csv_path = logger.open_run(params.label)
        abort_event = threading.Event()
        results: list = []

        def run() -> None:
            results.append(controller.run_scan(backend, params, logger, ScanCallbacks(), abort_event))

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        time.sleep(0.1)
        abort_event.set()
        thread.join(timeout=5.0)
        logger.close()

        self.assertTrue(results)
        self.assertTrue(results[0].aborted)

        # Abort ramps to 1 V but leaves channels ON (like a normal finish).
        snapshots = backend.read_all_channels()
        self.assertTrue(all(snapshot.is_on for snapshot in snapshots))
        self.assertTrue(all(abs(backend._channel_state[label]["voltage_v"] - 1.0) < 1e-9 for label in CHANNEL_LABELS))

        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            row_count = sum(1 for _ in csv.DictReader(handle))
        self.assertLess(row_count, 55)

    def test_scan_blocked_when_no_channels_are_on(self) -> None:
        backend = SimulationInterface(seed=7)
        backend.connect()
        backend.power_off_channels(CHANNEL_LABELS)  # nothing energized

        controller = ScanController()
        logger = DataLogger(self.output_dir)
        params = ScanParameters(label="THGEM", scan_variable=ScanVariable.THGEM_VOLTAGE, wait_seconds=0.0)
        csv_path = logger.open_run(params.label)
        result = controller.run_scan(backend, params, logger, ScanCallbacks(), threading.Event())
        logger.close()

        self.assertFalse(result.success)
        self.assertFalse(result.aborted)
        self.assertIn("nothing to scan", result.message)
        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            self.assertEqual(sum(1 for _ in csv.DictReader(handle)), 0)  # no rows written

    def test_scan_drives_only_the_powered_channels(self) -> None:
        backend = SimulationInterface(seed=7)
        backend.connect()
        backend.set_ramp_rates(300.0, 300.0)
        backend.power_on_channels(["B1", "T2"])  # leave C and T1 OFF

        driven: set = set()
        original = backend.set_channel_voltages

        def spy(mapping):
            driven.update(mapping.keys())
            original(mapping)

        backend.set_channel_voltages = spy

        controller = ScanController()
        logger = DataLogger(self.output_dir)
        params = ScanParameters(
            label="THGEM", scan_variable=ScanVariable.THGEM_VOLTAGE,
            start=200, stop=300, step=50, t1_v=300.0, b1_v=400.0, wait_seconds=0.0,
        )
        csv_path = logger.open_run(params.label)
        result = controller.run_scan(backend, params, logger, ScanCallbacks(), threading.Event())
        logger.close()

        self.assertTrue(result.success)
        self.assertEqual(driven, {"B1", "T2"})  # only the powered channels were ever driven
        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            self.assertEqual(sum(1 for _ in csv.DictReader(handle)), 3)

    def test_scan_parks_channels_at_1v_on_completion(self) -> None:
        backend = SimulationInterface(seed=7)
        backend.connect()
        backend.set_ramp_rates(300.0, 300.0)
        backend.power_on_channels(CHANNEL_LABELS)

        controller = ScanController()
        logger = DataLogger(self.output_dir)
        params = ScanParameters(
            label="THGEM", scan_variable=ScanVariable.THGEM_VOLTAGE,
            start=200, stop=300, step=50, wait_seconds=0.0,
        )
        logger.open_run(params.label)
        result = controller.run_scan(backend, params, logger, ScanCallbacks(), threading.Event())
        logger.close()

        self.assertTrue(result.success)
        for label in CHANNEL_LABELS:  # ramped to 1 V but left ON
            self.assertEqual(backend._channel_state[label]["voltage_v"], 1.0)
            self.assertTrue(backend._channel_state[label]["is_on"])


if __name__ == "__main__":
    unittest.main()
