from __future__ import annotations

import math
import unittest

from scan_controller import MIN_HV_V, ScanController, T1_ANCHOR_V


class ScanLogicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = ScanController(wait_seconds=0.0)

    def test_solver_never_returns_zero_and_preserves_thgem1_voltage(self) -> None:
        test_cases = [
            (150.0, 0.0, 0.0),
            (1500.0, 1.0, 3.0),
            (1500.0, 0.0, -1.0),
            (625.0, 0.6, 1.0),
        ]
        for v_thgem1_v, e_drift_kv_cm, e_transfer_kv_cm in test_cases:
            with self.subTest(
                v_thgem1_v=v_thgem1_v,
                e_drift_kv_cm=e_drift_kv_cm,
                e_transfer_kv_cm=e_transfer_kv_cm,
            ):
                solved = self.controller.solve_configuration(v_thgem1_v, e_drift_kv_cm, e_transfer_kv_cm)
                self.assertTrue(all(value >= MIN_HV_V for value in solved.values()))
                self.assertTrue(math.isclose(solved["B1"] + T1_ANCHOR_V, v_thgem1_v, rel_tol=0, abs_tol=1e-9))

    def test_solver_matches_gap_formulas(self) -> None:
        solved = self.controller.solve_configuration(1000.0, 0.8, 2.0)
        self.assertAlmostEqual(solved["C"], max(1.0, 1.0 + 0.8 * 1000.0 * 0.5))
        self.assertAlmostEqual(solved["T2"], max(1.0, solved["B1"] + 2.0 * 1000.0 * 0.1))

    def test_recipe_structure_and_point_counts(self) -> None:
        expected_subscan_counts = {
            "Reference": 1,
            "Collection scan": 1,
            "Transfer field scan": 4,
            "Drift field scan": 6,
        }
        for mode, subscan_count in expected_subscan_counts.items():
            with self.subTest(mode=mode):
                self.assertEqual(len(self.controller.field_configs_for_mode(mode)), subscan_count)
                self.assertEqual(self.controller.points_per_subscan(), 55)


if __name__ == "__main__":
    unittest.main()
