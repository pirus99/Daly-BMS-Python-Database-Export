"""
Unit tests for the Daly BMS Prometheus Exporter.

These tests exercise the adapter layer in daly_bms.py that wraps the
``dalybms`` library.  The underlying ``dalybms.DalyBMS`` is mocked so that
no physical hardware or serial port is required.
"""

import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------

import daly_bms as bms_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_lib_mock(**kwargs):
    """Return a pre-configured MagicMock for dalybms.DalyBMS."""
    m = MagicMock()
    # Sensible defaults for all getter methods
    m.get_soc.return_value = kwargs.get("soc", {
        "total_voltage": 52.0,
        "current": 10.0,
        "soc_percent": 85.0,
    })
    m.get_cell_voltage_range.return_value = kwargs.get("cell_voltage_range", {
        "highest_voltage": 3.650,
        "highest_cell": 4,
        "lowest_voltage": 3.200,
        "lowest_cell": 1,
    })
    m.get_temperature_range.return_value = kwargs.get("temperature_range", {
        "highest_temperature": 35,
        "highest_sensor": 1,
        "lowest_temperature": 25,
        "lowest_sensor": 2,
    })
    m.get_mosfet_status.return_value = kwargs.get("mosfet_status", {
        "charging_mosfet": True,
        "discharging_mosfet": False,
        "capacity_ah": 50.0,
    })
    m.get_status.return_value = kwargs.get("status", {
        "cells": 4,
        "temperature_sensors": 2,
        "charger_running": True,
        "load_running": False,
        "cycles": 42,
    })
    m.get_cell_voltages.return_value = kwargs.get("cell_voltages", {
        1: 3.600, 2: 3.620, 3: 3.580, 4: 3.610
    })
    m.get_temperatures.return_value = kwargs.get("temperatures", {
        1: 35.0, 2: 25.0
    })
    m.get_balancing_status.return_value = kwargs.get("balancing_status", {
        "error": "not implemented"
    })
    # Raw failure bytes: all zeros (no alarms)
    m._read_request.return_value = bytes(8)
    return m


# ---------------------------------------------------------------------------
# Tests for the DalyBMS adapter
# ---------------------------------------------------------------------------

class TestDalyBMSAdapterAddress(unittest.TestCase):
    """Verify that address conversion creates the right dalybms instance."""

    @patch("daly_bms._DalyBMSLib")
    def test_rs485_address_legacy_hex(self, MockLib):
        """0x40 (legacy) should map to dalybms address 4 (RS-485)."""
        bms_mod.DalyBMS(port="/dev/null", address=0x40)
        MockLib.assert_called_once_with(address=4, logger=unittest.mock.ANY)

    @patch("daly_bms._DalyBMSLib")
    def test_uart_address_legacy_hex(self, MockLib):
        """0x80 (legacy) should map to dalybms address 8 (UART)."""
        bms_mod.DalyBMS(port="/dev/null", address=0x80)
        MockLib.assert_called_once_with(address=8, logger=unittest.mock.ANY)

    @patch("daly_bms._DalyBMSLib")
    def test_rs485_address_native(self, MockLib):
        """Native dalybms address 4 should pass through unchanged."""
        bms_mod.DalyBMS(port="/dev/null", address=4)
        MockLib.assert_called_once_with(address=4, logger=unittest.mock.ANY)

    @patch("daly_bms._DalyBMSLib")
    def test_uart_address_native(self, MockLib):
        """Native dalybms address 8 should pass through unchanged."""
        bms_mod.DalyBMS(port="/dev/null", address=8)
        MockLib.assert_called_once_with(address=8, logger=unittest.mock.ANY)


class TestDalyBMSConnect(unittest.TestCase):
    """Verify connect/disconnect delegation."""

    def _make_bms(self):
        with patch("daly_bms._DalyBMSLib") as MockLib:
            bms = bms_mod.DalyBMS(port="/dev/ttyUSB0", address=4)
            bms._lib = _make_lib_mock()
            return bms

    def test_connect_calls_lib_connect(self):
        bms = self._make_bms()
        bms.connect()
        bms._lib.connect.assert_called_once_with("/dev/ttyUSB0")

    def test_connect_raises_on_failure(self):
        bms = self._make_bms()
        bms._lib.connect.side_effect = Exception("port not found")
        with self.assertRaises(bms_mod.DalyBMSError):
            bms.connect()

    def test_disconnect_calls_lib_disconnect(self):
        bms = self._make_bms()
        bms.disconnect()
        bms._lib.disconnect.assert_called_once()


