from __future__ import annotations

import unittest

from caen_interface import (
    CAEN_TRANSPORT_AUTO,
    CAEN_TRANSPORT_LIBS,
    CAEN_TRANSPORT_RAW_WRAPPER,
    BaseCaenInterface,
    CaenLibsInterface,
    CaenUsbVcpInterface,
    UsbVcpSettings,
    format_caen_connection_error,
)


class _StubBackend(BaseCaenInterface):
    def __init__(self, connection_name: str, connect_error: Exception | None = None) -> None:
        self._connection_name = connection_name
        self._connect_error = connect_error
        self.connect_calls = 0
        self.disconnect_calls = 0

    def connect(self) -> None:
        self.connect_calls += 1
        if self._connect_error is not None:
            raise self._connect_error

    def disconnect(self) -> None:
        self.disconnect_calls += 1

    def read_all_channels(self) -> list:
        return []

    def set_ramp_rates(self, ramp_up_v_s: float, ramp_down_v_s: float) -> None:
        del ramp_up_v_s, ramp_down_v_s

    def set_channel_voltages(self, voltages_by_label) -> None:
        del voltages_by_label

    def power_on_channels(self, labels) -> None:
        del labels

    def power_off_channels(self, labels) -> None:
        del labels

    def safe_shutdown(self, labels) -> None:
        del labels

    def connection_name(self) -> str:
        return self._connection_name


class CaenLibsInterfaceTests(unittest.TestCase):
    def test_is_a_backend_and_constructs(self) -> None:
        backend = CaenLibsInterface(com_port="COM3")
        self.assertIsInstance(backend, BaseCaenInterface)
        self.assertEqual(backend.com_port, "COM3")
        self.assertFalse(backend._connected)

    def test_connect_without_driver_or_device_raises_runtimeerror(self) -> None:
        backend = CaenLibsInterface(com_port="COM3")
        with self.assertRaises(RuntimeError):
            backend.connect()
        self.assertFalse(backend._connected)


class CaenUsbVcpInterfaceTests(unittest.TestCase):
    def test_auto_falls_back_from_caen_libs_to_raw_wrapper(self) -> None:
        libs_backend = _StubBackend("CAEN USB-VCP via caen-libs", RuntimeError("libs failed"))
        raw_backend = _StubBackend("CAEN USB-VCP via raw wrapper")
        backend = CaenUsbVcpInterface(
            UsbVcpSettings(com_port="COM4", transport=CAEN_TRANSPORT_AUTO),
            backend_factories={
                CAEN_TRANSPORT_LIBS: lambda: libs_backend,
                CAEN_TRANSPORT_RAW_WRAPPER: lambda: raw_backend,
            },
        )

        backend.connect()

        self.assertEqual(libs_backend.connect_calls, 1)
        self.assertEqual(raw_backend.connect_calls, 1)
        self.assertEqual(backend.connection_name(), "CAEN USB-VCP via raw wrapper")

    def test_caen_libs_only_does_not_fallback(self) -> None:
        libs_backend = _StubBackend("CAEN USB-VCP via caen-libs", RuntimeError("libs failed"))
        raw_backend = _StubBackend("CAEN USB-VCP via raw wrapper")
        backend = CaenUsbVcpInterface(
            UsbVcpSettings(com_port="COM4", transport=CAEN_TRANSPORT_LIBS),
            backend_factories={
                CAEN_TRANSPORT_LIBS: lambda: libs_backend,
                CAEN_TRANSPORT_RAW_WRAPPER: lambda: raw_backend,
            },
        )

        with self.assertRaisesRegex(RuntimeError, "libs failed"):
            backend.connect()

        self.assertEqual(libs_backend.connect_calls, 1)
        self.assertEqual(raw_backend.connect_calls, 0)

    def test_raw_wrapper_only_does_not_fallback(self) -> None:
        libs_backend = _StubBackend("CAEN USB-VCP via caen-libs")
        raw_backend = _StubBackend("CAEN USB-VCP via raw wrapper", RuntimeError("wrapper failed"))
        backend = CaenUsbVcpInterface(
            UsbVcpSettings(com_port="COM4", transport=CAEN_TRANSPORT_RAW_WRAPPER),
            backend_factories={
                CAEN_TRANSPORT_LIBS: lambda: libs_backend,
                CAEN_TRANSPORT_RAW_WRAPPER: lambda: raw_backend,
            },
        )

        with self.assertRaisesRegex(RuntimeError, "wrapper failed"):
            backend.connect()

        self.assertEqual(libs_backend.connect_calls, 0)
        self.assertEqual(raw_backend.connect_calls, 1)

    def test_connection_error_message_includes_backend_argument_and_code(self) -> None:
        message = format_caen_connection_error(
            backend_label=CAEN_TRANSPORT_RAW_WRAPPER,
            settings=UsbVcpSettings(com_port="COM4"),
            detail="CAENHV_InitSystem failed",
            code=4100,
            error_text="LOGINFAILED",
        )

        self.assertIn("raw wrapper", message)
        self.assertIn("COM4_115200_8_0_0_0", message)
        self.assertIn("code 4100", message)
        self.assertIn("LOGINFAILED", message)


if __name__ == "__main__":
    unittest.main()
