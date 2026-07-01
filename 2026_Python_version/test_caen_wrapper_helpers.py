from __future__ import annotations

import unittest

from caen_interface import (
    CAEN_WRAPPER_CURRENT_SOURCE_IMONL,
    CAEN_WRAPPER_MODEL_N1470,
    CAEN_WRAPPER_MODEL_N1471,
    CAENWrapperInterface,
    ChannelControlState,
    ChannelSnapshot,
    RunPointRecord,
    UsbVcpSettings,
    decode_status,
    status_color_hex,
)
from plotly_view import build_figure


class CaenWrapperHelperTests(unittest.TestCase):
    def test_usb_vcp_argument_format(self) -> None:
        self.assertEqual(CAENWrapperInterface.build_usb_vcp_argument("COM13"), "COM13_9600_8_1_none_0")

    def test_usb_vcp_argument_format_with_overrides(self) -> None:
        settings = UsbVcpSettings(
            com_port="7",
            transport="raw wrapper",
            baud=9600,
            data_bits=7,
            stop_bits="2",
            parity="Even",
            board_number=3,
        )
        self.assertEqual(settings.build_argument(), "COM7_9600_7_2_even_3")

    def test_usb_vcp_argument_normalizes_numeric_com_port(self) -> None:
        settings = UsbVcpSettings(com_port="4")
        self.assertEqual(settings.build_argument(), "COM4_9600_8_1_none_0")

    def test_parameter_alias_resolution(self) -> None:
        resolved = CAENWrapperInterface.resolve_parameter_names(
            ["V0Set", "VMon", "IMon", "Pw", "Status", "RUp", "RDW"],
            UsbVcpSettings(com_port="COM4", wrapper_model=CAEN_WRAPPER_MODEL_N1470),
        )
        self.assertEqual(resolved["voltage_set"], "V0Set")
        self.assertEqual(resolved["current_monitor"], "IMon")
        self.assertEqual(resolved["power"], "Pw")

    def test_parameter_alias_resolution_prefers_imonl_for_n1471_auto(self) -> None:
        resolved = CAENWrapperInterface.resolve_parameter_names(
            ["VSet", "VMon", "IMonH", "IMonL", "Pw", "Status", "RUp", "RDW"],
            UsbVcpSettings(com_port="COM4", wrapper_model=CAEN_WRAPPER_MODEL_N1471),
        )

        self.assertEqual(resolved["current_monitor"], "IMonL")

    def test_parameter_alias_resolution_explicit_current_source_must_exist(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Try Current source = Auto"):
            CAENWrapperInterface.resolve_parameter_names(
                ["VSet", "VMon", "IMonH", "Pw", "Status", "RUp", "RDW"],
                UsbVcpSettings(
                    com_port="COM4",
                    wrapper_model=CAEN_WRAPPER_MODEL_N1471,
                    wrapper_current_source=CAEN_WRAPPER_CURRENT_SOURCE_IMONL,
                ),
            )

    def test_missing_required_parameter_alias_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            CAENWrapperInterface.resolve_parameter_names(["V0Set", "VMon", "Pw"])

    def test_status_decode_off_is_grey(self) -> None:
        self.assertEqual(decode_status(0, False), ("OFF", "off"))
        self.assertEqual(status_color_hex(0, False), "#dddddd")

    def test_status_decode_on_and_ramping_use_readable_labels(self) -> None:
        self.assertEqual(decode_status(1, True), ("ON", "on"))
        self.assertEqual(decode_status(3, True), ("ON Ramp↑", "ramping"))
        self.assertEqual(status_color_hex(3, True), "#f4d58d")

    def test_status_decode_trip_is_alarm_red(self) -> None:
        self.assertEqual(decode_status(1 << 7, False), ("TRIP", "alarm"))
        self.assertEqual(status_color_hex(1 << 7, False), "#f4a3a3")

    def test_build_control_states_returns_vset_and_ramps(self) -> None:
        controls = CAENWrapperInterface.build_control_states(
            None,
            [120.0, 220.0, 320.0, 420.0],
            [10.0, 20.0, 30.0, 40.0],
            [11.0, 21.0, 31.0, 41.0],
        )
        self.assertEqual(
            controls,
            [
                ChannelControlState("C", 0, 120.0, 10.0, 11.0),
                ChannelControlState("T1", 1, 220.0, 20.0, 21.0),
                ChannelControlState("B1", 2, 320.0, 30.0, 31.0),
                ChannelControlState("T2", 3, 420.0, 40.0, 41.0),
            ],
        )

    def test_raw_wrapper_manual_ramp_writes_use_expected_parameters(self) -> None:
        backend = object.__new__(CAENWrapperInterface)
        calls: list[tuple[int, str, float]] = []
        backend._ensure_connected = lambda: None
        backend._set_float_param = lambda channel_index, name, value: calls.append((channel_index, name, value))

        CAENWrapperInterface.set_channel_ramp_up_rates(backend, {"T1": 12.5})
        CAENWrapperInterface.set_channel_ramp_down_rates(backend, {"T2": 7.5})

        self.assertEqual(calls, [(1, "ramp_up", 12.5), (3, "ramp_down", 7.5)])

    def test_run_point_record_uses_microamp_fields(self) -> None:
        record = RunPointRecord.from_snapshots(
            mode="Reference",
            subscan_label="Reference",
            uv_expected=False,
            point_index=1,
            v_thgem1_v=250.0,
            e_drift_kv_cm=0.0,
            e_transfer_kv_cm=0.0,
            timestamp_iso="2026-06-24T12:00:00",
            snapshots=[
                ChannelSnapshot("C", 0, "-", 100.0, -0.1, True, 1, "ON"),
                ChannelSnapshot("T1", 1, "-", 101.0, -0.2, True, 3, "ON Ramp↑"),
                ChannelSnapshot("B1", 2, "+", 102.0, 0.3, False, 0, "OFF"),
                ChannelSnapshot("T2", 3, "+", 103.0, 0.4, True, 1 << 7, "TRIP"),
            ],
        )

        fieldnames = RunPointRecord.csv_fieldnames()

        self.assertIn("c_imon_ua", fieldnames)
        self.assertIn("t2_imon_ua", fieldnames)
        self.assertNotIn("c_imon_na", fieldnames)
        self.assertEqual(record.channel_snapshots()["B1"].imon_ua, 0.3)

    def test_plotly_axis_uses_microamp_units(self) -> None:
        self.assertEqual(build_figure([], set()).layout.yaxis.title.text, "Current [μA]")


if __name__ == "__main__":
    unittest.main()
