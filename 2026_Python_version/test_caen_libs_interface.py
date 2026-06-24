from __future__ import annotations

import unittest

import caen_interface
from caen_interface import (
    CAEN_TRANSPORT_DIRECT_SERIAL,
    CAEN_TRANSPORT_LIBS,
    CAEN_TRANSPORT_RAW_WRAPPER,
    CAEN_TRANSPORT_WRAPPER_AUTO,
    CAEN_WRAPPER_CURRENT_SOURCE_AUTO,
    CAEN_WRAPPER_MODEL_N1470,
    CAEN_WRAPPER_MODEL_N1471,
    BaseCaenInterface,
    CaenDirectSerialInterface,
    CaenLibsInterface,
    CaenUsbVcpInterface,
    UsbVcpSettings,
    format_caen_connection_error,
    format_direct_serial_error,
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


class _RecordingHvDevice:
    def __init__(self) -> None:
        self.open_calls: list[tuple[object, object, str, str, str]] = []

    def open(self, system_type, link_type, argument, username, password):
        self.open_calls.append((system_type, link_type, argument, username, password))
        raise RuntimeError("boom")


class _RecordingRawLibrary:
    def __init__(self) -> None:
        self.init_calls: list[tuple[int, int, bytes, bytes, bytes]] = []

    def CAENHV_InitSystem(self, system_type, link_type, argument, username, password, handle_ptr):
        self.init_calls.append((system_type, link_type, argument.value, username.value, password.value))
        return 1234

    def CAENHV_GetError(self, handle):
        del handle
        return b"LOGINFAILED"


class _FakeSerial:
    def __init__(self, responses_by_command: dict[str, str | bytes] | None = None, **kwargs) -> None:
        self.responses_by_command = responses_by_command or {}
        self.kwargs = kwargs
        self.write_history: list[str] = []
        self._buffer = b""
        self.closed = False

    @property
    def in_waiting(self) -> int:
        return len(self._buffer)

    def reset_input_buffer(self) -> None:
        self._buffer = b""

    def reset_output_buffer(self) -> None:
        return None

    def write(self, payload: bytes) -> int:
        command = payload.decode("ascii")
        self.write_history.append(command)
        response = self.responses_by_command.get(command, b"")
        self._buffer = response.encode("ascii") if isinstance(response, str) else response
        return len(payload)

    def flush(self) -> None:
        return None

    def read(self, size: int = 1) -> bytes:
        if size <= 0:
            return b""
        chunk = self._buffer[:size]
        self._buffer = self._buffer[size:]
        return chunk

    def close(self) -> None:
        self.closed = True


class _FakeSerialFactory:
    def __init__(self, responses_by_command: dict[str, str | bytes] | None = None, error: Exception | None = None) -> None:
        self.responses_by_command = responses_by_command or {}
        self.error = error
        self.last_kwargs: dict[str, object] | None = None
        self.instance: _FakeSerial | None = None

    def __call__(self, **kwargs):
        if self.error is not None:
            raise self.error
        self.last_kwargs = dict(kwargs)
        self.instance = _FakeSerial(self.responses_by_command, **kwargs)
        return self.instance


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

    def test_usb_open_uses_empty_credentials_and_logger_style_argument(self) -> None:
        import builtins
        import types
        from unittest import mock

        device = _RecordingHvDevice()
        fake_hv = types.SimpleNamespace(
            SystemType=types.SimpleNamespace(N1470="N1470", N1471="N1471"),
            LinkType=types.SimpleNamespace(USB_VCP="USB_VCP"),
            Device=device,
        )
        original_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "caen_libs" and fromlist == ("caenhvwrapper",):
                return types.SimpleNamespace(caenhvwrapper=fake_hv)
            return original_import(name, globals, locals, fromlist, level)

        backend = CaenLibsInterface(
            settings=UsbVcpSettings(com_port="4", wrapper_model=CAEN_WRAPPER_MODEL_N1471)
        )
        with mock.patch("builtins.__import__", side_effect=fake_import):
            with self.assertRaisesRegex(RuntimeError, "COM4_9600_8_1_none_0"):
                backend.connect()

        self.assertEqual(device.open_calls, [("N1471", "USB_VCP", "COM4_9600_8_1_none_0", "", "")])

    def test_caen_libs_reports_missing_n1471_system_type(self) -> None:
        import builtins
        import types
        from unittest import mock

        device = _RecordingHvDevice()
        fake_hv = types.SimpleNamespace(
            SystemType=types.SimpleNamespace(N1470="N1470"),
            LinkType=types.SimpleNamespace(USB_VCP="USB_VCP"),
            Device=device,
        )
        original_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "caen_libs" and fromlist == ("caenhvwrapper",):
                return types.SimpleNamespace(caenhvwrapper=fake_hv)
            return original_import(name, globals, locals, fromlist, level)

        backend = CaenLibsInterface(
            settings=UsbVcpSettings(com_port="4", wrapper_model=CAEN_WRAPPER_MODEL_N1471)
        )
        with mock.patch("builtins.__import__", side_effect=fake_import):
            with self.assertRaisesRegex(RuntimeError, r"SystemType\.N1471"):
                backend.connect()

        self.assertEqual(device.open_calls, [])


class CaenUsbVcpInterfaceTests(unittest.TestCase):
    def test_auto_falls_back_from_caen_libs_to_raw_wrapper(self) -> None:
        libs_backend = _StubBackend("CAEN USB-VCP via caen-libs", RuntimeError("libs failed"))
        raw_backend = _StubBackend("CAEN USB-VCP via raw wrapper")
        backend = CaenUsbVcpInterface(
            UsbVcpSettings(com_port="COM4", transport=CAEN_TRANSPORT_WRAPPER_AUTO),
            backend_factories={
                CAEN_TRANSPORT_LIBS: lambda: libs_backend,
                CAEN_TRANSPORT_RAW_WRAPPER: lambda: raw_backend,
            },
        )

        backend.connect()

        self.assertEqual(libs_backend.connect_calls, 1)
        self.assertEqual(raw_backend.connect_calls, 1)
        self.assertEqual(backend.connection_name(), "CAEN USB-VCP via raw wrapper")

    def test_direct_serial_does_not_fallback_to_wrapper_backends(self) -> None:
        direct_backend = _StubBackend("CAEN USB-VCP via direct serial", RuntimeError("serial failed"))
        libs_backend = _StubBackend("CAEN USB-VCP via caen-libs")
        raw_backend = _StubBackend("CAEN USB-VCP via raw wrapper")
        backend = CaenUsbVcpInterface(
            UsbVcpSettings(com_port="COM4", transport=CAEN_TRANSPORT_DIRECT_SERIAL),
            backend_factories={
                CAEN_TRANSPORT_DIRECT_SERIAL: lambda: direct_backend,
                CAEN_TRANSPORT_LIBS: lambda: libs_backend,
                CAEN_TRANSPORT_RAW_WRAPPER: lambda: raw_backend,
            },
        )

        with self.assertRaisesRegex(RuntimeError, "serial failed"):
            backend.connect()

        self.assertEqual(direct_backend.connect_calls, 1)
        self.assertEqual(libs_backend.connect_calls, 0)
        self.assertEqual(raw_backend.connect_calls, 0)

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
            settings=UsbVcpSettings(com_port="COM4", wrapper_model=CAEN_WRAPPER_MODEL_N1471),
            detail="CAENHV_InitSystem failed",
            code=4100,
            error_text="LOGINFAILED",
        )

        self.assertIn("N1471H / N1471", message)
        self.assertIn("raw wrapper", message)
        self.assertIn("COM4_9600_8_1_none_0", message)
        self.assertIn("code 4100", message)
        self.assertIn("LOGINFAILED", message)

    def test_raw_wrapper_uses_empty_credentials_and_logger_style_argument(self) -> None:
        from unittest import mock

        from caen_interface import CAENWrapperInterface, LINKTYPE_USB_VCP, SYSTEM_TYPE_N1470

        library = _RecordingRawLibrary()
        backend = CAENWrapperInterface(
            settings=UsbVcpSettings(
                com_port="4",
                wrapper_model=CAEN_WRAPPER_MODEL_N1471,
                wrapper_current_source=CAEN_WRAPPER_CURRENT_SOURCE_AUTO,
            )
        )

        with mock.patch("caen_interface.os.name", "nt"):
            with mock.patch.object(backend, "_load_library", return_value=library):
                with mock.patch.object(backend, "_configure_library", return_value=None):
                    with self.assertRaisesRegex(RuntimeError, "N14xx family system code"):
                        backend.connect()

        self.assertEqual(
            library.init_calls,
            [(SYSTEM_TYPE_N1470, LINKTYPE_USB_VCP, b"COM4_9600_8_1_none_0", b"", b"")],
        )


