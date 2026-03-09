"""
Unit tests for the Daly BMS Prometheus Exporter.

These tests exercise the frame-building and response-parsing logic in
daly_bms.py without requiring a physical USB-to-RS485 adapter.  A mock
serial port is injected to simulate BMS responses.
"""

import struct
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helper – build a syntactically valid BMS response frame
# ---------------------------------------------------------------------------

START = 0xA5
ADDR = 0x40


def _make_response(command: int, data: bytes) -> bytes:
    """Build a valid 13-byte (data_len=8) response frame."""
    assert len(data) == 8, "Daly BMS data payload must be exactly 8 bytes"
    header = bytes([START, ADDR, command, 8])
    payload = header + data
    checksum = sum(payload) & 0xFF
    return payload + bytes([checksum])


# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------

import daly_bms as bms_mod


class TestFrameBuilding(unittest.TestCase):
    """Verify that _build_request constructs a valid frame."""

    def setUp(self):
        self.bms = bms_mod.DalyBMS(port="/dev/null", address=ADDR)

    def test_request_length(self):
        frame = self.bms._build_request(0x90)
        self.assertEqual(len(frame), 13)

    def test_request_start_byte(self):
        frame = self.bms._build_request(0x90)
        self.assertEqual(frame[0], 0xA5)

    def test_request_address(self):
        frame = self.bms._build_request(0x90)
        self.assertEqual(frame[1], ADDR)

    def test_request_command(self):
        frame = self.bms._build_request(0x91)
        self.assertEqual(frame[2], 0x91)

    def test_request_data_length(self):
        frame = self.bms._build_request(0x90)
        self.assertEqual(frame[3], 0x08)

    def test_request_checksum(self):
        frame = self.bms._build_request(0x90)
        expected_checksum = sum(frame[:-1]) & 0xFF
        self.assertEqual(frame[-1], expected_checksum)

    def test_request_data_bytes_are_zero(self):
        frame = self.bms._build_request(0x90)
        self.assertEqual(list(frame[4:12]), [0] * 8)


class TestValidateResponse(unittest.TestCase):
    """Verify checksum and header validation."""

    def setUp(self):
        self.bms = bms_mod.DalyBMS(port="/dev/null", address=ADDR)

    def _good(self, cmd):
        return _make_response(cmd, b"\x00" * 8)

    def test_valid_frame_accepted(self):
        frame = self._good(0x90)
        self.assertTrue(self.bms._validate_response(frame, 0x90))

    def test_wrong_command_rejected(self):
        frame = self._good(0x90)
        self.assertFalse(self.bms._validate_response(frame, 0x91))

    def test_bad_checksum_rejected(self):
        frame = bytearray(self._good(0x90))
        frame[-1] ^= 0xFF   # corrupt checksum
        self.assertFalse(self.bms._validate_response(bytes(frame), 0x90))

    def test_too_short_rejected(self):
        self.assertFalse(self.bms._validate_response(b"\xA5\x40", 0x90))

    def test_wrong_start_byte_rejected(self):
        frame = bytearray(self._good(0x90))
        frame[0] = 0x00
        self.assertFalse(self.bms._validate_response(bytes(frame), 0x90))


class TestGetBasicStatus(unittest.TestCase):
    """Verify parsing of command 0x90."""

    def setUp(self):
        self.bms = bms_mod.DalyBMS(port="/dev/null", address=ADDR)
        self.bms._serial = MagicMock()
        self.bms._serial.is_open = True

    def _patch_read(self, raw: bytes):
        self.bms._serial.read.return_value = raw
        self.bms._serial.write = MagicMock()
        self.bms._serial.reset_input_buffer = MagicMock()

    def test_basic_status_voltage(self):
        # Pack voltage = 520 → 52.0 V
        data = struct.pack(">HHHH", 520, 519, 30000 + 100, 850)
        raw = _make_response(0x90, data)
        self._patch_read(raw)
        with patch("time.sleep"):
            result = self.bms.get_basic_status()
        self.assertAlmostEqual(result.pack_voltage, 52.0)

    def test_basic_status_current_charging(self):
        data = struct.pack(">HHHH", 520, 519, 30100, 850)
        raw = _make_response(0x90, data)
        self._patch_read(raw)
        with patch("time.sleep"):
            result = self.bms.get_basic_status()
        self.assertAlmostEqual(result.pack_current, 10.0)

    def test_basic_status_current_discharging(self):
        data = struct.pack(">HHHH", 520, 519, 29900, 850)
        raw = _make_response(0x90, data)
        self._patch_read(raw)
        with patch("time.sleep"):
            result = self.bms.get_basic_status()
        self.assertAlmostEqual(result.pack_current, -10.0)

    def test_basic_status_soc(self):
        data = struct.pack(">HHHH", 520, 519, 30000, 750)
        raw = _make_response(0x90, data)
        self._patch_read(raw)
        with patch("time.sleep"):
            result = self.bms.get_basic_status()
        self.assertAlmostEqual(result.soc_percent, 75.0)

    def test_no_response_raises(self):
        self.bms._serial.read.return_value = b""
        self.bms._serial.write = MagicMock()
        self.bms._serial.reset_input_buffer = MagicMock()
        with patch("time.sleep"):
            with self.assertRaises(bms_mod.DalyBMSError):
                self.bms.get_basic_status()


