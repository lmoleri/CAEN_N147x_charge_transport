from __future__ import annotations

import unittest

from caen_interface import (
    CAEN_WRAPPER_CURRENT_SOURCE_IMONL,
    CAEN_WRAPPER_MODEL_N1470,
    CAEN_WRAPPER_MODEL_N1471,
    CAENWrapperInterface,
    UsbVcpSettings,
)


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


if __name__ == "__main__":
    unittest.main()
