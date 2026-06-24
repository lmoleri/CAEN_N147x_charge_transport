from __future__ import annotations

import ctypes
import os
import random
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Callable, Mapping, Sequence

try:
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - dependency-driven
    list_ports = None

MAX_PARAM_NAME = 10

SYSTEM_TYPE_N1470 = 6
LINKTYPE_USB_VCP = 5

USB_VCP_BAUD = 9600
USB_VCP_DATA_BITS = 8
USB_VCP_STOP_BITS = "1"
USB_VCP_PARITY = "None"
USB_VCP_BOARD_NUMBER = 0

USB_VCP_BAUD_OPTIONS = ("300", "600", "1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200")
USB_VCP_DATA_BITS_OPTIONS = ("5", "6", "7", "8")
USB_VCP_STOP_BITS_OPTIONS = ("1", "1.5", "2")
USB_VCP_PARITY_OPTIONS = ("None", "Even", "Odd", "Mark", "Space")

CAEN_TRANSPORT_AUTO = "Auto"
CAEN_TRANSPORT_LIBS = "caen-libs"
CAEN_TRANSPORT_RAW_WRAPPER = "raw wrapper"
CAEN_TRANSPORT_OPTIONS = (
    CAEN_TRANSPORT_AUTO,
    CAEN_TRANSPORT_LIBS,
    CAEN_TRANSPORT_RAW_WRAPPER,
)

CAEN_N1470_COMPATIBILITY_HINT = (
    "N1470/N1470H support requires CAEN HV Wrapper Rel. 5.10+ "
    "(December 2012); prefer 6.x."
)
CAEN_RAW_WRAPPER_HINT = (
    "If caen-libs fails, try Transport = raw wrapper to compare against the DLL path directly."
)

PARAMETER_ALIAS_SETS = {
    "voltage_set": ("V0Set", "VSet", "VSET"),
    "voltage_monitor": ("VMon", "VMON"),
    "current_monitor": ("IMon", "IMON"),
    "power": ("Pw", "PW"),
    "status": ("Status", "STAT"),
    "ramp_up": ("RUp", "RUP"),
    "ramp_down": ("RDW",),
}


@dataclass(frozen=True)
class ChannelDefinition:
    label: str
    channel_index: int
    polarity: str


CHANNEL_DEFINITIONS = (
    ChannelDefinition("C", 0, "-"),
    ChannelDefinition("T1", 1, "-"),
    ChannelDefinition("B1", 2, "+"),
    ChannelDefinition("T2", 3, "+"),
)
CHANNEL_LABELS = tuple(channel.label for channel in CHANNEL_DEFINITIONS)
CHANNEL_BY_LABEL = {channel.label: channel for channel in CHANNEL_DEFINITIONS}


def list_serial_ports() -> list[str]:
    if list_ports is None:
        return []
    return sorted(port.device for port in list_ports.comports())


def _join_sentence_parts(parts: Sequence[str]) -> str:
    cleaned: list[str] = []
    for part in parts:
        text = part.strip()
        if text:
            cleaned.append(text)
    return " ".join(cleaned)


def normalize_usb_vcp_com_port(value: str) -> str:
    port = str(value).strip() or "COM1"
    if port.isdigit():
        return f"COM{port}"
    return port.upper()


def format_caen_connection_error(
    *,
    backend_label: str,
    settings: UsbVcpSettings,
    detail: str,
    code: int | None = None,
    error_text: str | None = None,
    hints: Sequence[str] = (),
) -> str:
    parts = [
        f"Failed to open CAEN N1470 on {settings.com_port} via {backend_label}.",
        f"USB-VCP argument: {settings.build_argument()}.",
        detail.rstrip(".") + ".",
    ]
    if code is not None or error_text:
        caen_details: list[str] = []
        if code is not None:
            caen_details.append(f"code {code}")
        if error_text:
            caen_details.append(str(error_text))
        parts.append(f"CAEN: {'; '.join(caen_details)}.")
    for hint in hints:
        if hint:
            parts.append(hint.rstrip(".") + ".")
    return _join_sentence_parts(parts)


def format_caen_auto_connection_error(
    settings: UsbVcpSettings, attempt_errors: Sequence[tuple[str, str]]
) -> str:
    attempt_summaries = [f"[{transport}] {message}" for transport, message in attempt_errors]
    return _join_sentence_parts(
        [
            (
                "Failed to open CAEN N1470 via Auto after trying "
                f"{CAEN_TRANSPORT_LIBS} then {CAEN_TRANSPORT_RAW_WRAPPER}."
            ),
            f"USB-VCP argument: {settings.build_argument()}.",
            " ".join(attempt_summaries),
            "Use Transport = caen-libs or raw wrapper to compare the two code paths directly.",
            CAEN_N1470_COMPATIBILITY_HINT,
        ]
    )


def format_status_text(status_code: int) -> str:
    return "OK" if status_code == 0 else f"ALARM (0x{status_code:X})"


@dataclass(frozen=True)
class ChannelSnapshot:
    label: str
    channel_index: int
    polarity: str
    vmon_v: float
    imon_na: float
    is_on: bool
    status_code: int
    status_text: str