class CaenDirectSerialInterfaceTests(unittest.TestCase):
    def test_primary_protocol_builds_expected_commands(self) -> None:
        protocol = CaenDirectSerialInterface.PRIMARY_PROTOCOL

        self.assertEqual(protocol.build_module_monitor_command("BDNCH"), "$BD:0,CMD:MON,PAR:BDNCH")
        self.assertEqual(protocol.build_channel_monitor_command(2, "VMON"), "$BD:0,CMD:MON,CH:2,PAR:VMON")
        self.assertEqual(protocol.build_channel_set_command(1, "VSET", 123.4), "$BD:0,CMD:SET,CH:1,PAR:VSET,VAL:123.4")
        self.assertEqual(protocol.build_channel_action_command(3, "ON"), "$BD:0,CMD:SET,CH:3,PAR:ON")

    def test_direct_serial_open_uses_normalized_port_and_serial_settings(self) -> None:
        factory = _FakeSerialFactory(
            {
                "$BD:0,CMD:MON,PAR:BDNCH\r": "#BD:0,CMD:OK,VAL:4\r",
            }
        )
        backend = CaenDirectSerialInterface(settings=UsbVcpSettings(com_port="4"), serial_factory=factory)

        backend.connect()

        self.assertEqual(factory.last_kwargs["port"], "COM4")
        self.assertEqual(factory.last_kwargs["baudrate"], 9600)
        self.assertEqual(factory.last_kwargs["bytesize"], 8)
        expected_parity = caen_interface.serial.PARITY_NONE if caen_interface.serial is not None else "N"
        expected_stop_bits = caen_interface.serial.STOPBITS_ONE if caen_interface.serial is not None else 1
        self.assertEqual(factory.last_kwargs["parity"], expected_parity)
        self.assertEqual(factory.last_kwargs["stopbits"], expected_stop_bits)

        backend.disconnect()
        self.assertTrue(factory.instance.closed)

    def test_direct_serial_readback_parses_channel_values(self) -> None:
        responses = {
            "$BD:0,CMD:MON,PAR:BDNCH\r": "#BD:0,CMD:OK,VAL:4\r",
            "$BD:0,CMD:MON,CH:0,PAR:VMON\r": "#BD:0,CMD:OK,VAL:100.0\r",
            "$BD:0,CMD:MON,CH:0,PAR:IMON\r": "#BD:0,CMD:OK,VAL:0.5\r",
            "$BD:0,CMD:MON,CH:0,PAR:STAT\r": "#BD:0,CMD:OK,VAL:1\r",
            "$BD:0,CMD:MON,CH:1,PAR:VMON\r": "#BD:0,CMD:OK,VAL:50.0\r",
            "$BD:0,CMD:MON,CH:1,PAR:IMON\r": "#BD:0,CMD:OK,VAL:0.25\r",
            "$BD:0,CMD:MON,CH:1,PAR:STAT\r": "#BD:0,CMD:OK,VAL:0\r",
            "$BD:0,CMD:MON,CH:2,PAR:VMON\r": "#BD:0,CMD:OK,VAL:75.0\r",
            "$BD:0,CMD:MON,CH:2,PAR:IMON\r": "#BD:0,CMD:OK,VAL:0.75\r",
            "$BD:0,CMD:MON,CH:2,PAR:STAT\r": "#BD:0,CMD:OK,VAL:3\r",
            "$BD:0,CMD:MON,CH:3,PAR:VMON\r": "#BD:0,CMD:OK,VAL:25.0\r",
            "$BD:0,CMD:MON,CH:3,PAR:IMON\r": "#BD:0,CMD:OK,VAL:0.125\r",
            "$BD:0,CMD:MON,CH:3,PAR:STAT\r": "#BD:0,CMD:OK,VAL:0\r",
        }
        backend = CaenDirectSerialInterface(
            settings=UsbVcpSettings(com_port="COM4"),
            serial_factory=_FakeSerialFactory(responses),
        )

        backend.connect()
        snapshots = backend.read_all_channels()

        self.assertEqual([snapshot.label for snapshot in snapshots], ["C", "T1", "B1", "T2"])
        self.assertEqual(snapshots[0].vmon_v, 100.0)
        self.assertEqual(snapshots[0].imon_na, -0.5)
        self.assertTrue(snapshots[0].is_on)
        self.assertEqual(snapshots[0].status_code, 1)
        self.assertEqual(snapshots[2].imon_na, 0.75)
        self.assertTrue(snapshots[2].is_on)

    def test_direct_serial_setters_emit_expected_commands(self) -> None:
        responses = {
            "$BD:0,CMD:MON,PAR:BDNCH\r": "#BD:0,CMD:OK,VAL:4\r",
            "$BD:0,CMD:SET,CH:2,PAR:VSET,VAL:123.4\r": "#BD:0,CMD:OK\r",
            "$BD:0,CMD:SET,CH:0,PAR:RUP,VAL:200\r": "#BD:0,CMD:OK\r",
            "$BD:0,CMD:SET,CH:0,PAR:RDW,VAL:150\r": "#BD:0,CMD:OK\r",
            "$BD:0,CMD:SET,CH:1,PAR:RUP,VAL:200\r": "#BD:0,CMD:OK\r",
            "$BD:0,CMD:SET,CH:1,PAR:RDW,VAL:150\r": "#BD:0,CMD:OK\r",
            "$BD:0,CMD:SET,CH:2,PAR:RUP,VAL:200\r": "#BD:0,CMD:OK\r",
            "$BD:0,CMD:SET,CH:2,PAR:RDW,VAL:150\r": "#BD:0,CMD:OK\r",
            "$BD:0,CMD:SET,CH:3,PAR:RUP,VAL:200\r": "#BD:0,CMD:OK\r",
            "$BD:0,CMD:SET,CH:3,PAR:RDW,VAL:150\r": "#BD:0,CMD:OK\r",
            "$BD:0,CMD:SET,CH:0,PAR:ON\r": "#BD:0,CMD:OK\r",
            "$BD:0,CMD:SET,CH:3,PAR:OFF\r": "#BD:0,CMD:OK\r",
        }
        factory = _FakeSerialFactory(responses)
        backend = CaenDirectSerialInterface(settings=UsbVcpSettings(com_port="COM4"), serial_factory=factory)

        backend.connect()
        backend.set_channel_voltages({"B1": 123.4})
        backend.set_ramp_rates(200.0, 150.0)
        backend.power_on_channels(["C"])
        backend.power_off_channels(["T2"])

        self.assertIn("$BD:0,CMD:SET,CH:2,PAR:VSET,VAL:123.4\r", factory.instance.write_history)
        self.assertIn("$BD:0,CMD:SET,CH:0,PAR:RUP,VAL:200\r", factory.instance.write_history)
        self.assertIn("$BD:0,CMD:SET,CH:0,PAR:RDW,VAL:150\r", factory.instance.write_history)
        self.assertIn("$BD:0,CMD:SET,CH:0,PAR:ON\r", factory.instance.write_history)
        self.assertIn("$BD:0,CMD:SET,CH:3,PAR:OFF\r", factory.instance.write_history)

    def test_direct_serial_connection_error_includes_backend_and_settings(self) -> None:
        backend = CaenDirectSerialInterface(
            settings=UsbVcpSettings(com_port="4"),
            serial_factory=_FakeSerialFactory(error=OSError("Access denied")),
        )

        with self.assertRaises(RuntimeError) as ctx:
            backend.connect()
        message = str(ctx.exception)

        self.assertIn("direct serial", message)
        self.assertIn("COM4", message)
        self.assertIn("Access denied", message)

    def test_direct_serial_error_formatter_includes_serial_settings(self) -> None:
        message = format_direct_serial_error(
            settings=UsbVcpSettings(com_port="4"),
            detail="Could not open the serial port",
            error_text="Access denied",
        )

        self.assertIn("direct serial", message)
        self.assertIn("COM4", message)
        self.assertIn("baud=9600", message)
        self.assertIn("Access denied", message)


if __name__ == "__main__":
    unittest.main()