class TestGetCellVoltageExtremes(unittest.TestCase):
    """Verify parsing of command 0x91."""

    def setUp(self):
        self.bms = bms_mod.DalyBMS(port="/dev/null", address=ADDR)
        self.bms._serial = MagicMock()
        self.bms._serial.is_open = True

    def test_cell_voltage_extremes(self):
        # max = 3650 mV = 3.650 V, cell 4
        # min = 3200 mV = 3.200 V, cell 1
        data = bytes([
            0x0E, 0x42,   # 3650
            0x04,         # cell 4
            0x0C, 0x80,   # 3200
            0x01,         # cell 1
            0x00, 0x00,
        ])
        raw = _make_response(0x91, data)
        self.bms._serial.read.return_value = raw
        self.bms._serial.write = MagicMock()
        self.bms._serial.reset_input_buffer = MagicMock()
        with patch("time.sleep"):
            result = self.bms.get_cell_voltage_extremes()
        self.assertAlmostEqual(result.max_voltage, 3.650, places=3)
        self.assertEqual(result.max_cell_number, 4)
        self.assertAlmostEqual(result.min_voltage, 3.200, places=3)
        self.assertEqual(result.min_cell_number, 1)


class TestGetTemperatureExtremes(unittest.TestCase):
    """Verify parsing of command 0x92."""

    def setUp(self):
        self.bms = bms_mod.DalyBMS(port="/dev/null", address=ADDR)
        self.bms._serial = MagicMock()
        self.bms._serial.is_open = True

    def test_temperature_extremes(self):
        # max = 60 + 40 = 100 raw → 60 °C, sensor 1
        # min = 15 + 40 = 55 raw → 15 °C, sensor 2
        data = bytes([100, 1, 55, 2, 0, 0, 0, 0])
        raw = _make_response(0x92, data)
        self.bms._serial.read.return_value = raw
        self.bms._serial.write = MagicMock()
        self.bms._serial.reset_input_buffer = MagicMock()
        with patch("time.sleep"):
            result = self.bms.get_temperature_extremes()
        self.assertEqual(result.max_temperature, 60.0)
        self.assertEqual(result.max_sensor_number, 1)
        self.assertEqual(result.min_temperature, 15.0)
        self.assertEqual(result.min_sensor_number, 2)


class TestGetMosStatus(unittest.TestCase):
    """Verify parsing of command 0x93."""

    def setUp(self):
        self.bms = bms_mod.DalyBMS(port="/dev/null", address=ADDR)
        self.bms._serial = MagicMock()
        self.bms._serial.is_open = True

    def test_mos_status_on(self):
        # charge MOS on, discharge MOS off, heartbeat=5
        # remaining = 50000 mAh → 50.0 Ah
        remaining_raw = struct.pack(">I", 50000)
        data = bytes([1, 0, 5, 0]) + remaining_raw
        raw = _make_response(0x93, data)
        self.bms._serial.read.return_value = raw
        self.bms._serial.write = MagicMock()
        self.bms._serial.reset_input_buffer = MagicMock()
        with patch("time.sleep"):
            result = self.bms.get_mos_status()
        self.assertTrue(result.charge_mos_on)
        self.assertFalse(result.discharge_mos_on)
        self.assertEqual(result.bms_heartbeat, 5)
        self.assertAlmostEqual(result.remaining_capacity_ah, 50.0)


class TestGetStatusInfo(unittest.TestCase):
    """Verify parsing of command 0x94."""

    def setUp(self):
        self.bms = bms_mod.DalyBMS(port="/dev/null", address=ADDR)
        self.bms._serial = MagicMock()
        self.bms._serial.is_open = True

    def test_status_info(self):
        # 8 cells, 2 temp sensors, charger=on, load=off, states=0, cycles=42
        data = struct.pack(">BBBBBHx", 8, 2, 1, 0, 0, 42)
        raw = _make_response(0x94, data)
        self.bms._serial.read.return_value = raw
        self.bms._serial.write = MagicMock()
        self.bms._serial.reset_input_buffer = MagicMock()
        with patch("time.sleep"):
            result = self.bms.get_status_info()
        self.assertEqual(result.cell_count, 8)
        self.assertEqual(result.temperature_sensor_count, 2)
        self.assertTrue(result.charger_connected)
        self.assertFalse(result.load_connected)
        self.assertEqual(result.cycles, 42)


