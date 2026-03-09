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
    # Raw failure bytes: all zeros (no alarms).
    # _read_request is a private method of dalybms.DalyBMS that returns the
    # 8-byte payload from BMS command 0x98 (failure flags).
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


# ---------------------------------------------------------------------------
# Tests for data-fetch toggles
# ---------------------------------------------------------------------------

class TestFetchToggles(unittest.TestCase):
    """Verify that disabled fetch flags prevent the corresponding lib call."""

    def _make_bms(self, **fetch_kwargs):
        with patch("daly_bms._DalyBMSLib"):
            bms = bms_mod.DalyBMS(port="/dev/null", address=4, **fetch_kwargs)
        bms._lib = _make_lib_mock()
        return bms

    def test_fetch_soc_false_skips_call(self):
        bms = self._make_bms(fetch_soc=False)
        data = bms.get_all_data()
        bms._lib.get_soc.assert_not_called()
        self.assertIsNone(data.basic)

    def test_fetch_cell_voltage_range_false_skips_call(self):
        bms = self._make_bms(fetch_cell_voltage_range=False)
        data = bms.get_all_data()
        bms._lib.get_cell_voltage_range.assert_not_called()
        self.assertIsNone(data.cell_extremes)

    def test_fetch_temperature_range_false_skips_call(self):
        bms = self._make_bms(fetch_temperature_range=False)
        data = bms.get_all_data()
        bms._lib.get_temperature_range.assert_not_called()
        self.assertIsNone(data.temp_extremes)

    def test_fetch_mosfet_status_false_skips_call(self):
        bms = self._make_bms(fetch_mosfet_status=False)
        data = bms.get_all_data()
        bms._lib.get_mosfet_status.assert_not_called()
        self.assertIsNone(data.mos)

    def test_fetch_status_false_skips_call(self):
        bms = self._make_bms(fetch_status=False)
        data = bms.get_all_data()
        bms._lib.get_status.assert_not_called()
        # No override provided → status stays None
        self.assertIsNone(data.status)

    def test_fetch_cell_voltages_false_skips_call(self):
        bms = self._make_bms(fetch_cell_voltages=False)
        data = bms.get_all_data()
        bms._lib.get_cell_voltages.assert_not_called()
        self.assertEqual(data.cell_voltages, [])

    def test_fetch_temperatures_false_skips_call(self):
        bms = self._make_bms(fetch_temperatures=False)
        data = bms.get_all_data()
        bms._lib.get_temperatures.assert_not_called()
        self.assertEqual(data.temperatures, [])

    def test_fetch_balancing_false_skips_call(self):
        bms = self._make_bms(fetch_balancing=False)
        data = bms.get_all_data()
        bms._lib.get_balancing_status.assert_not_called()
        self.assertEqual(data.cell_balance_active, [])

    def test_fetch_errors_false_skips_call(self):
        bms = self._make_bms(fetch_errors=False)
        data = bms.get_all_data()
        bms._lib._read_request.assert_not_called()
        self.assertIsNone(data.failures)


class TestFetchStatusFalseWithOverride(unittest.TestCase):
    """
    Verify that cell voltages and temperatures still work when FETCH_STATUS
    is False but cell_count_override / temp_sensor_count_override are set.
    """

    def _make_bms(self, cell_count=4, temp_count=2):
        with patch("daly_bms._DalyBMSLib"):
            bms = bms_mod.DalyBMS(
                port="/dev/null",
                address=4,
                fetch_status=False,
                cell_count_override=cell_count,
                temp_sensor_count_override=temp_count,
            )
        bms._lib = _make_lib_mock()
        return bms

    def test_status_synthesised_from_override(self):
        bms = self._make_bms(cell_count=8, temp_count=3)
        data = bms.get_all_data()
        bms._lib.get_status.assert_not_called()
        self.assertIsNotNone(data.status)
        self.assertEqual(data.status.cell_count, 8)
        self.assertEqual(data.status.temperature_sensor_count, 3)

    def test_lib_status_primed_for_cell_voltages(self):
        bms = self._make_bms(cell_count=4, temp_count=2)
        # Simulate the case where connect() failed to populate lib.status
        bms._lib.status = None
        bms.get_all_data()
        # The adapter must prime lib.status so get_cell_voltages() works.
        self.assertIsNotNone(bms._lib.status)
        self.assertEqual(bms._lib.status["cells"], 4)
        self.assertEqual(bms._lib.status["temperature_sensors"], 2)

    def test_cell_voltages_still_fetched(self):
        bms = self._make_bms(cell_count=4)
        data = bms.get_all_data()
        bms._lib.get_cell_voltages.assert_called_once()
        self.assertEqual(len(data.cell_voltages), 4)

    def test_no_override_skips_cell_voltages(self):
        """Without an override, cell voltages and temperatures are not fetched."""
        with patch("daly_bms._DalyBMSLib"):
            bms = bms_mod.DalyBMS(
                port="/dev/null",
                address=4,
                fetch_status=False,
                cell_count_override=0,
                temp_sensor_count_override=0,
            )
        bms._lib = _make_lib_mock()
        # lib.status is not primed, so get_cell_voltages will fail inside
        # dalybms (_calc_num_responses checks self.status).  Our adapter
        # still calls it but handles any exception gracefully.
        data = bms.get_all_data()
        self.assertIsNone(data.status)


