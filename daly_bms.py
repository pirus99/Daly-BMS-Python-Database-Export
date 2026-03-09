"""
Daly BMS RS-485 communication module.

Implements the Daly BMS serial protocol over USB-to-RS485, supporting all
standard data-request commands (0x90 – 0x98).

Protocol overview
-----------------
Request frame  (13 bytes):
  0xA5  [ADDR]  [CMD]  0x08  0x00 0x00 0x00 0x00 0x00 0x00 0x00 0x00  [CHKSUM]

Response frame (variable length):
  0xA5  [ADDR]  [CMD]  [LEN]  <LEN data bytes>  [CHKSUM]

CHKSUM = (sum of all preceding bytes) & 0xFF
"""

import logging
import struct
import time
from dataclasses import dataclass, field
from typing import List, Optional

import serial

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass
class BasicStatus:
    """Data returned by command 0x90."""
    pack_voltage: float = 0.0          # V
    acquisition_voltage: float = 0.0   # V  (measurement at terminals)
    pack_current: float = 0.0          # A  (+ve = charging, -ve = discharging)
    soc_percent: float = 0.0           # %


@dataclass
class CellVoltageExtremes:
    """Data returned by command 0x91."""
    max_voltage: float = 0.0     # V
    max_cell_number: int = 0
    min_voltage: float = 0.0     # V
    min_cell_number: int = 0


@dataclass
class TemperatureExtremes:
    """Data returned by command 0x92."""
    max_temperature: float = 0.0    # °C
    max_sensor_number: int = 0
    min_temperature: float = 0.0    # °C
    min_sensor_number: int = 0


@dataclass
class MosStatus:
    """Data returned by command 0x93."""
    charge_mos_on: bool = False
    discharge_mos_on: bool = False
    bms_heartbeat: int = 0          # increments with every BMS communication
    remaining_capacity_ah: float = 0.0  # Ah


@dataclass
class StatusInfo:
    """Data returned by command 0x94."""
    cell_count: int = 0
    temperature_sensor_count: int = 0
    charger_connected: bool = False
    load_connected: bool = False
    states: int = 0                 # raw state byte (DIO bits)
    cycles: int = 0


@dataclass
class FailureFlags:
    """Data returned by command 0x98.

    Each boolean attribute corresponds to one alarm/protection/fault bit in
    the seven data bytes of the BMS failure-flags response.
    """
    # Byte 0 – voltage alarms
    cell_overvoltage_alarm_l1: bool = False
    cell_overvoltage_alarm_l2: bool = False
    cell_undervoltage_alarm_l1: bool = False
    cell_undervoltage_alarm_l2: bool = False
    pack_overvoltage_alarm_l1: bool = False
    pack_overvoltage_alarm_l2: bool = False
    pack_undervoltage_alarm_l1: bool = False
    pack_undervoltage_alarm_l2: bool = False

    # Byte 1 – temperature alarms
    charge_overtemp_alarm_l1: bool = False
    charge_overtemp_alarm_l2: bool = False
    charge_undertemp_alarm_l1: bool = False
    charge_undertemp_alarm_l2: bool = False
    discharge_overtemp_alarm_l1: bool = False
    discharge_overtemp_alarm_l2: bool = False
    discharge_undertemp_alarm_l1: bool = False
    discharge_undertemp_alarm_l2: bool = False

    # Byte 2 – current / SOC alarms
    charge_overcurrent_alarm_l1: bool = False
    charge_overcurrent_alarm_l2: bool = False
    discharge_overcurrent_alarm_l1: bool = False
    discharge_overcurrent_alarm_l2: bool = False
    soc_low_alarm_l1: bool = False
    soc_low_alarm_l2: bool = False
    cell_voltage_diff_alarm_l1: bool = False
    cell_voltage_diff_alarm_l2: bool = False

    # Byte 3 – MOS temperature alarms
    cell_temp_diff_alarm_l1: bool = False
    cell_temp_diff_alarm_l2: bool = False
    charge_mos_overtemp_alarm_l1: bool = False
    charge_mos_overtemp_alarm_l2: bool = False
    discharge_mos_overtemp_alarm_l1: bool = False
    discharge_mos_overtemp_alarm_l2: bool = False

    # Byte 4 – hardware faults
    charge_mos_fault: bool = False
    discharge_mos_fault: bool = False
    temp_sensor_fault: bool = False
    cell_fault: bool = False
    sampling_circuit_fault: bool = False
    cell_balance_fault: bool = False