class TestGetCellVoltages(unittest.TestCase):
    """Verify multi-frame cell voltage parsing (command 0x95)."""

    def setUp(self):
        self.bms = bms_mod.DalyBMS(port="/dev/null", address=ADDR)
        self.bms._serial = MagicMock()
        self.bms._serial.is_open = True

    def _cell_frame(self, frame_number: int, v1: int, v2: int, v3: int) -> bytes:
        """Build a single cell-voltage response frame."""
        data = bytes([frame_number]) + struct.pack(">HHH", v1, v2, v3) + b"\x00"
        return _make_response(0x95, data)

    def test_three_cells_one_frame(self):
        raw = self._cell_frame(1, 3600, 3620, 3580)
        self.bms._serial.read.return_value = raw
        self.bms._serial.write = MagicMock()
        self.bms._serial.reset_input_buffer = MagicMock()
        with patch("time.sleep"):
            voltages = self.bms.get_cell_voltages(3)
        self.assertEqual(len(voltages), 3)
        self.assertAlmostEqual(voltages[0], 3.600, places=3)
        self.assertAlmostEqual(voltages[1], 3.620, places=3)
        self.assertAlmostEqual(voltages[2], 3.580, places=3)

    def test_four_cells_two_frames(self):
        frame1 = self._cell_frame(1, 3600, 3620, 3580)
        # frame 2: only cell 4 is meaningful; cells 5,6 = 0
        frame2 = self._cell_frame(2, 3610, 0, 0)
        self.bms._serial.read.return_value = frame1 + frame2
        self.bms._serial.write = MagicMock()
        self.bms._serial.reset_input_buffer = MagicMock()
        with patch("time.sleep"):
            voltages = self.bms.get_cell_voltages(4)
        self.assertEqual(len(voltages), 4)
        self.assertAlmostEqual(voltages[3], 3.610, places=3)


class TestGetFailureFlags(unittest.TestCase):
    """Verify failure-flag bit parsing (command 0x98)."""

    def setUp(self):
        self.bms = bms_mod.DalyBMS(port="/dev/null", address=ADDR)
        self.bms._serial = MagicMock()
        self.bms._serial.is_open = True

    def test_no_failures(self):
        raw = _make_response(0x98, b"\x00" * 8)
        self.bms._serial.read.return_value = raw
        self.bms._serial.write = MagicMock()
        self.bms._serial.reset_input_buffer = MagicMock()
        with patch("time.sleep"):
            result = self.bms.get_failure_flags()
        self.assertFalse(result.cell_overvoltage_alarm_l1)
        self.assertFalse(result.charge_mos_fault)

    def test_cell_overvoltage_alarm_l1(self):
        data = bytes([0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        raw = _make_response(0x98, data)
        self.bms._serial.read.return_value = raw
        self.bms._serial.write = MagicMock()
        self.bms._serial.reset_input_buffer = MagicMock()
        with patch("time.sleep"):
            result = self.bms.get_failure_flags()
        self.assertTrue(result.cell_overvoltage_alarm_l1)
        self.assertFalse(result.cell_overvoltage_alarm_l2)

    def test_multiple_flags(self):
        # Byte 0 bit 0 + Byte 4 bit 0 (charge_mos_fault)
        data = bytes([0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00])
        raw = _make_response(0x98, data)
        self.bms._serial.read.return_value = raw
        self.bms._serial.write = MagicMock()
        self.bms._serial.reset_input_buffer = MagicMock()
        with patch("time.sleep"):
            result = self.bms.get_failure_flags()
        self.assertTrue(result.cell_overvoltage_alarm_l1)
        self.assertTrue(result.charge_mos_fault)


class TestBalanceStatus(unittest.TestCase):
    """Verify balance-status bit parsing (command 0x97)."""

    def setUp(self):
        self.bms = bms_mod.DalyBMS(port="/dev/null", address=ADDR)
        self.bms._serial = MagicMock()
        self.bms._serial.is_open = True

    def test_no_balancing(self):
        raw = _make_response(0x97, b"\x00" * 8)
        self.bms._serial.read.return_value = raw
        self.bms._serial.write = MagicMock()
        self.bms._serial.reset_input_buffer = MagicMock()
        with patch("time.sleep"):
            result = self.bms.get_balance_status(4)
        self.assertEqual(result, [False, False, False, False])

    def test_cell_2_balancing(self):
        # Byte 0, bit 1 = cell 2 balancing
        raw = _make_response(0x97, bytes([0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]))
        self.bms._serial.read.return_value = raw
        self.bms._serial.write = MagicMock()
        self.bms._serial.reset_input_buffer = MagicMock()
        with patch("time.sleep"):
            result = self.bms.get_balance_status(4)
        self.assertFalse(result[0])
        self.assertTrue(result[1])
        self.assertFalse(result[2])


if __name__ == "__main__":
    unittest.main()