class TestGetAllDataBasicStatus(unittest.TestCase):
    """Verify SOC/voltage/current translation into BasicStatus."""

    def _bms_with_mock(self, **kwargs):
        with patch("daly_bms._DalyBMSLib"):
            bms = bms_mod.DalyBMS(port="/dev/null", address=4)
        bms._lib = _make_lib_mock(**kwargs)
        return bms

    def test_pack_voltage(self):
        bms = self._bms_with_mock(soc={"total_voltage": 52.0, "current": 0.0, "soc_percent": 80.0})
        data = bms.get_all_data()
        self.assertIsNotNone(data.basic)
        self.assertAlmostEqual(data.basic.pack_voltage, 52.0)

    def test_pack_current(self):
        bms = self._bms_with_mock(soc={"total_voltage": 52.0, "current": 10.0, "soc_percent": 80.0})
        data = bms.get_all_data()
        self.assertAlmostEqual(data.basic.pack_current, 10.0)

    def test_soc_percent(self):
        bms = self._bms_with_mock(soc={"total_voltage": 52.0, "current": 0.0, "soc_percent": 75.0})
        data = bms.get_all_data()
        self.assertAlmostEqual(data.basic.soc_percent, 75.0)

    def test_soc_false_returns_none_basic(self):
        bms = self._bms_with_mock()
        bms._lib.get_soc.return_value = False
        data = bms.get_all_data()
        self.assertIsNone(data.basic)


class TestGetAllDataCellVoltageExtremes(unittest.TestCase):
    """Verify cell voltage range translation into CellVoltageExtremes."""

    def _bms_with_mock(self):
        with patch("daly_bms._DalyBMSLib"):
            bms = bms_mod.DalyBMS(port="/dev/null", address=4)
        bms._lib = _make_lib_mock()
        return bms

    def test_max_voltage(self):
        data = self._bms_with_mock().get_all_data()
        self.assertAlmostEqual(data.cell_extremes.max_voltage, 3.650, places=3)

    def test_max_cell_number(self):
        data = self._bms_with_mock().get_all_data()
        self.assertEqual(data.cell_extremes.max_cell_number, 4)

    def test_min_voltage(self):
        data = self._bms_with_mock().get_all_data()
        self.assertAlmostEqual(data.cell_extremes.min_voltage, 3.200, places=3)

    def test_min_cell_number(self):
        data = self._bms_with_mock().get_all_data()
        self.assertEqual(data.cell_extremes.min_cell_number, 1)


class TestGetAllDataTemperatureExtremes(unittest.TestCase):
    """Verify temperature range translation into TemperatureExtremes."""

    def _bms_with_mock(self):
        with patch("daly_bms._DalyBMSLib"):
            bms = bms_mod.DalyBMS(port="/dev/null", address=4)
        bms._lib = _make_lib_mock()
        return bms

    def test_max_temperature(self):
        data = self._bms_with_mock().get_all_data()
        self.assertEqual(data.temp_extremes.max_temperature, 35.0)

    def test_min_temperature(self):
        data = self._bms_with_mock().get_all_data()
        self.assertEqual(data.temp_extremes.min_temperature, 25.0)

    def test_sensor_numbers(self):
        data = self._bms_with_mock().get_all_data()
        self.assertEqual(data.temp_extremes.max_sensor_number, 1)
        self.assertEqual(data.temp_extremes.min_sensor_number, 2)


class TestGetAllDataMosStatus(unittest.TestCase):
    """Verify MOSFET status translation into MosStatus."""

    def _bms_with_mock(self, **kwargs):
        with patch("daly_bms._DalyBMSLib"):
            bms = bms_mod.DalyBMS(port="/dev/null", address=4)
        bms._lib = _make_lib_mock(**kwargs)
        return bms

    def test_charge_mos_on(self):
        data = self._bms_with_mock().get_all_data()
        self.assertTrue(data.mos.charge_mos_on)

    def test_discharge_mos_off(self):
        data = self._bms_with_mock().get_all_data()
        self.assertFalse(data.mos.discharge_mos_on)

    def test_remaining_capacity(self):
        data = self._bms_with_mock().get_all_data()
        self.assertAlmostEqual(data.mos.remaining_capacity_ah, 50.0)


class TestGetAllDataStatusInfo(unittest.TestCase):
    """Verify BMS status translation into StatusInfo."""

    def _bms_with_mock(self):
        with patch("daly_bms._DalyBMSLib"):
            bms = bms_mod.DalyBMS(port="/dev/null", address=4)
        bms._lib = _make_lib_mock()
        return bms

    def test_cell_count(self):
        data = self._bms_with_mock().get_all_data()
        self.assertEqual(data.status.cell_count, 4)

    def test_temperature_sensors(self):
        data = self._bms_with_mock().get_all_data()
        self.assertEqual(data.status.temperature_sensor_count, 2)

    def test_charger_connected(self):
        data = self._bms_with_mock().get_all_data()
        self.assertTrue(data.status.charger_connected)

    def test_load_connected(self):
        data = self._bms_with_mock().get_all_data()
        self.assertFalse(data.status.load_connected)

    def test_cycles(self):
        data = self._bms_with_mock().get_all_data()
        self.assertEqual(data.status.cycles, 42)