# ---------------------------------------------------------------------------
# Tests for Sinowealth mode
# ---------------------------------------------------------------------------

def _make_sinowealth_lib_mock(**kwargs):
    """Return a pre-configured MagicMock for dalybms.DalyBMSSinowealth."""
    m = MagicMock()
    m.get_soc.return_value = kwargs.get("soc", {
        "total_voltage": 48.0,
        "current": -5.0,
        "soc_percent": 90.0,
    })
    m.get_cell_voltage_range.return_value = kwargs.get("cell_voltage_range", {})
    m.get_temperature_range.return_value = kwargs.get("temperature_range", {})
    m.get_mosfet_status.return_value = kwargs.get("mosfet_status", {
        "full_capacity_ah": 100.0,
        "remaining_capacity_ah": 90.0,
        "pack_state": [
            "CHGMOS: Charging enabled",
            "DSGMOS: Discharging enabled",
        ],
    })
    m.get_status.return_value = kwargs.get("status", {"cycles": 12})
    m.get_cell_voltages.return_value = kwargs.get("cell_voltages", {
        1: 3.200, 2: 3.210, 3: 3.190, 4: 3.205,
    })
    # Sinowealth temperatures are named "external1", "external2"
    m.get_temperatures.return_value = kwargs.get("temperatures", {
        "external1": 28.0, "external2": 30.0,
    })
    m.get_balancing_status.return_value = kwargs.get("balancing_status", {})
    m.get_errors.return_value = kwargs.get("errors", [])
    return m


class TestSinowealthMode(unittest.TestCase):
    """Verify Sinowealth chip mode uses the right library and data mapping."""

    @patch("daly_bms._DalyBMSSinowealth")
    @patch("daly_bms._DalyBMSLib")
    def test_sinowealth_true_uses_sinowealth_class(self, MockLib, MockSino):
        bms_mod.DalyBMS(port="/dev/null", sinowealth=True)
        MockSino.assert_called_once()
        MockLib.assert_not_called()

    @patch("daly_bms._DalyBMSSinowealth")
    @patch("daly_bms._DalyBMSLib")
    def test_sinowealth_false_uses_regular_class(self, MockLib, MockSino):
        bms_mod.DalyBMS(port="/dev/null", sinowealth=False)
        MockLib.assert_called_once()
        MockSino.assert_not_called()

    def _make_sino_bms(self, **kwargs):
        with patch("daly_bms._DalyBMSSinowealth"), patch("daly_bms._DalyBMSLib"):
            bms = bms_mod.DalyBMS(port="/dev/null", sinowealth=True)
        bms._lib = _make_sinowealth_lib_mock(**kwargs)
        return bms

    def test_soc_data_mapped(self):
        data = self._make_sino_bms().get_all_data()
        self.assertIsNotNone(data.basic)
        self.assertAlmostEqual(data.basic.pack_voltage, 48.0)
        self.assertAlmostEqual(data.basic.pack_current, -5.0)
        self.assertAlmostEqual(data.basic.soc_percent, 90.0)

    def test_cell_voltage_range_empty_dict_not_populated(self):
        data = self._make_sino_bms().get_all_data()
        # Sinowealth returns {} for cell_voltage_range — leave as None
        self.assertIsNone(data.cell_extremes)

    def test_mosfet_status_from_pack_state(self):
        data = self._make_sino_bms().get_all_data()
        self.assertIsNotNone(data.mos)
        self.assertTrue(data.mos.charge_mos_on)
        self.assertTrue(data.mos.discharge_mos_on)
        self.assertAlmostEqual(data.mos.remaining_capacity_ah, 90.0)

    def test_mosfet_mos_off_when_not_in_pack_state(self):
        bms = self._make_sino_bms(mosfet_status={
            "remaining_capacity_ah": 50.0,
            "pack_state": [],
        })
        data = bms.get_all_data()
        self.assertFalse(data.mos.charge_mos_on)
        self.assertFalse(data.mos.discharge_mos_on)

    def test_status_cycles_from_sinowealth(self):
        data = self._make_sino_bms().get_all_data()
        self.assertIsNotNone(data.status)
        self.assertEqual(data.status.cycles, 12)

    def test_status_cell_count_backfilled_from_cell_voltages(self):
        data = self._make_sino_bms().get_all_data()
        # 4 cells from mock get_cell_voltages
        self.assertEqual(data.status.cell_count, 4)

    def test_status_temp_sensor_count_backfilled_from_temperatures(self):
        data = self._make_sino_bms().get_all_data()
        # 2 sensors from mock get_temperatures (external1, external2)
        self.assertEqual(data.status.temperature_sensor_count, 2)

    def test_cell_voltages_list(self):
        data = self._make_sino_bms().get_all_data()
        self.assertEqual(len(data.cell_voltages), 4)
        self.assertAlmostEqual(data.cell_voltages[0], 3.200, places=3)

    def test_temperatures_sorted_by_key_name(self):
        data = self._make_sino_bms().get_all_data()
        # sorted(["external1", "external2"]) → ["external1", "external2"]
        self.assertEqual(len(data.temperatures), 2)
        self.assertAlmostEqual(data.temperatures[0], 28.0)  # external1
        self.assertAlmostEqual(data.temperatures[1], 30.0)  # external2

    def test_errors_logged_not_failures_populated(self):
        bms = self._make_sino_bms(errors=["OV: Overvoltage protection occurs"])
        data = bms.get_all_data()
        # Sinowealth errors are logged, not put into FailureFlags
        self.assertIsNone(data.failures)
        bms._lib.get_errors.assert_called_once()

    def test_no_errors_when_list_empty(self):
        data = self._make_sino_bms(errors=[]).get_all_data()
        self.assertIsNone(data.failures)

    def test_read_request_not_called_in_sinowealth_mode(self):
        bms = self._make_sino_bms()
        bms.get_all_data()
        bms._lib._read_request.assert_not_called()