@dataclass(frozen=True)
class FieldConfig:
    label: str
    e_drift_kv_cm: float
    e_transfer_kv_cm: float
    uv_expected: bool


@dataclass(frozen=True)
class UsbVcpSettings:
    com_port: str
    transport: str = CAEN_TRANSPORT_AUTO
    baud: int = USB_VCP_BAUD
    data_bits: int = USB_VCP_DATA_BITS
    stop_bits: str = USB_VCP_STOP_BITS
    parity: str = USB_VCP_PARITY
    board_number: int = USB_VCP_BOARD_NUMBER

    def build_argument(self) -> str:
        return CAENWrapperInterface.build_usb_vcp_argument(
            self.com_port,
            baud=self.baud,
            data_bits=self.data_bits,
            stop_bits=self.stop_bits,
            parity=self.parity,
            board_number=self.board_number,
        )

    @property
    def normalized_com_port(self) -> str:
        return normalize_usb_vcp_com_port(self.com_port)

    def transport_order(self) -> tuple[str, ...]:
        if self.transport == CAEN_TRANSPORT_AUTO:
            return (CAEN_TRANSPORT_LIBS, CAEN_TRANSPORT_RAW_WRAPPER)
        if self.transport in (CAEN_TRANSPORT_LIBS, CAEN_TRANSPORT_RAW_WRAPPER):
            return (self.transport,)
        raise ValueError(f"Unsupported CAEN transport: {self.transport}")


@dataclass(frozen=True)
class RunPointRecord:
    mode: str
    subscan_label: str
    uv_expected: bool
    point_index: int
    v_thgem1_v: float
    e_drift_kv_cm: float
    e_transfer_kv_cm: float
    timestamp_iso: str
    c_vmon_v: float
    c_imon_na: float
    c_is_on: bool
    c_status_code: int
    c_status_text: str
    t1_vmon_v: float
    t1_imon_na: float
    t1_is_on: bool
    t1_status_code: int
    t1_status_text: str
    b1_vmon_v: float
    b1_imon_na: float
    b1_is_on: bool
    b1_status_code: int
    b1_status_text: str
    t2_vmon_v: float
    t2_imon_na: float
    t2_is_on: bool
    t2_status_code: int
    t2_status_text: str

    @classmethod
    def from_snapshots(
        cls,
        *,
        mode: str,
        subscan_label: str,
        uv_expected: bool,
        point_index: int,
        v_thgem1_v: float,
        e_drift_kv_cm: float,
        e_transfer_kv_cm: float,
        timestamp_iso: str,
        snapshots: Sequence[ChannelSnapshot],
    ) -> "RunPointRecord":
        by_label = {snapshot.label: snapshot for snapshot in snapshots}

        def values(label: str) -> tuple[float, float, bool, int, str]:
            snapshot = by_label[label]
            return (
                snapshot.vmon_v,
                snapshot.imon_na,
                snapshot.is_on,
                snapshot.status_code,
                snapshot.status_text,
            )

        return cls(
            mode=mode,
            subscan_label=subscan_label,
            uv_expected=uv_expected,
            point_index=point_index,
            v_thgem1_v=v_thgem1_v,
            e_drift_kv_cm=e_drift_kv_cm,
            e_transfer_kv_cm=e_transfer_kv_cm,
            timestamp_iso=timestamp_iso,
            c_vmon_v=values("C")[0],
            c_imon_na=values("C")[1],
            c_is_on=values("C")[2],
            c_status_code=values("C")[3],
            c_status_text=values("C")[4],
            t1_vmon_v=values("T1")[0],
            t1_imon_na=values("T1")[1],
            t1_is_on=values("T1")[2],
            t1_status_code=values("T1")[3],
            t1_status_text=values("T1")[4],
            b1_vmon_v=values("B1")[0],
            b1_imon_na=values("B1")[1],
            b1_is_on=values("B1")[2],
            b1_status_code=values("B1")[3],
            b1_status_text=values("B1")[4],
            t2_vmon_v=values("T2")[0],
            t2_imon_na=values("T2")[1],
            t2_is_on=values("T2")[2],
            t2_status_code=values("T2")[3],
            t2_status_text=values("T2")[4],
        )

    @classmethod
    def csv_fieldnames(cls) -> list[str]:
        return [field.name for field in fields(cls)]

    def to_csv_row(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.csv_fieldnames()}

    def channel_snapshots(self) -> dict[str, ChannelSnapshot]:
        return {
            "C": ChannelSnapshot(
                "C",
                CHANNEL_BY_LABEL["C"].channel_index,
                CHANNEL_BY_LABEL["C"].polarity,
                self.c_vmon_v,
                self.c_imon_na,
                self.c_is_on,
                self.c_status_code,
                self.c_status_text,
            ),
            "T1": ChannelSnapshot(
                "T1",
                CHANNEL_BY_LABEL["T1"].channel_index,
                CHANNEL_BY_LABEL["T1"].polarity,
                self.t1_vmon_v,
                self.t1_imon_na,
                self.t1_is_on,
                self.t1_status_code,
                self.t1_status_text,
            ),
            "B1": ChannelSnapshot(
                "B1",
                CHANNEL_BY_LABEL["B1"].channel_index,
                CHANNEL_BY_LABEL["B1"].polarity,
                self.b1_vmon_v,
                self.b1_imon_na,
                self.b1_is_on,
                self.b1_status_code,
                self.b1_status_text,
            ),
            "T2": ChannelSnapshot(
                "T2",
                CHANNEL_BY_LABEL["T2"].channel_index,
                CHANNEL_BY_LABEL["T2"].polarity,
                self.t2_vmon_v,
                self.t2_imon_na,
                self.t2_is_on,
                self.t2_status_code,
                self.t2_status_text,
            ),
        }