@dataclass
class DalyBMSData:
    """Aggregated snapshot of all BMS readings."""
    basic: Optional[BasicStatus] = None
    cell_extremes: Optional[CellVoltageExtremes] = None
    temp_extremes: Optional[TemperatureExtremes] = None
    mos: Optional[MosStatus] = None
    status: Optional[StatusInfo] = None
    cell_voltages: List[float] = field(default_factory=list)   # V, indexed from 0
    temperatures: List[float] = field(default_factory=list)    # °C, indexed from 0
    cell_balance_active: List[bool] = field(default_factory=list)
    failures: Optional[FailureFlags] = None


# ---------------------------------------------------------------------------
# Communication class
# ---------------------------------------------------------------------------

# Frame constants
_START_BYTE = 0xA5
_REQUEST_DATA_LENGTH = 0x08   # always 8 for requests


class DalyBMSError(Exception):
    """Raised when a BMS communication error occurs."""


class DalyBMS:
    """Communicates with a Daly BMS over a serial RS-485 link."""

    # Command codes
    CMD_BASIC_STATUS = 0x90
    CMD_CELL_VOLTAGE_EXTREMES = 0x91
    CMD_TEMPERATURE_EXTREMES = 0x92
    CMD_MOS_STATUS = 0x93
    CMD_STATUS_INFO = 0x94
    CMD_CELL_VOLTAGES = 0x95
    CMD_TEMPERATURES = 0x96
    CMD_BALANCE_STATUS = 0x97
    CMD_FAILURE_FLAGS = 0x98

    def __init__(
        self,
        port: str,
        baud_rate: int = 9600,
        address: int = 0x40,
        timeout: float = 1.0,
    ) -> None:
        self.port = port
        self.baud_rate = baud_rate
        self.address = address
        self.timeout = timeout
        self._serial: Optional[serial.Serial] = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Open the serial port."""
        if self._serial and self._serial.is_open:
            return
        try:
            self._serial = serial.Serial(
                port=self.port,
                baudrate=self.baud_rate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.timeout,
            )
            logger.info("Opened serial port %s at %d baud", self.port, self.baud_rate)
        except serial.SerialException as exc:
            raise DalyBMSError(f"Cannot open {self.port}: {exc}") from exc

    def disconnect(self) -> None:
        """Close the serial port if it is open."""
        if self._serial and self._serial.is_open:
            self._serial.close()
            logger.info("Closed serial port %s", self.port)

    def __enter__(self) -> "DalyBMS":
        self.connect()
        return self

    def __exit__(self, *_) -> None:
        self.disconnect()

    # ------------------------------------------------------------------
    # Low-level framing helpers
    # ------------------------------------------------------------------

    def _build_request(self, command: int) -> bytes:
        """Build a 13-byte request frame for the given command."""
        frame = bytearray([
            _START_BYTE,
            self.address,
            command,
            _REQUEST_DATA_LENGTH,
            0x00, 0x00, 0x00, 0x00,
            0x00, 0x00, 0x00, 0x00,
        ])
        frame.append(sum(frame) & 0xFF)
        return bytes(frame)

    @staticmethod
    def _checksum(data: bytes) -> int:
        return sum(data) & 0xFF

    def _validate_response(self, raw: bytes, command: int) -> bool:
        """Return True when *raw* is a well-formed response for *command*."""
        if len(raw) < 4:
            return False
        if raw[0] != _START_BYTE:
            return False
        if raw[2] != command:
            return False
        data_len = raw[3]
        expected_len = 4 + data_len + 1   # header(4) + data + checksum(1)
        if len(raw) < expected_len:
            return False
        payload = raw[: 4 + data_len]
        if raw[4 + data_len] != self._checksum(payload):
            logger.warning(
                "Checksum mismatch for command 0x%02X: "
                "got 0x%02X, expected 0x%02X",
                command,
                raw[4 + data_len],
                self._checksum(payload),
            )
            return False
        return True

    def _send_receive(self, command: int, expected_frames: int = 1) -> bytes:
        """
        Send a request for *command* and collect *expected_frames* response
        frames.  Returns the concatenated raw bytes.

        Raises :class:`DalyBMSError` on any communication fault.
        """
        if not self._serial or not self._serial.is_open:
            raise DalyBMSError("Serial port is not open")

        request = self._build_request(command)
        try:
            self._serial.reset_input_buffer()
            self._serial.write(request)
        except serial.SerialException as exc:
            raise DalyBMSError(f"Write error: {exc}") from exc

        # Wait a short moment for the BMS to start responding.
        time.sleep(0.05)

        # Each Daly frame: 4 header bytes + data_len + 1 checksum.
        # For single-frame commands data_len is always 8, so 13 bytes total.
        single_frame_len = 13
        total_bytes = single_frame_len * expected_frames

        try:
            raw = self._serial.read(total_bytes)
        except serial.SerialException as exc:
            raise DalyBMSError(f"Read error: {exc}") from exc

        if not raw:
            raise DalyBMSError(
                f"No response from BMS for command 0x{command:02X}"
            )
        return raw

    # ------------------------------------------------------------------
    # Individual command parsers
    # ------------------------------------------------------------------

    def get_basic_status(self) -> BasicStatus:
        """0x90 – pack voltage, current, SOC."""
        raw = self._send_receive(self.CMD_BASIC_STATUS)
        if not self._validate_response(raw, self.CMD_BASIC_STATUS):
            raise DalyBMSError("Invalid response for CMD_BASIC_STATUS")
        # data starts at index 4
        d = raw[4:12]
        pack_voltage = struct.unpack(">H", d[0:2])[0] / 10.0
        acq_voltage = struct.unpack(">H", d[2:4])[0] / 10.0
        current_raw = struct.unpack(">H", d[4:6])[0]
        pack_current = (current_raw - 30000) / 10.0
        soc = struct.unpack(">H", d[6:8])[0] / 10.0
        return BasicStatus(
            pack_voltage=pack_voltage,
            acquisition_voltage=acq_voltage,
            pack_current=pack_current,
            soc_percent=soc,
        )

    def get_cell_voltage_extremes(self) -> CellVoltageExtremes:
        """0x91 – max/min individual cell voltages."""
        raw = self._send_receive(self.CMD_CELL_VOLTAGE_EXTREMES)
        if not self._validate_response(raw, self.CMD_CELL_VOLTAGE_EXTREMES):
            raise DalyBMSError("Invalid response for CMD_CELL_VOLTAGE_EXTREMES")
        d = raw[4:12]
        max_v = struct.unpack(">H", d[0:2])[0] / 1000.0
        max_cell = d[2]
        min_v = struct.unpack(">H", d[3:5])[0] / 1000.0
        min_cell = d[5]
        return CellVoltageExtremes(
            max_voltage=max_v,
            max_cell_number=max_cell,
            min_voltage=min_v,
            min_cell_number=min_cell,
        )

    def get_temperature_extremes(self) -> TemperatureExtremes:
        """0x92 – max/min cell temperatures."""
        raw = self._send_receive(self.CMD_TEMPERATURE_EXTREMES)
        if not self._validate_response(raw, self.CMD_TEMPERATURE_EXTREMES):
            raise DalyBMSError("Invalid response for CMD_TEMPERATURE_EXTREMES")
        d = raw[4:12]
        max_t = d[0] - 40
        max_sensor = d[1]
        min_t = d[2] - 40
        min_sensor = d[3]
        return TemperatureExtremes(
            max_temperature=float(max_t),
            max_sensor_number=max_sensor,
            min_temperature=float(min_t),
            min_sensor_number=min_sensor,
        )

    def get_mos_status(self) -> MosStatus:
        """0x93 – MOS switch states, heartbeat, remaining capacity."""
        raw = self._send_receive(self.CMD_MOS_STATUS)
        if not self._validate_response(raw, self.CMD_MOS_STATUS):
            raise DalyBMSError("Invalid response for CMD_MOS_STATUS")
        d = raw[4:12]
        charge_mos = bool(d[0])
        discharge_mos = bool(d[1])
        heartbeat = d[2]
        # Remaining capacity: 4 bytes big-endian, unit = mAh
        remaining_raw = struct.unpack(">I", d[4:8])[0]
        remaining_ah = remaining_raw / 1000.0  # convert mAh → Ah
        return MosStatus(
            charge_mos_on=charge_mos,
            discharge_mos_on=discharge_mos,
            bms_heartbeat=heartbeat,
            remaining_capacity_ah=remaining_ah,
        )

    def get_status_info(self) -> StatusInfo:
        """0x94 – cell count, sensor count, charger/load status, cycles."""
        raw = self._send_receive(self.CMD_STATUS_INFO)
        if not self._validate_response(raw, self.CMD_STATUS_INFO):
            raise DalyBMSError("Invalid response for CMD_STATUS_INFO")
        d = raw[4:12]
        cell_count = d[0]
        temp_sensors = d[1]
        charger = bool(d[2])
        load = bool(d[3])
        states = d[4]
        cycles = struct.unpack(">H", d[5:7])[0]
        return StatusInfo(
            cell_count=cell_count,
            temperature_sensor_count=temp_sensors,
            charger_connected=charger,
            load_connected=load,
            states=states,
            cycles=cycles,
        )

    def get_cell_voltages(self, cell_count: int) -> List[float]:
        """
        0x95 – individual cell voltages in volts.

        *cell_count* must be obtained from :meth:`get_status_info` beforehand.
        The BMS sends ceil(cell_count / 3) response frames (3 cells per frame).
        """
        if cell_count <= 0:
            return []
        frames_needed = (cell_count + 2) // 3   # ceil(cell_count / 3)
        try:
            raw = self._send_receive(self.CMD_CELL_VOLTAGES, expected_frames=frames_needed)
        except DalyBMSError:
            raise

        voltages: List[float] = []
        frame_size = 13   # each response frame is 13 bytes
        for frame_idx in range(frames_needed):
            start = frame_idx * frame_size
            frame = raw[start: start + frame_size]
            if len(frame) < frame_size:
                break
            if not self._validate_response(frame, self.CMD_CELL_VOLTAGES):
                logger.warning("Skipping invalid cell-voltage frame %d", frame_idx + 1)
                continue
            # frame[4] = frame number (1-based, not used here)
            d = frame[5:11]  # 6 bytes = 3 × 2-byte cell voltages
            for i in range(3):
                if len(voltages) >= cell_count:
                    break
                raw_mv = struct.unpack(">H", d[i * 2: i * 2 + 2])[0]
                voltages.append(raw_mv / 1000.0)
        return voltages

    def get_temperatures(self, sensor_count: int) -> List[float]:
        """
        0x96 – individual temperature sensor readings in °C.

        *sensor_count* must be obtained from :meth:`get_status_info`.
        The BMS sends ceil(sensor_count / 7) response frames (7 readings per
        frame).
        """
        if sensor_count <= 0:
            return []
        frames_needed = (sensor_count + 6) // 7   # ceil(sensor_count / 7)
        try:
            raw = self._send_receive(self.CMD_TEMPERATURES, expected_frames=frames_needed)
        except DalyBMSError:
            raise

        temperatures: List[float] = []
        frame_size = 13
        for frame_idx in range(frames_needed):
            start = frame_idx * frame_size
            frame = raw[start: start + frame_size]
            if len(frame) < frame_size:
                break
            if not self._validate_response(frame, self.CMD_TEMPERATURES):
                logger.warning("Skipping invalid temperature frame %d", frame_idx + 1)
                continue
            # frame[4] = frame number, frame[5:12] = up to 7 temp bytes
            for byte_idx in range(7):
                if len(temperatures) >= sensor_count:
                    break
                temperatures.append(float(frame[5 + byte_idx] - 40))
        return temperatures

    def get_balance_status(self, cell_count: int) -> List[bool]:
        """
        0x97 – per-cell balance-active flags.

        Returns a list of *cell_count* booleans (True = balancing active).
        """
        raw = self._send_receive(self.CMD_BALANCE_STATUS)
        if not self._validate_response(raw, self.CMD_BALANCE_STATUS):
            raise DalyBMSError("Invalid response for CMD_BALANCE_STATUS")
        balance: List[bool] = []
        # 6 data bytes = up to 48 cells (1 bit each)
        for byte_idx in range(6):
            byte_val = raw[4 + byte_idx]
            for bit in range(8):
                if len(balance) >= cell_count:
                    return balance
                balance.append(bool(byte_val & (1 << bit)))
        return balance[:cell_count]

    def get_failure_flags(self) -> FailureFlags:
        """0x98 – alarm and fault flags."""
        raw = self._send_receive(self.CMD_FAILURE_FLAGS)
        if not self._validate_response(raw, self.CMD_FAILURE_FLAGS):
            raise DalyBMSError("Invalid response for CMD_FAILURE_FLAGS")
        d = raw[4:12]

        def bit(byte_index: int, bit_index: int) -> bool:
            if byte_index >= len(d):
                return False
            return bool(d[byte_index] & (1 << bit_index))

        return FailureFlags(
            # Byte 0
            cell_overvoltage_alarm_l1=bit(0, 0),
            cell_overvoltage_alarm_l2=bit(0, 1),
            cell_undervoltage_alarm_l1=bit(0, 2),
            cell_undervoltage_alarm_l2=bit(0, 3),
            pack_overvoltage_alarm_l1=bit(0, 4),
            pack_overvoltage_alarm_l2=bit(0, 5),
            pack_undervoltage_alarm_l1=bit(0, 6),
            pack_undervoltage_alarm_l2=bit(0, 7),
            # Byte 1
            charge_overtemp_alarm_l1=bit(1, 0),
            charge_overtemp_alarm_l2=bit(1, 1),
            charge_undertemp_alarm_l1=bit(1, 2),
            charge_undertemp_alarm_l2=bit(1, 3),
            discharge_overtemp_alarm_l1=bit(1, 4),
            discharge_overtemp_alarm_l2=bit(1, 5),
            discharge_undertemp_alarm_l1=bit(1, 6),
            discharge_undertemp_alarm_l2=bit(1, 7),
            # Byte 2
            charge_overcurrent_alarm_l1=bit(2, 0),
            charge_overcurrent_alarm_l2=bit(2, 1),
            discharge_overcurrent_alarm_l1=bit(2, 2),
            discharge_overcurrent_alarm_l2=bit(2, 3),
            soc_low_alarm_l1=bit(2, 4),
            soc_low_alarm_l2=bit(2, 5),
            cell_voltage_diff_alarm_l1=bit(2, 6),
            cell_voltage_diff_alarm_l2=bit(2, 7),
            # Byte 3
            cell_temp_diff_alarm_l1=bit(3, 0),
            cell_temp_diff_alarm_l2=bit(3, 1),
            charge_mos_overtemp_alarm_l1=bit(3, 2),
            charge_mos_overtemp_alarm_l2=bit(3, 3),
            discharge_mos_overtemp_alarm_l1=bit(3, 4),
            discharge_mos_overtemp_alarm_l2=bit(3, 5),
            # Byte 4
            charge_mos_fault=bit(4, 0),
            discharge_mos_fault=bit(4, 1),
            temp_sensor_fault=bit(4, 2),
            cell_fault=bit(4, 3),
            sampling_circuit_fault=bit(4, 4),
            cell_balance_fault=bit(4, 5),
        )

    # ------------------------------------------------------------------
    # Aggregated read
    # ------------------------------------------------------------------

    def get_all_data(self) -> DalyBMSData:
        """
        Query all standard BMS data and return a :class:`DalyBMSData` snapshot.

        Each sub-command is attempted independently.  A failure in one command
        does not abort the others – the corresponding field in the result is
        left as *None* and a warning is logged.
        """
        data = DalyBMSData()

        def _try(name: str, fn, *args):
            try:
                return fn(*args)
            except DalyBMSError as exc:
                logger.warning("Failed to read %s: %s", name, exc)
                return None

        data.basic = _try("basic status", self.get_basic_status)
        data.cell_extremes = _try(
            "cell voltage extremes", self.get_cell_voltage_extremes
        )
        data.temp_extremes = _try(
            "temperature extremes", self.get_temperature_extremes
        )
        data.mos = _try("MOS status", self.get_mos_status)
        data.status = _try("status info", self.get_status_info)

        if data.status and data.status.cell_count > 0:
            cell_count = data.status.cell_count
            sensor_count = data.status.temperature_sensor_count

            voltages = _try("cell voltages", self.get_cell_voltages, cell_count)
            data.cell_voltages = voltages or []

            temps = _try("temperatures", self.get_temperatures, sensor_count)
            data.temperatures = temps or []

            balance = _try(
                "balance status", self.get_balance_status, cell_count
            )
            data.cell_balance_active = balance or []

        data.failures = _try("failure flags", self.get_failure_flags)
        return data
