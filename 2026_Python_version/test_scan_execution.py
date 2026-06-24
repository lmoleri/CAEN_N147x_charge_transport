from __future__ import annotations

import csv
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from caen_interface import CHANNEL_LABELS, SimulationInterface
from data_logger import DataLogger
from scan_controller import ScanCallbacks, ScanController, ScanParameters


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

    def test_single_curve_row_count_matches_scan_values(self) -> None:
        params = ScanParameters(label="Custom", wait_seconds=0.0)  # 150→1500 step 25 = 55 pts
        row_count, records = self._run(params)
        self.assertEqual(row_count, 55)
        self.assertEqual(len(records), 55)
        self.assertTrue(all(record.subscan_label == "Custom" for record in records))

    def test_custom_range_changes_point_count(self) -> None:
        params = ScanParameters(
            label="Short", vthgem1_start_v=200, vthgem1_stop_v=300, vthgem1_step_v=50, wait_seconds=0.0
        )
        row_count, records = self._run(params)
        self.assertEqual(row_count, 3)  # 200, 250, 300
        self.assertEqual(records[0].v_thgem1_v, 200.0)
        self.assertEqual(records[-1].v_thgem1_v, 300.0)

    def test_records_carry_field_settings(self) -> None:
        params = ScanParameters(label="Fields", drift_field_kv_cm=0.4, induction_field_kv_cm=2.0, wait_seconds=0.0)
        _, records = self._run(params)
        self.assertAlmostEqual(records[0].e_drift_kv_cm, 0.4)
        self.assertAlmostEqual(records[0].e_transfer_kv_cm, 2.0)

    def test_abort_stops_scan_and_powers_channels_off(self) -> None:
        backend = SimulationInterface(seed=11)
        backend.connect()
        backend.set_ramp_rates(300.0, 300.0)

        controller = ScanController()
        logger = DataLogger(self.output_dir)
        params = ScanParameters(label="Collection", wait_seconds=0.2)
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

        snapshots = backend.read_all_channels()
        self.assertTrue(all(not snapshot.is_on for snapshot in snapshots))
        self.assertTrue(all(abs(backend._channel_state[label]["voltage_v"] - 1.0) < 1e-9 for label in CHANNEL_LABELS))

        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            row_count = sum(1 for _ in csv.DictReader(handle))
        self.assertLess(row_count, 55)


if __name__ == "__main__":
    unittest.main()