class BaseCaenInterface(ABC):
    @abstractmethod
    def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def read_all_channels(self) -> list[ChannelSnapshot]:
        raise NotImplementedError

    @abstractmethod
    def set_ramp_rates(self, ramp_up_v_s: float, ramp_down_v_s: float) -> None:
        raise NotImplementedError

    @abstractmethod
    def set_channel_voltages(self, voltages_by_label: Mapping[str, float]) -> None:
        raise NotImplementedError

    @abstractmethod
    def power_on_channels(self, labels: Sequence[str]) -> None:
        raise NotImplementedError

    @abstractmethod
    def power_off_channels(self, labels: Sequence[str]) -> None:
        raise NotImplementedError

    @abstractmethod
    def safe_shutdown(self, labels: Sequence[str]) -> None:
        raise NotImplementedError

    def set_measurement_context(self, field_config: FieldConfig, v_thgem1_v: float) -> None:
        del field_config, v_thgem1_v

    def connection_name(self) -> str:
        return self.__class__.__name__


class SimulationInterface(BaseCaenInterface):
    def __init__(self, seed: int | None = None) -> None:
        self._random = random.Random(seed)
        self._connected = False
        self._ramp_up_v_s = 300.0
        self._ramp_down_v_s = 300.0
        self._context = {
            "uv_expected": True,
            "e_drift_kv_cm": 0.0,
            "e_transfer_kv_cm": 0.0,
            "v_thgem1_v": 150.0,
        }
        self._channel_state = {
            label: {"voltage_v": 1.0, "is_on": False, "status_code": 0}
            for label in CHANNEL_LABELS
        }

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def connection_name(self) -> str:
        return "Simulation"

    def set_measurement_context(self, field_config: FieldConfig, v_thgem1_v: float) -> None:
        self._context = {
            "uv_expected": field_config.uv_expected,
            "e_drift_kv_cm": field_config.e_drift_kv_cm,
            "e_transfer_kv_cm": field_config.e_transfer_kv_cm,
            "v_thgem1_v": v_thgem1_v,
        }

    def read_all_channels(self) -> list[ChannelSnapshot]:
        self._ensure_connected()
        snapshots: list[ChannelSnapshot] = []
        for channel in CHANNEL_DEFINITIONS:
            state = self._channel_state[channel.label]
            base_voltage = state["voltage_v"] if state["is_on"] else 0.0
            measured_voltage = max(0.0, base_voltage + self._random.gauss(0.0, 0.02))
            measured_current = self._simulate_current(channel)
            status_code = int(state["status_code"])
            snapshots.append(
                ChannelSnapshot(
                    label=channel.label,
                    channel_index=channel.channel_index,
                    polarity=channel.polarity,
                    vmon_v=measured_voltage,
                    imon_na=measured_current,
                    is_on=bool(state["is_on"]),
                    status_code=status_code,
                    status_text=format_status_text(status_code),
                )
            )
        return snapshots

    def set_ramp_rates(self, ramp_up_v_s: float, ramp_down_v_s: float) -> None:
        self._ensure_connected()
        self._ramp_up_v_s = float(ramp_up_v_s)
        self._ramp_down_v_s = float(ramp_down_v_s)

    def set_channel_voltages(self, voltages_by_label: Mapping[str, float]) -> None:
        self._ensure_connected()
        for label, voltage_v in voltages_by_label.items():
            self._channel_state[label]["voltage_v"] = float(voltage_v)

    def power_on_channels(self, labels: Sequence[str]) -> None:
        self._ensure_connected()
        for label in labels:
            self._channel_state[label]["is_on"] = True

    def power_off_channels(self, labels: Sequence[str]) -> None:
        self._ensure_connected()
        for label in labels:
            self._channel_state[label]["is_on"] = False

    def safe_shutdown(self, labels: Sequence[str]) -> None:
        self._ensure_connected()
        for label in labels:
            self._channel_state[label]["voltage_v"] = 1.0

    def _ensure_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("Simulation backend is not connected.")

    def _simulate_current(self, channel: ChannelDefinition) -> float:
        state = self._channel_state[channel.label]
        if not state["is_on"]:
            return 0.0

        v_thgem1_v = max(
            self._context["v_thgem1_v"],
            self._channel_state["T1"]["voltage_v"] + self._channel_state["B1"]["voltage_v"],
        )
        e_drift = float(self._context["e_drift_kv_cm"])
        e_transfer = float(self._context["e_transfer_kv_cm"])
        uv_factor = 1.0 if self._context["uv_expected"] else 0.06

        collection_component = max(0.0, (v_thgem1_v - 150.0) / 275.0)
        avalanche_component = max(0.0, (v_thgem1_v - 700.0) / 250.0)
        transfer_enhancement = 1.0 + 0.18 * max(e_transfer, 0.0) + 0.08 * abs(e_transfer)
        drift_enhancement = 1.0 + 0.14 * max(e_drift, 0.0)

        base_signal = uv_factor * (
            0.08
            + 0.42 * collection_component
            + 0.95 * avalanche_component * avalanche_component
        )

        magnitudes = {
            "C": base_signal * (0.32 + 0.22 * drift_enhancement),
            "T1": base_signal * 0.78,
            "B1": base_signal * 0.62 * transfer_enhancement,
            "T2": base_signal * (0.30 + 0.16 * transfer_enhancement),
        }

        noise = self._random.gauss(0.0, 0.02 + 0.04 * magnitudes[channel.label])
        magnitude = max(0.0, magnitudes[channel.label] + noise)
        sign = -1.0 if channel.polarity == "-" else 1.0
        return sign * magnitude


