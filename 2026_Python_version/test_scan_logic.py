from __future__ import annotations

import unittest

from scan_controller import (
    DRIFT_GAP_CM,
    INDUCTION_GAP_CM,
    MIN_HV_V,
    ScanController,
    ScanParameters,
    ScanVariable,
    variable_spec_for,
)


class ScanLogicTests(unittest.TestCase):
    def test_point_state_overrides_only_the_swept_quantity(self) -> None:
        base = dict(t1_v=300.0, b1_v=400.0, drift_field_kv_cm=0.3, induction_field_kv_cm=1.0)
        self.assertEqual(
            ScanParameters(scan_variable=ScanVariable.THGEM_VOLTAGE, **base).point_state(900.0),
            (300.0, 900.0, 0.3, 1.0),  # gain sweeps B1
        )
        self.assertEqual(
            ScanParameters(scan_variable=ScanVariable.DRIFT_FIELD, **base).point_state(0.8),
            (300.0, 400.0, 0.8, 1.0),
        )
        self.assertEqual(
            ScanParameters(scan_variable=ScanVariable.INDUCTION_FIELD, **base).point_state(2.5),
            (300.0, 400.0, 0.3, 2.5),
        )

    def test_gain_scan_sweeps_b1_and_holds_t1(self) -> None:
        params = ScanParameters(
            scan_variable=ScanVariable.THGEM_VOLTAGE, t1_v=300.0, b1_v=400.0,
            drift_field_kv_cm=0.0, induction_field_kv_cm=1.0,
        )
        lo, hi = params.solve(200.0), params.solve(900.0)
        self.assertEqual((lo["B1"], hi["B1"]), (200.0, 900.0))  # B1 swept
        self.assertEqual(lo["T1"], hi["T1"])                     # T1 held
        self.assertEqual(lo["T1"], 300.0)
        self.assertAlmostEqual(lo["C"], hi["C"])                 # E_drift held (0) → C fixed
        self.assertNotAlmostEqual(lo["T2"], hi["T2"])            # T2 tracks B1 to hold E_induction

    def test_thgem_voltage_is_b1_plus_t1(self) -> None:
        params = ScanParameters(scan_variable=ScanVariable.THGEM_VOLTAGE, t1_v=300.0, b1_v=400.0)
        self.assertAlmostEqual(params.thgem_voltage_at(500.0), 500.0 + 300.0)  # swept B1 + held T1

    def test_drift_scan_moves_only_the_cathode(self) -> None:
        params = ScanParameters(
            scan_variable=ScanVariable.DRIFT_FIELD, t1_v=300.0, b1_v=400.0, induction_field_kv_cm=1.0
        )
        lo, hi = params.solve(0.2), params.solve(1.5)
        self.assertNotAlmostEqual(lo["C"], hi["C"])  # cathode tracks E_drift off T1
        for label in ("T1", "B1", "T2"):
            self.assertAlmostEqual(lo[label], hi[label])
        self.assertAlmostEqual(hi["C"], 300.0 + 1.5 * 1000.0 * DRIFT_GAP_CM)
        self.assertAlmostEqual(params.thgem_voltage_at(0.2), 700.0)  # ΔV held = B1+T1

    def test_induction_scan_moves_only_the_top_electrode(self) -> None:
        params = ScanParameters(
            scan_variable=ScanVariable.INDUCTION_FIELD, t1_v=300.0, b1_v=400.0, drift_field_kv_cm=0.5
        )
        lo, hi = params.solve(0.5), params.solve(3.0)
        self.assertNotAlmostEqual(lo["T2"], hi["T2"])  # top electrode tracks E_induction off B1
        for label in ("C", "T1", "B1"):
            self.assertAlmostEqual(lo[label], hi[label])
        self.assertAlmostEqual(hi["T2"], 400.0 + 3.0 * 1000.0 * INDUCTION_GAP_CM)

    def test_signed_solve_applies_electrode_polarity(self) -> None:
        params = ScanParameters(
            scan_variable=ScanVariable.THGEM_VOLTAGE, t1_v=300.0, b1_v=400.0,
            drift_field_kv_cm=0.0, induction_field_kv_cm=1.0,
        )
        signed, mags = params.signed_solve(400.0), params.solve(400.0)
        self.assertLess(signed["C"], 0.0)     # C is negative polarity
        self.assertLess(signed["T1"], 0.0)    # T1 is negative polarity
        self.assertGreater(signed["B1"], 0.0)  # B1 is positive polarity
        self.assertGreater(signed["T2"], 0.0)  # T2 is positive polarity
        self.assertAlmostEqual(abs(signed["T1"]), mags["T1"])

    def test_solver_floors_at_min_hv(self) -> None:
        params = ScanParameters(
            scan_variable=ScanVariable.DRIFT_FIELD, t1_v=0.0, b1_v=0.0,
            drift_field_kv_cm=0.0, induction_field_kv_cm=0.0,
        )
        self.assertTrue(all(value >= MIN_HV_V for value in params.solve(0.0).values()))

    def test_gaps_are_configurable(self) -> None:
        params = ScanParameters(
            scan_variable=ScanVariable.DRIFT_FIELD, t1_v=300.0, b1_v=400.0,
            drift_field_kv_cm=1.0, drift_gap_cm=0.3, induction_field_kv_cm=1.0, induction_gap_cm=0.2,
        )
        solved = params.solve(1.0)
        self.assertAlmostEqual(solved["C"], 300.0 + 1.0 * 1000.0 * 0.3)
        self.assertAlmostEqual(solved["T2"], 400.0 + 1.0 * 1000.0 * 0.2)

    def test_scan_values_inclusive_range(self) -> None:
        params = ScanParameters(start=150, stop=1500, step=25)
        values = params.scan_values()
        self.assertEqual(len(values), 55)
        self.assertEqual((values[0], values[-1]), (150.0, 1500.0))

    def test_presets_are_three_distinct_programs(self) -> None:
        controller = ScanController()
        names = controller.preset_names()
        self.assertEqual(len(names), 3)
        self.assertEqual({controller.preset(n).scan_variable for n in names}, set(ScanVariable))
        drift = controller.preset("Drift field scan")
        self.assertIs(drift.scan_variable, ScanVariable.DRIFT_FIELD)
        self.assertEqual(drift.unit(), "kV/cm")
        gain = controller.preset("THGEM voltage (gain)")
        self.assertEqual(gain.spec.symbol, "B1")  # gain sweeps B1
        self.assertEqual(gain.axis_title(), "THGEM1 voltage [V]")

    def test_variable_spec_lookup_handles_enum_value_and_legacy(self) -> None:
        self.assertEqual(variable_spec_for("drift_field").record_column, "e_drift_kv_cm")
        self.assertEqual(variable_spec_for(ScanVariable.INDUCTION_FIELD).axis_title, "Induction field [kV/cm]")
        # Unknown / legacy value falls back to the THGEM-voltage spec.
        self.assertEqual(variable_spec_for("e_transfer_kv_cm").record_column, "v_thgem1_v")


if __name__ == "__main__":
    unittest.main()
