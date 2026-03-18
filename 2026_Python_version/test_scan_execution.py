from __future__ import annotations

import csv
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from caen_interface import CHANNEL_LABELS, SimulationInterface
from data_logger import DataLogger
from scan_controller import ScanCallbacks, ScanController


class ScanExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.output_dir = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _run_mode(self, mode: str) -> tuple[int, list]:
        backend = SimulationInterface(seed=7)
        backend.connect()
        backend.set_ramp_rates(300.0, 300.0)

        controller = ScanController(wait_seconds=0.0)
        logger = DataLogger(self.output_dir)
        csv_path = logger.open_run(mode)
        records = []
        callbacks = ScanCallbacks(on_point_recorded=records.append)

        result = controller.run_recipe(backend, mode, logger, callbacks, threading.Event())
        logger.close()

        self.assertTrue(result.success)
        self.assertFalse(result.aborted)

        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            row_count = sum(1 for _ in csv.DictReader(handle))

        return row_count, records

    def test_reference_collection_transfer_and_drift_row_counts(self) -> None:
        expected_rows = {
            "Reference": 55,
            "Collection scan": 55,
            "Transfer field scan": 220,
            "Drift field scan": 330,
        }
        for mode, expected in expected_rows.items():
            with self.subTest(mode=mode):
                row_count, records = self._run_mode(mode)
                self.assertEqual(row_count, expected)
                self.assertEqual(len(records), expected)

    def test_abort_stops_scan_and_powers_channels_off(self) -> None:
        backend = SimulationInterface(seed=11)
        backend.connect()
        backend.set_ramp_rates(300.0, 300.0)

        controller = ScanController(wait_seconds=0.2)
        logger = DataLogger(self.output_dir)
        csv_path = logger.open_run("Collection scan")
        abort_event = threading.Event()
        results = []

        def run_scan() -> None:
            result = controller.run_recipe(backend, "Collection scan", logger, ScanCallbacks(), abort_event)
            results.append(result)

        thread = threading.Thread(target=run_scan, daemon=True)
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