class CAENWrapperInterface(BaseCaenInterface):
    def __init__(
        self,
        com_port: str | None = None,
        *,
        settings: UsbVcpSettings | None = None,
        dll_path: str | Path | None = None,
    ) -> None:
        if settings is None:
            if not com_port:
                raise ValueError("A COM port is required for the CAEN USB-VCP backend.")
            settings = UsbVcpSettings(com_port=com_port)
        self.settings = settings
        self.com_port = settings.com_port
        self.dll_path = Path(dll_path) if dll_path else None
        self._lib: ctypes.CDLL | None = None
        self._handle: int | None = None
        self._slot_index: int | None = None
        self._parameter_names: dict[str, str] = {}
        self._connected = False

    @staticmethod
    def build_usb_vcp_argument(
        com_port: str,
        *,
        baud: int = USB_VCP_BAUD,
        data_bits: int = USB_VCP_DATA_BITS,
        stop_bits: str = USB_VCP_STOP_BITS,
        parity: str = USB_VCP_PARITY,
        board_number: int = USB_VCP_BOARD_NUMBER,
    ) -> str:
        return (
            f"{normalize_usb_vcp_com_port(com_port)}_{int(baud)}_{int(data_bits)}_"
            f"{str(stop_bits)}_{str(parity).lower()}_{int(board_number)}"
        )

    @staticmethod
    def resolve_parameter_names(parameter_names: Sequence[str]) -> dict[str, str]:
        resolved: dict[str, str] = {}
        by_lower = {name.lower(): name for name in parameter_names}
        missing: list[str] = []

        for semantic_name, aliases in PARAMETER_ALIAS_SETS.items():
            matched = next((by_lower.get(alias.lower()) for alias in aliases if alias.lower() in by_lower), None)
            if matched is None:
                missing.append(semantic_name)
                continue
            resolved[semantic_name] = matched

        if missing:
            missing_list = ", ".join(missing)
            raise RuntimeError(f"Missing required CAEN parameters: {missing_list}")

        return resolved

    def connect(self) -> None:
        if os.name != "nt":
            raise RuntimeError("CAEN USB-VCP is only supported on Windows.")

        self._lib = self._load_library()
        self._configure_library(self._lib)

        handle = ctypes.c_int(-1)
        argument_text = self.settings.build_argument()
        argument = argument_text.encode("ascii")
        result = self._lib.CAENHV_InitSystem(
            SYSTEM_TYPE_N1470,
            LINKTYPE_USB_VCP,
            ctypes.c_char_p(argument),
            ctypes.c_char_p(b""),
            ctypes.c_char_p(b""),
            ctypes.byref(handle),
        )
        self._handle = int(handle.value)
        if result != 0:
            error_text = self._error_text()
            self._reset_connection_state()
            raise RuntimeError(
                format_caen_connection_error(
                    backend_label=CAEN_TRANSPORT_RAW_WRAPPER,
                    settings=self.settings,
                    detail="CAENHV_InitSystem failed",
                    code=result,
                    error_text=error_text,
                    hints=(CAEN_N1470_COMPATIBILITY_HINT,),
                )
            )

        self._slot_index = self._resolve_slot_index()
        raw_parameter_names = self._read_parameter_names(self._slot_index, 0)
        self._parameter_names = self.resolve_parameter_names(raw_parameter_names)
        self._connected = True

    def connection_name(self) -> str:
        return "CAEN USB-VCP via raw wrapper"

    def disconnect(self) -> None:
        if not self._lib or self._handle is None:
            self._reset_connection_state()
            return

        try:
            self._lib.CAENHV_DeinitSystem(self._handle)
        finally:
            self._reset_connection_state()

    def read_all_channels(self) -> list[ChannelSnapshot]:
        self._ensure_connected()
        voltages = self._read_float_param("voltage_monitor")
        currents = self._read_float_param("current_monitor")
        powers = self._read_ulong_param("power")
        statuses = self._read_ulong_param("status")
        return self.build_snapshots(voltages, currents, powers, statuses)

    def set_ramp_rates(self, ramp_up_v_s: float, ramp_down_v_s: float) -> None:
        self._ensure_connected()
        for label in CHANNEL_LABELS:
            channel_index = CHANNEL_BY_LABEL[label].channel_index
            self._set_float_param(channel_index, "ramp_up", ramp_up_v_s)
            self._set_float_param(channel_index, "ramp_down", ramp_down_v_s)

    def set_channel_voltages(self, voltages_by_label: Mapping[str, float]) -> None:
        self._ensure_connected()
        for label, voltage_v in voltages_by_label.items():
            channel_index = CHANNEL_BY_LABEL[label].channel_index
            self._set_float_param(channel_index, "voltage_set", float(voltage_v))

    def power_on_channels(self, labels: Sequence[str]) -> None:
        self._ensure_connected()
        for label in labels:
            self._set_ulong_param(CHANNEL_BY_LABEL[label].channel_index, "power", 1)

    def power_off_channels(self, labels: Sequence[str]) -> None:
        self._ensure_connected()
        for label in labels:
            self._set_ulong_param(CHANNEL_BY_LABEL[label].channel_index, "power", 0)

    def safe_shutdown(self, labels: Sequence[str]) -> None:
        self._ensure_connected()
        safe_values = {label: 1.0 for label in labels}
        self.set_channel_voltages(safe_values)

    def _load_library(self) -> ctypes.CDLL:
        candidate_paths: list[str] = []
        if self.dll_path is not None:
            candidate_paths.append(str(self.dll_path))
        if getattr(sys, "frozen", False):  # bundled exe: DLL dropped beside it
            candidate_paths.append(str(Path(sys.executable).resolve().with_name("CAENHVWrapper.dll")))
        candidate_paths.append(str(Path(__file__).resolve().with_name("CAENHVWrapper.dll")))
        candidate_paths.append("CAENHVWrapper.dll")

        last_error: OSError | None = None
        for candidate in candidate_paths:
            try:
                return ctypes.CDLL(candidate)
            except OSError as exc:
                last_error = exc

        message = "Unable to load CAENHVWrapper.dll. Place it beside main.py or on PATH."
        if last_error is not None:
            message = f"{message} ({last_error})"
        raise RuntimeError(message)

    def _configure_library(self, library: ctypes.CDLL) -> None:
        library.CAENHV_InitSystem.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_int),
        ]
        library.CAENHV_InitSystem.restype = ctypes.c_int
        library.CAENHV_DeinitSystem.argtypes = [ctypes.c_int]
        library.CAENHV_DeinitSystem.restype = ctypes.c_int
        library.CAENHV_GetCrateMap.argtypes = [
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_ushort),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        library.CAENHV_GetCrateMap.restype = ctypes.c_int
        library.CAENHV_GetChParamInfo.argtypes = [
            ctypes.c_int,
            ctypes.c_ushort,
            ctypes.c_ushort,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_int),
        ]
        library.CAENHV_GetChParamInfo.restype = ctypes.c_int
        library.CAENHV_GetChParam.argtypes = [
            ctypes.c_int,
            ctypes.c_ushort,
            ctypes.c_char_p,
            ctypes.c_ushort,
            ctypes.POINTER(ctypes.c_ushort),
            ctypes.c_void_p,
        ]
        library.CAENHV_GetChParam.restype = ctypes.c_int
        library.CAENHV_SetChParam.argtypes = [
            ctypes.c_int,
            ctypes.c_ushort,
            ctypes.c_char_p,
            ctypes.c_ushort,
            ctypes.POINTER(ctypes.c_ushort),
            ctypes.c_void_p,
        ]
        library.CAENHV_SetChParam.restype = ctypes.c_int
        library.CAENHV_GetError.argtypes = [ctypes.c_int]
        library.CAENHV_GetError.restype = ctypes.c_char_p
        library.CAENHV_Free.argtypes = [ctypes.c_void_p]
        library.CAENHV_Free.restype = ctypes.c_int

    def _resolve_slot_index(self) -> int:
        self._ensure_handle()
        nr_of_slots = ctypes.c_ushort()
        nrof_ch_list = ctypes.c_void_p()
        model_list = ctypes.c_void_p()
        description_list = ctypes.c_void_p()
        serial_list = ctypes.c_void_p()
        fw_min_list = ctypes.c_void_p()
        fw_max_list = ctypes.c_void_p()

        result = self._lib.CAENHV_GetCrateMap(
            self._handle,
            ctypes.byref(nr_of_slots),
            ctypes.byref(nrof_ch_list),
            ctypes.byref(model_list),
            ctypes.byref(description_list),
            ctypes.byref(serial_list),
            ctypes.byref(fw_min_list),
            ctypes.byref(fw_max_list),
        )
        self._raise_on_error(result, "CAENHV_GetCrateMap failed")

        try:
            counts = self._read_ushort_buffer(nrof_ch_list.value, nr_of_slots.value)
            for slot_index, channel_count in enumerate(counts):
                if channel_count >= len(CHANNEL_DEFINITIONS):
                    return slot_index
        finally:
            self._free_pointer(nrof_ch_list.value)
            self._free_pointer(model_list.value)
            self._free_pointer(description_list.value)
            self._free_pointer(serial_list.value)
            self._free_pointer(fw_min_list.value)
            self._free_pointer(fw_max_list.value)

        raise RuntimeError("No CAEN board with at least four channels was found.")

    def _read_parameter_names(self, slot_index: int, channel_index: int) -> list[str]:
        self._ensure_handle()
        raw_pointer = ctypes.c_void_p()
        parameter_count = ctypes.c_int()
        result = self._lib.CAENHV_GetChParamInfo(
            self._handle,
            slot_index,
            channel_index,
            ctypes.byref(raw_pointer),
            ctypes.byref(parameter_count),
        )
        self._raise_on_error(result, "CAENHV_GetChParamInfo failed")
        try:
            return self._read_fixed_width_strings(raw_pointer.value, parameter_count.value, MAX_PARAM_NAME)
        finally:
            self._free_pointer(raw_pointer.value)

    def _read_float_param(self, semantic_name: str) -> list[float]:
        raw_values = (ctypes.c_float * len(CHANNEL_DEFINITIONS))()
        self._read_param_into_buffer(semantic_name, raw_values)
        return [float(value) for value in raw_values]

    def _read_ulong_param(self, semantic_name: str) -> list[int]:
        raw_values = (ctypes.c_ulong * len(CHANNEL_DEFINITIONS))()
        self._read_param_into_buffer(semantic_name, raw_values)
        return [int(value) for value in raw_values]

    def _read_param_into_buffer(self, semantic_name: str, buffer: ctypes.Array) -> None:
        self._ensure_connected()
        channel_ids = (ctypes.c_ushort * len(CHANNEL_DEFINITIONS))(
            *[channel.channel_index for channel in CHANNEL_DEFINITIONS]
        )
        result = self._lib.CAENHV_GetChParam(
            self._handle,
            self._slot_index,
            ctypes.c_char_p(self._parameter_names[semantic_name].encode("ascii")),
            len(CHANNEL_DEFINITIONS),
            channel_ids,
            ctypes.cast(buffer, ctypes.c_void_p),
        )
        self._raise_on_error(result, f"CAENHV_GetChParam failed for {semantic_name}")

    def _set_float_param(self, channel_index: int, semantic_name: str, value: float) -> None:
        payload = ctypes.c_float(float(value))
        self._set_scalar_param(channel_index, semantic_name, ctypes.byref(payload))

    def _set_ulong_param(self, channel_index: int, semantic_name: str, value: int) -> None:
        payload = ctypes.c_ulong(int(value))
        self._set_scalar_param(channel_index, semantic_name, ctypes.byref(payload))

    def _set_scalar_param(self, channel_index: int, semantic_name: str, payload: ctypes.c_void_p) -> None:
        self._ensure_connected()
        channel_ids = (ctypes.c_ushort * 1)(channel_index)
        result = self._lib.CAENHV_SetChParam(
            self._handle,
            self._slot_index,
            ctypes.c_char_p(self._parameter_names[semantic_name].encode("ascii")),
            1,
            channel_ids,
            payload,
        )
        self._raise_on_error(result, f"CAENHV_SetChParam failed for {semantic_name}")

    def _read_fixed_width_strings(self, pointer_value: int | None, count: int, width: int) -> list[str]:
        if not pointer_value or count <= 0:
            return []
        buffer = ctypes.string_at(pointer_value, count * width)
        names: list[str] = []
        for index in range(count):
            chunk = buffer[index * width : (index + 1) * width]
            cleaned = chunk.split(b"\x00", 1)[0].decode("ascii", errors="ignore").strip()
            if cleaned:
                names.append(cleaned)
        return names

    def _read_ushort_buffer(self, pointer_value: int | None, count: int) -> list[int]:
        if not pointer_value or count <= 0:
            return []
        array_type = ctypes.c_ushort * count
        return [int(value) for value in ctypes.cast(pointer_value, ctypes.POINTER(array_type)).contents]

    def _free_pointer(self, pointer_value: int | None) -> None:
        if self._lib is None or not pointer_value:
            return
        self._lib.CAENHV_Free(ctypes.c_void_p(pointer_value))

    def _raise_on_error(self, result: int, action: str) -> None:
        if result == 0:
            return
        raise RuntimeError(f"{action}: {self._error_text()} (code {result})")

    def _error_text(self) -> str:
        if self._lib is None or self._handle is None:
            return "Unknown CAEN error"
        raw_error = self._lib.CAENHV_GetError(self._handle)
        if not raw_error:
            return "Unknown CAEN error"
        return raw_error.decode("utf-8", errors="replace")

    def _ensure_handle(self) -> None:
        if self._lib is None or self._handle is None:
            raise RuntimeError("CAEN library is not initialized.")

    def _ensure_connected(self) -> None:
        self._ensure_handle()
        if not self._connected or self._slot_index is None:
            raise RuntimeError("CAEN backend is not connected.")

    def _reset_connection_state(self) -> None:
        self._lib = None
        self._handle = None
        self._slot_index = None
        self._parameter_names = {}
        self._connected = False

    def build_snapshots(
        self,
        voltages_v: Sequence[float],
        currents_na: Sequence[float],
        powers: Sequence[int],
        statuses: Sequence[int],
    ) -> list[ChannelSnapshot]:
        snapshots: list[ChannelSnapshot] = []
        for index, channel in enumerate(CHANNEL_DEFINITIONS):
            status_code = int(statuses[index])
            snapshots.append(
                ChannelSnapshot(
                    label=channel.label,
                    channel_index=channel.channel_index,
                    polarity=channel.polarity,
                    vmon_v=float(abs(voltages_v[index])),
                    imon_na=float(currents_na[index]),
                    is_on=bool(powers[index]),
                    status_code=status_code,
                    status_text=format_status_text(status_code),
                )
            )
        return snapshots


