from __future__ import annotations

import math
import unittest

from scan_controller import (
    DRIFT_GAP_CM,
    INDUCTION_GAP_CM,
    MIN_HV_V,
    T1_ANCHOR_V,
    ScanController,
    ScanParameters,
    ScanVariable,
    variable_spec_for,
)


class ScanLogicTests(unittest.TestCase):
    def test_thgem_sweep_preserves_voltage_and_floors_at_min_hv(self) -> None:
        params = ScanParameters(
            scan_variable=ScanVariable.THGEM_VOLTAGE, drift_field_kv_cm=0.0, induction_field_kv_cm=0.0
        )
        for v_thgem1_v in (150.0, 700.0, 1500.0):
            with self.subTest(v=v_thgem1_v):
                solved = params.solve(v_thgem1_v)
                self.assertTrue(all(value >= MIN_HV_V for value in solved.values()))
                self.assertTrue(math.isclose(solved["B1"] + T1_ANCHOR_V, v_thgem1_v, abs_tol=1e-9))

    def test_point_fields_overrides_only_the_swept_quantity(self) -> None:
        base = dict(v_thgem1_v=700.0, drift_field_kv_cm=0.3, induction_field_kv_cm=1.0)
        self.assertEqual(
            ScanParameters(scan_variable=ScanVariable.THGEM_VOLTAGE, **base).point_fields(900.0),
            (900.0, 0.3, 1.0),
        )
        self.assertEqual(
            ScanParameters(scan_variable=ScanVariable.DRIFT_FIELD, **base).point_fields(0.8),
            (700.0, 0.8, 1.0),
        )
        self.assertEqual(
            ScanParameters(scan_variable=ScanVariable.INDUCTION_FIELD, **base).point_fields(2.5),
            (700.0, 0.3, 2.5),
        )

    def test_drift_scan_moves_only_the_cathode(self) -> None:
        params = ScanParameters(
            scan_variable=ScanVariable.DRIFT_FIELD, v_thgem1_v=700.0, induction_field_kv_cm=1.0
        )
        lo, hi = params.solve(0.2), params.solve(1.5)
        self.assertNotAlmostEqual(lo["C"], hi["C"])  # cathode tracks E_drift
        for label in ("T1", "B1", "T2"):
            self.assertAlmostEqual(lo[label], hi[label])  # everything else holds
        self.assertAlmostEqual(hi["C"], T1_ANCHOR_V + 1.5 * 1000.0 * DRIFT_GAP_CM)

    def test_induction_scan_moves_only_the_top_electrode(self) -> None:
        params = ScanParameters(
            scan_variable=ScanVariable.INDUCTION_FIELD, v_thgem1_v=700.0, drift_field_kv_cm=0.5
        )
        lo, hi = params.solve(0.5), params.solve(3.0)
        self.assertNotAlmostEqual(lo["T2"], hi["T2"])
        for label in ("C", "T1", "B1"):
            self.assertAlmostEqual(lo[label], hi[label])
        self.assertAlmostEqual(hi["T2"], hi["B1"] + 3.0 * 1000.0 * INDUCTION_GAP_CM)

    def test_solver_matches_gap_formulas_for_thgem_sweep(self) -> None:
        params = ScanParameters(
            scan_variable=ScanVariable.THGEM_VOLTAGE, drift_field_kv_cm=0.8, induction_field_kv_cm=2.0
        )
        solved = params.solve(1000.0)
        self.assertAlmostEqual(solved["C"], max(1.0, 1.0 + 0.8 * 1000.0 * DRIFT_GAP_CM))
        self.assertAlmostEqual(solved["T2"], max(1.0, solved["B1"] + 2.0 * 1000.0 * INDUCTION_GAP_CM))

    def test_gaps_are_configurable(self) -> None:
        params = ScanParameters(
            scan_variable=ScanVariable.THGEM_VOLTAGE,
            drift_field_kv_cm=1.0, drift_gap_cm=0.3,
            induction_field_kv_cm=1.0, induction_gap_cm=0.2,
        )
        solved = params.solve(500.0)
        self.assertAlmostEqual(solved["C"], 1.0 + 1.0 * 1000.0 * 0.3)
        self.assertAlmostEqual(solved["T2"], solved["B1"] + 1.0 * 1000.0 * 0.2)

    def test_scan_values_inclusive_range(self) -> None:
        params = ScanParameters(start=150, stop=1500, step=25)
        values = params.scan_values()
        self.assertEqual(len(values), 55)
        self.assertEqual(values[0], 150.0)
        self.assertEqual(values[-1], 1500.0)

    def test_presets_are_three_distinct_programs(self) -> None:
        controller = ScanController()
        names = controller.preset_names()
        self.assertEqual(len(names), 3)
        self.assertEqual({controller.preset(n).scan_variable for n in names}, set(ScanVariable))
        drift = controller.preset("Drift field scan")
        self.assertIs(drift.scan_variable, ScanVariable.DRIFT_FIELD)
        self.assertEqual(drift.unit(), "kV/cm")
        self.assertEqual(drift.axis_title(), "Drift field [kV/cm]")

    def test_variable_spec_lookup_handles_enum_value_and_legacy(self) -> None:
        self.assertEqual(variable_spec_for("drift_field").record_column, "e_drift_kv_cm")
        self.assertEqual(variable_spec_for(ScanVariable.INDUCTION_FIELD).axis_title, "Induction field [kV/cm]")
        # Unknown / legacy value falls back to the THGEM-voltage spec.
        self.assertEqual(variable_spec_for("e_transfer_kv_cm").record_column, "v_thgem1_v")


if __name__ == "__main__":
    unittest.main()
