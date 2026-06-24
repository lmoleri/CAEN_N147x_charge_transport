from __future__ import annotations

import unittest

from caen_interface import CAENWrapperInterface, UsbVcpSettings


class CaenWrapperHelperTests(unittest.TestCase):
    def test_usb_vcp_argument_format(self) -> None:
        self.assertEqual(CAENWrapperInterface.build_usb_vcp_argument("COM13"), "COM13_115200_8_0_0_0")

    def test_usb_vcp_argument_format_with_overrides(self) -> None:
        settings = UsbVcpSettings(
            com_port="COM7",
            transport="raw wrapper",
            baud=9600,
            data_bits=7,
            stop_bits=2,
            parity=1,
            board_number=3,
        )
        self.assertEqual(settings.build_argument(), "COM7_9600_7_2_1_3")

    def test_parameter_alias_resolution(self) -> None:
        resolved = CAENWrapperInterface.resolve_parameter_names(
            ["V0Set", "VMon", "IMon", "Pw", "Status", "RUp", "RDW"]
        )
        self.assertEqual(resolved["voltage_set"], "V0Set")
        self.assertEqual(resolved["current_monitor"], "IMon")
        self.assertEqual(resolved["power"], "Pw")

    def test_missing_required_parameter_alias_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            CAENWrapperInterface.resolve_parameter_names(["V0Set", "VMon", "Pw"])


if __name__ == "__main__":
    unittest.main()