class CaenLibsInterface(BaseCaenInterface):
    """CAEN N1470 over USB-VCP using the official ``caen-libs`` (CAEN HV Wrapper)
    Python bindings.

    Same parameter model as :class:`CAENWrapperInterface` (it reuses the static
    helpers below), but talks to the device through ``caen-libs`` instead of a
    hand-rolled ctypes wrapper. ``caen-libs`` loads the native CAEN HV Wrapper
    library at import time, so it is imported lazily inside :meth:`connect` — the
    rest of the app, the Simulation backend, and the tests stay importable on
    machines without the CAEN driver installed.
    """

    def __init__(self, com_port: str | None = None, *, settings: UsbVcpSettings | None = None) -> None:
        if settings is None:
            if not com_port:
                raise ValueError("A COM port is required for the CAEN USB-VCP backend.")
            settings = UsbVcpSettings(com_port=com_port)
        self.settings = settings
        self.com_port = settings.com_port
        self._device = None
        self._slot: int | None = None
        self._parameter_names: dict[str, str] = {}
        self._connected = False

    def connect(self) -> None:
        try:
            from caen_libs import caenhvwrapper as hv
        except Exception as exc:  # pragma: no cover - driver-dependent
            raise RuntimeError(
                format_caen_connection_error(
                    backend_label=CAEN_TRANSPORT_LIBS,
                    settings=self.settings,
                    detail="The CAEN HV Wrapper library / caen-libs is not available",
                    error_text=str(exc),
                    hints=(CAEN_RAW_WRAPPER_HINT, CAEN_N1470_COMPATIBILITY_HINT),
                )
            ) from exc

        argument = self.settings.build_argument()
        try:
            self._device = hv.Device.open(
                hv.SystemType.N1470, hv.LinkType.USB_VCP, argument, "", ""
            )
        except Exception as exc:  # pragma: no cover - hardware-dependent
            self._device = None
            raise RuntimeError(
                format_caen_connection_error(
                    backend_label=CAEN_TRANSPORT_LIBS,
                    settings=self.settings,
                    detail="Device.open failed while opening SystemType.N1470 over USB_VCP",
                    error_text=str(exc),
                    hints=(CAEN_RAW_WRAPPER_HINT, CAEN_N1470_COMPATIBILITY_HINT),
                )
            ) from exc

        try:
            self._slot = self._resolve_slot()
            raw_names = self._device.get_ch_param_info(self._slot, 0)
            self._parameter_names = CAENWrapperInterface.resolve_parameter_names(list(raw_names))
            self._connected = True
        except Exception as exc:  # pragma: no cover - hardware-dependent
            self.disconnect()
            raise RuntimeError(
                format_caen_connection_error(
                    backend_label=CAEN_TRANSPORT_LIBS,
                    settings=self.settings,
                    detail="The device opened but channel metadata could not be read",
                    error_text=str(exc),
                    hints=(CAEN_RAW_WRAPPER_HINT, CAEN_N1470_COMPATIBILITY_HINT),
                )
            ) from exc

    def disconnect(self) -> None:
        device, self._device = self._device, None
        self._slot = None
        self._connected = False
        if device is not None:
            try:
                device.close()
            except Exception:  # pragma: no cover - hardware-dependent
                pass

    def connection_name(self) -> str:
        return "CAEN USB-VCP via caen-libs"

    def read_all_channels(self) -> list[ChannelSnapshot]:
        self._ensure_connected()
        channels = self._channel_indices()
        voltages = self._get_floats("voltage_monitor", channels)
        currents = self._get_floats("current_monitor", channels)
        powers = self._get_ints("power", channels)
        statuses = self._get_ints("status", channels)
        # build_snapshots is self-independent; reuse the CAENWrapperInterface impl.
        return CAENWrapperInterface.build_snapshots(self, voltages, currents, powers, statuses)

    def set_ramp_rates(self, ramp_up_v_s: float, ramp_down_v_s: float) -> None:
        self._ensure_connected()
        channels = self._channel_indices()
        self._device.set_ch_param(self._slot, channels, self._parameter_names["ramp_up"], float(ramp_up_v_s))
        self._device.set_ch_param(self._slot, channels, self._parameter_names["ramp_down"], float(ramp_down_v_s))

    def set_channel_voltages(self, voltages_by_label: Mapping[str, float]) -> None:
        self._ensure_connected()
        name = self._parameter_names["voltage_set"]
        for label, voltage_v in voltages_by_label.items():
            channel_index = CHANNEL_BY_LABEL[label].channel_index
            self._device.set_ch_param(self._slot, [channel_index], name, float(voltage_v))

    def power_on_channels(self, labels: Sequence[str]) -> None:
        self._set_power(labels, 1)

    def power_off_channels(self, labels: Sequence[str]) -> None:
        self._set_power(labels, 0)

    def safe_shutdown(self, labels: Sequence[str]) -> None:
        self._ensure_connected()
        self.set_channel_voltages({label: 1.0 for label in labels})

    # ── helpers ──────────────────────────────────────────────────────────────
    def _resolve_slot(self) -> int:
        for board in self._device.get_crate_map():
            if board is not None:
                return int(board.slot)
        raise RuntimeError("No CAEN board found in the crate map.")

    def _ensure_connected(self) -> None:
        if not self._connected or self._device is None or self._slot is None:
            raise RuntimeError("CAEN device is not connected.")

    @staticmethod
    def _channel_indices() -> list[int]:
        return [channel.channel_index for channel in CHANNEL_DEFINITIONS]

    def _get_floats(self, semantic: str, channels: Sequence[int]) -> list[float]:
        values = self._device.get_ch_param(self._slot, list(channels), self._parameter_names[semantic])
        return [float(v) for v in values]

    def _get_ints(self, semantic: str, channels: Sequence[int]) -> list[int]:
        values = self._device.get_ch_param(self._slot, list(channels), self._parameter_names[semantic])
        return [int(v) for v in values]

    def _set_power(self, labels: Sequence[str], value: int) -> None:
        self._ensure_connected()
        channels = [CHANNEL_BY_LABEL[label].channel_index for label in labels]
        if channels:
            self._device.set_ch_param(self._slot, channels, self._parameter_names["power"], value)


