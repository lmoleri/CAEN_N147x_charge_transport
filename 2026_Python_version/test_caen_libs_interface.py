from __future__ import annotations

import unittest

from caen_interface import BaseCaenInterface, CaenLibsInterface


class CaenLibsInterfaceTests(unittest.TestCase):
    def test_is_a_backend_and_constructs(self) -> None:
        backend = CaenLibsInterface(com_port="COM3")
        self.assertIsInstance(backend, BaseCaenInterface)
        self.assertEqual(backend.com_port, "COM3")
        self.assertFalse(backend._connected)

    def test_connect_without_driver_or_device_raises_runtimeerror(self) -> None:
        # caen-libs loads the native CAEN HV Wrapper at import time; without the
        # driver (dev/CI) or a connected device, connect() must raise a clear
        # RuntimeError rather than crash — and never reaches the connected state.
        backend = CaenLibsInterface(com_port="COM3")
        with self.assertRaises(RuntimeError):
            backend.connect()
        self.assertFalse(backend._connected)


if __name__ == "__main__":
    unittest.main()