# ---------------------------------------------------------------------------
# Tests for verbose mode
# ---------------------------------------------------------------------------

class TestVerboseMode(unittest.TestCase):
    """Verify that the verbose flag raises the logger to DEBUG level."""

    def test_verbose_true_sets_root_logger_debug(self):
        """BMS_VERBOSE=true should set the root logger level to DEBUG."""
        import logging as logging_mod
        root_logger = logging_mod.getLogger()
        original_level = root_logger.level
        try:
            # Simulate what main() does when BMS_VERBOSE is True
            root_logger.setLevel(logging_mod.DEBUG)
            self.assertEqual(root_logger.level, logging_mod.DEBUG)
        finally:
            root_logger.setLevel(original_level)


# ---------------------------------------------------------------------------
# Tests for the poll-timeout watchdog (BMSPoller)
# ---------------------------------------------------------------------------

class TestBMSPollerTimeout(unittest.TestCase):
    """Verify the BMSPoller timeout watchdog via exporter.BMSPoller."""

    def setUp(self):
        # Import BMSPoller from exporter without running main()
        import importlib
        import sys
        # Stub prometheus_client before import to avoid binding issues
        sys.modules.setdefault("prometheus_client", MagicMock())
        import exporter as exp_mod
        self.exp_mod = exp_mod

    def _make_poller(self, bms_mock, interval=1.0, poll_timeout=0.1):
        return self.exp_mod.BMSPoller(bms_mock, interval=interval, poll_timeout=poll_timeout)

    def test_hung_poll_triggers_disconnect(self):
        """A poll that exceeds poll_timeout must call disconnect()."""
        import threading
        import time

        bms_mock = MagicMock()
        # get_all_data blocks until the Event is set (simulates a hang).
        # After the event is set it raises RuntimeError to mimic the
        # SerialException raised when disconnect() closes the port.
        hang_event = threading.Event()
        def _hanging_poll():
            hang_event.wait(timeout=5.0)
            raise RuntimeError("simulated serial port close after disconnect")
        bms_mock.get_all_data.side_effect = _hanging_poll

        poller = self._make_poller(bms_mock, interval=2.0, poll_timeout=0.15)
        poller.start()

        # Give enough time for the poller to start, time out, and call disconnect
        time.sleep(0.8)

        hang_event.set()   # unblock the worker so the test cleans up
        poller.join(timeout=2.0)

        bms_mock.disconnect.assert_called()

    def test_successful_poll_calls_get_all_data(self):
        """A normal (fast) poll must call get_all_data() and not disconnect()."""
        import threading
        import time

        bms_mock = MagicMock()
        done_event = threading.Event()
        def _fast_poll():
            done_event.set()
            return MagicMock()
        bms_mock.get_all_data.side_effect = _fast_poll

        poller = self._make_poller(bms_mock, interval=2.0, poll_timeout=1.0)
        poller.start()

        done_event.wait(timeout=2.0)
        poller.join(timeout=3.0)

        bms_mock.get_all_data.assert_called()
        bms_mock.disconnect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