class CaenUsbVcpInterface(BaseCaenInterface):
    def __init__(
        self,
        settings: UsbVcpSettings,
        *,
        backend_factories: Mapping[str, Callable[[], BaseCaenInterface]] | None = None,
    ) -> None:
        self.settings = settings
        self._backend_factories = dict(
            backend_factories
            or {
                CAEN_TRANSPORT_LIBS: lambda: CaenLibsInterface(settings=settings),
                CAEN_TRANSPORT_RAW_WRAPPER: lambda: CAENWrapperInterface(settings=settings),
            }
        )
        self._backend: BaseCaenInterface | None = None
        self._transport_in_use: str | None = None

    def connect(self) -> None:
        attempt_errors: list[tuple[str, str]] = []
        for transport in self.settings.transport_order():
            backend = self._make_backend(transport)
            try:
                backend.connect()
            except Exception as exc:  # pragma: no cover - hardware-dependent
                attempt_errors.append((transport, str(exc)))
                try:
                    backend.disconnect()
                except Exception:
                    pass
                continue
            self._backend = backend
            self._transport_in_use = transport
            return

        if self.settings.transport == CAEN_TRANSPORT_AUTO:
            raise RuntimeError(format_caen_auto_connection_error(self.settings, attempt_errors))
        if attempt_errors:
            raise RuntimeError(attempt_errors[0][1])
        raise RuntimeError("No CAEN backend attempt was made.")

    def disconnect(self) -> None:
        backend, self._backend = self._backend, None
        self._transport_in_use = None
        if backend is not None:
            backend.disconnect()

    def connection_name(self) -> str:
        if self._backend is not None:
            return self._backend.connection_name()
        return "CAEN USB-VCP"

    def read_all_channels(self) -> list[ChannelSnapshot]:
        return self._require_backend().read_all_channels()

    def set_ramp_rates(self, ramp_up_v_s: float, ramp_down_v_s: float) -> None:
        self._require_backend().set_ramp_rates(ramp_up_v_s, ramp_down_v_s)

    def set_channel_voltages(self, voltages_by_label: Mapping[str, float]) -> None:
        self._require_backend().set_channel_voltages(voltages_by_label)

    def power_on_channels(self, labels: Sequence[str]) -> None:
        self._require_backend().power_on_channels(labels)

    def power_off_channels(self, labels: Sequence[str]) -> None:
        self._require_backend().power_off_channels(labels)

    def safe_shutdown(self, labels: Sequence[str]) -> None:
        self._require_backend().safe_shutdown(labels)

    def set_measurement_context(self, field_config: FieldConfig, v_thgem1_v: float) -> None:
        self._require_backend().set_measurement_context(field_config, v_thgem1_v)

    def _make_backend(self, transport: str) -> BaseCaenInterface:
        try:
            factory = self._backend_factories[transport]
        except KeyError as exc:
            raise RuntimeError(f"Unsupported CAEN transport: {transport}") from exc
        return factory()

    def _require_backend(self) -> BaseCaenInterface:
        if self._backend is None:
            raise RuntimeError("CAEN USB-VCP backend is not connected.")
        return self._backend
