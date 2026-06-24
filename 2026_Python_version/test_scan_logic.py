from __future__ import annotations

import math
import unittest

from scan_controller import (
    DRIFT_GAP_CM,
    INDUCTION_GAP_CM,
    MIN_HV_V,
    PRESETS,
    T1_ANCHOR_V,
    ScanController,
    ScanParameters,
)


class ScanLogicTests(unittest.TestCase):
    def test_solver_never_returns_zero_and_preserves_thgem1_voltage(self) -> None:
        cases = [(150.0, 0.0, 0.0), (1500.0, 1.0, 3.0), (1500.0, 0.0, -1.0), (625.0, 0.6, 1.0)]
        for v_thgem1_v, e_drift, e_induction in cases:
            with self.subTest(v=v_thgem1_v, ed=e_drift, ei=e_induction):
                params = ScanParameters(drift_field_kv_cm=e_drift, induction_field_kv_cm=e_induction)
                solved = params.solve(v_thgem1_v)
                self.assertTrue(all(value >= MIN_HV_V for value in solved.values()))
                self.assertTrue(math.isclose(solved["B1"] + T1_ANCHOR_V, v_thgem1_v, abs_tol=1e-9))

    def test_solver_matches_gap_formulas(self) -> None:
        params = ScanParameters(drift_field_kv_cm=0.8, induction_field_kv_cm=2.0)
        solved = params.solve(1000.0)
        self.assertAlmostEqual(solved["C"], max(1.0, 1.0 + 0.8 * 1000.0 * DRIFT_GAP_CM))
        self.assertAlmostEqual(solved["T2"], max(1.0, solved["B1"] + 2.0 * 1000.0 * INDUCTION_GAP_CM))

    def test_gaps_are_configurable(self) -> None:
        params = ScanParameters(
            drift_field_kv_cm=1.0, drift_gap_cm=0.3,
            induction_field_kv_cm=1.0, induction_gap_cm=0.2,
        )
        solved = params.solve(500.0)
        self.assertAlmostEqual(solved["C"], 1.0 + 1.0 * 1000.0 * 0.3)
        self.assertAlmostEqual(solved["T2"], solved["B1"] + 1.0 * 1000.0 * 0.2)

    def test_scan_values_inclusive_range(self) -> None:
        params = ScanParameters(vthgem1_start_v=150, vthgem1_stop_v=1500, vthgem1_step_v=25)
        values = params.scan_values()
        self.assertEqual(len(values), 55)
        self.assertEqual(values[0], 150.0)
        self.assertEqual(values[-1], 1500.0)

    def test_presets_pre_fill_distinct_field_settings(self) -> None:
        controller = ScanController()
        self.assertEqual(controller.preset_names(), list(PRESETS))
        self.assertFalse(controller.preset("Reference").uv_expected)
        self.assertEqual(controller.preset("Reference").induction_field_kv_cm, 0.0)
        self.assertTrue(controller.preset("Collection scan").uv_expected)


if __name__ == "__main__":
    unittest.main()