class TestGetAllDataCellVoltages(unittest.TestCase):
    """Verify per-cell voltage list translation."""

    def _bms_with_mock(self, **kwargs):
        with patch("daly_bms._DalyBMSLib"):
            bms = bms_mod.DalyBMS(port="/dev/null", address=4)
        bms._lib = _make_lib_mock(**kwargs)
        return bms

    def test_cell_voltages_list(self):
        data = self._bms_with_mock().get_all_data()
        self.assertEqual(len(data.cell_voltages), 4)
        self.assertAlmostEqual(data.cell_voltages[0], 3.600, places=3)
        self.assertAlmostEqual(data.cell_voltages[1], 3.620, places=3)
        self.assertAlmostEqual(data.cell_voltages[2], 3.580, places=3)
        self.assertAlmostEqual(data.cell_voltages[3], 3.610, places=3)

    def test_no_cell_voltages_returns_empty(self):
        bms = self._bms_with_mock()
        bms._lib.get_cell_voltages.return_value = False
        data = bms.get_all_data()
        self.assertEqual(data.cell_voltages, [])


class TestGetAllDataTemperatures(unittest.TestCase):
    """Verify per-sensor temperature list translation."""

    def _bms_with_mock(self):
        with patch("daly_bms._DalyBMSLib"):
            bms = bms_mod.DalyBMS(port="/dev/null", address=4)
        bms._lib = _make_lib_mock()
        return bms

    def test_temperatures_list(self):
        data = self._bms_with_mock().get_all_data()
        self.assertEqual(len(data.temperatures), 2)
        self.assertAlmostEqual(data.temperatures[0], 35.0)
        self.assertAlmostEqual(data.temperatures[1], 25.0)


class TestGetAllDataBalancing(unittest.TestCase):
    """Verify cell balancing status handling."""

    def _bms_with_mock(self, **kwargs):
        with patch("daly_bms._DalyBMSLib"):
            bms = bms_mod.DalyBMS(port="/dev/null", address=4)
        bms._lib = _make_lib_mock(**kwargs)
        return bms

    def test_not_implemented_returns_all_false(self):
        # dalybms currently returns {"error": "not implemented"}
        data = self._bms_with_mock().get_all_data()
        self.assertEqual(len(data.cell_balance_active), 4)
        self.assertFalse(any(data.cell_balance_active))

    def test_balancing_active(self):
        bms = self._bms_with_mock(balancing_status={1: False, 2: True, 3: False, 4: False})
        data = bms.get_all_data()
        self.assertFalse(data.cell_balance_active[0])
        self.assertTrue(data.cell_balance_active[1])
        self.assertFalse(data.cell_balance_active[2])


class TestGetAllDataFailureFlags(unittest.TestCase):
    """Verify failure-flag parsing from raw bytes."""

    def _bms_with_raw_errors(self, raw: bytes):
        with patch("daly_bms._DalyBMSLib"):
            bms = bms_mod.DalyBMS(port="/dev/null", address=4)
        bms._lib = _make_lib_mock()
        bms._lib._read_request.return_value = raw
        return bms

    def test_no_failures(self):
        data = self._bms_with_raw_errors(bytes(8)).get_all_data()
        self.assertIsNotNone(data.failures)
        self.assertFalse(data.failures.cell_overvoltage_alarm_l1)
        self.assertFalse(data.failures.charge_mos_fault)

    def test_cell_overvoltage_alarm_l1(self):
        raw = bytes([0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        data = self._bms_with_raw_errors(raw).get_all_data()
        self.assertTrue(data.failures.cell_overvoltage_alarm_l1)
        self.assertFalse(data.failures.cell_overvoltage_alarm_l2)

    def test_charge_mos_fault(self):
        # Byte 4, bit 0
        raw = bytes([0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00])
        data = self._bms_with_raw_errors(raw).get_all_data()
        self.assertTrue(data.failures.charge_mos_fault)
        self.assertFalse(data.failures.discharge_mos_fault)

    def test_multiple_flags(self):
        raw = bytes([0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00])
        data = self._bms_with_raw_errors(raw).get_all_data()
        self.assertTrue(data.failures.cell_overvoltage_alarm_l1)
        self.assertTrue(data.failures.charge_mos_fault)

    def test_read_failure_returns_none_failures(self):
        bms = self._bms_with_raw_errors(bytes(8))
        bms._lib._read_request.return_value = False
        data = bms.get_all_data()
        self.assertIsNone(data.failures)


class TestParseFailureFlags(unittest.TestCase):
    """Unit tests for the _parse_failure_flags helper."""

    def test_all_zeros(self):
        flags = bms_mod._parse_failure_flags(bytes(8))
        self.assertFalse(flags.cell_overvoltage_alarm_l1)
        self.assertFalse(flags.pack_undervoltage_alarm_l2)
        self.assertFalse(flags.cell_balance_fault)

    def test_pack_overvoltage_l1(self):
        # byte 0, bit 4
        data = bytes([0x10, 0, 0, 0, 0, 0, 0, 0])
        flags = bms_mod._parse_failure_flags(data)
        self.assertTrue(flags.pack_overvoltage_alarm_l1)
        self.assertFalse(flags.pack_overvoltage_alarm_l2)

    def test_discharge_mos_fault(self):
        # byte 4, bit 1
        data = bytes([0, 0, 0, 0, 0x02, 0, 0, 0])
        flags = bms_mod._parse_failure_flags(data)
        self.assertFalse(flags.charge_mos_fault)
        self.assertTrue(flags.discharge_mos_fault)


if __name__ == "__main__":
    unittest.main()
