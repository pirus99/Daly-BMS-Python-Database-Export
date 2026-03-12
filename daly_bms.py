"""
Daly BMS communication adapter.

Uses the `dalybms` library (https://pypi.org/project/dalybms/) for all serial
communication and translates its response dictionaries into the structured
dataclasses consumed by exporter.py.

The ``DalyBMS`` class in this module is a thin wrapper around
``dalybms.DalyBMS`` that preserves the interface expected by the rest of the
application (``connect`` / ``disconnect`` / ``get_all_data``).
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from dalybms import DalyBMS as _DalyBMSLib
from dalybms import DalyBMSSinowealth as _DalyBMSSinowealth

logger = logging.getLogger(__name__)
# Explicitly keep module logger at INFO so that dalybms debug calls do not
# execute when verbose mode is off (even if the root logger is at DEBUG).
logger.setLevel(logging.INFO)

# Name of the dedicated DEBUG-level logger used in verbose mode.
# exporter.py reads this constant to enable propagation when BMS_DEBUG_LOG=true.
_LIB_LOGGER_NAME: str = __name__ + ".lib"


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
# Communication adapter
# ---------------------------------------------------------------------------


class DalyBMSError(Exception):
    """Raised when a BMS communication error occurs."""


def _parse_failure_flags(data: bytes) -> FailureFlags:
    """
    Parse the 8-byte failure-flags payload into a :class:`FailureFlags`.

    This is the same bit-extraction logic as the original raw protocol module,
    operating on the payload bytes returned by the BMS command 0x98.
    """

    def bit(byte_index: int, bit_index: int) -> bool:
        if byte_index >= len(data):
            return False
        return bool(data[byte_index] & (1 << bit_index))

    return FailureFlags(
        # Byte 0 – voltage alarms
        cell_overvoltage_alarm_l1=bit(0, 0),
        cell_overvoltage_alarm_l2=bit(0, 1),
        cell_undervoltage_alarm_l1=bit(0, 2),
        cell_undervoltage_alarm_l2=bit(0, 3),
        pack_overvoltage_alarm_l1=bit(0, 4),
        pack_overvoltage_alarm_l2=bit(0, 5),
        pack_undervoltage_alarm_l1=bit(0, 6),
        pack_undervoltage_alarm_l2=bit(0, 7),
        # Byte 1 – temperature alarms
        charge_overtemp_alarm_l1=bit(1, 0),
        charge_overtemp_alarm_l2=bit(1, 1),
        charge_undertemp_alarm_l1=bit(1, 2),
        charge_undertemp_alarm_l2=bit(1, 3),
        discharge_overtemp_alarm_l1=bit(1, 4),
        discharge_overtemp_alarm_l2=bit(1, 5),
        discharge_undertemp_alarm_l1=bit(1, 6),
        discharge_undertemp_alarm_l2=bit(1, 7),
        # Byte 2 – current / SOC alarms
        charge_overcurrent_alarm_l1=bit(2, 0),
        charge_overcurrent_alarm_l2=bit(2, 1),
        discharge_overcurrent_alarm_l1=bit(2, 2),
        discharge_overcurrent_alarm_l2=bit(2, 3),
        soc_low_alarm_l1=bit(2, 4),
        soc_low_alarm_l2=bit(2, 5),
        cell_voltage_diff_alarm_l1=bit(2, 6),
        cell_voltage_diff_alarm_l2=bit(2, 7),
        # Byte 3 – MOS temperature alarms
        cell_temp_diff_alarm_l1=bit(3, 0),
        cell_temp_diff_alarm_l2=bit(3, 1),
        charge_mos_overtemp_alarm_l1=bit(3, 2),
        charge_mos_overtemp_alarm_l2=bit(3, 3),
        discharge_mos_overtemp_alarm_l1=bit(3, 4),
        discharge_mos_overtemp_alarm_l2=bit(3, 5),
        # Byte 4 – hardware faults
        charge_mos_fault=bit(4, 0),
        discharge_mos_fault=bit(4, 1),
        temp_sensor_fault=bit(4, 2),
        cell_fault=bit(4, 3),
        sampling_circuit_fault=bit(4, 4),
        cell_balance_fault=bit(4, 5),
    )


class DalyBMS:
    """
    Adapter around the ``dalybms`` library for use with the Prometheus exporter.

    All serial communication is delegated to :class:`dalybms.DalyBMS` or, when
    *sinowealth* is ``True``, to :class:`dalybms.DalyBMSSinowealth`.
    This class translates the library's response dictionaries into the typed
    dataclasses consumed by :mod:`exporter`.

    Address conventions
    -------------------
    The ``dalybms`` library uses address ``4`` for RS-485 and ``8`` for
    UART / Bluetooth.  This adapter accepts the legacy hex-style address
    ``0x40`` (64) and ``0x80`` (128) as well and converts them automatically:
    any ``address >= 16`` is right-shifted by 4 bits (e.g. ``0x40 → 4``).
    This parameter is ignored in Sinowealth mode.

    Verbose / BMS_VERBOSE workaround
    ---------------------------------
    When *verbose* is ``True`` the dalybms library receives a dedicated
    ``logging.DEBUG``-level logger (named :data:`_LIB_LOGGER_NAME`).  This
    makes the library's internal ``logger.debug()`` calls execute, adding the
    small serial-timing delays that allow ``--status`` to work correctly on
    BMS firmware that otherwise times out.
    Whether those DEBUG messages are visible in the console is controlled
    separately by the application's root-handler level (``BMS_DEBUG_LOG`` in
    ``exporter.py``), so the timing workaround can be active without
    spamming the log.

    Data-fetch toggles
    ------------------
    Some BMS firmware versions do not respond correctly to every command.
    Each ``fetch_*`` constructor parameter controls whether that particular
    command is issued during :meth:`get_all_data`.  All default to ``True``.

    When *fetch_status* is ``False`` the library cannot auto-detect how many
    cells / sensors the pack has.  Supply *cell_count_override* and
    *temp_sensor_count_override* so that :meth:`get_all_data` can still
    request per-cell voltages and per-sensor temperatures.
    """

    def __init__(
        self,
        port: str,
        baud_rate: int = 9600,
        address: int = 0x40,
        timeout: float = 1.0,
        sinowealth: bool = False,
        verbose: bool = False,
        fetch_soc: bool = True,
        fetch_cell_voltage_range: bool = True,
        fetch_temperature_range: bool = True,
        fetch_mosfet_status: bool = True,
        fetch_status: bool = True,
        fetch_cell_voltages: bool = True,
        fetch_temperatures: bool = True,
        fetch_balancing: bool = True,
        fetch_errors: bool = True,
        cell_count_override: int = 0,
        temp_sensor_count_override: int = 0,
    ) -> None:
        self.port = port
        self._sinowealth = sinowealth

        # Select the logger passed to the dalybms library.
        # When verbose=True a dedicated DEBUG-level logger is used so the
        # library's debug() calls execute (timing workaround for --status).
        # Propagation to the root logger is left enabled; the root *handler*
        # level (controlled by BMS_DEBUG_LOG) decides whether the output
        # actually appears in the console.
        if verbose:
            lib_logger = logging.getLogger(_LIB_LOGGER_NAME)
            lib_logger.setLevel(logging.DEBUG)
            if not lib_logger.handlers:
                lib_logger.addHandler(logging.NullHandler())
        else:
            lib_logger = logger  # INFO level; debug calls short-circuit

        if sinowealth:
            self._lib = _DalyBMSSinowealth(logger=lib_logger)
        else:
            # Convert legacy hex-style address (e.g. 0x40) to dalybms convention.
            # dalybms: 4 = RS-485, 8 = UART/Bluetooth.
            lib_address = address >> 4 if address >= 16 else address
            if lib_address not in (4, 8):
                logger.warning(
                    "Unexpected BMS address %r (resolved to lib address %d); "
                    "defaulting to RS-485 (lib address=4).",
                    address,
                    lib_address,
                )
                lib_address = 4
            self._lib = _DalyBMSLib(address=lib_address, logger=lib_logger)

        self._fetch_soc              = fetch_soc
        self._fetch_cell_voltage_range = fetch_cell_voltage_range
        self._fetch_temperature_range  = fetch_temperature_range
        self._fetch_mosfet_status    = fetch_mosfet_status
        self._fetch_status           = fetch_status
        self._fetch_cell_voltages    = fetch_cell_voltages
        self._fetch_temperatures     = fetch_temperatures
        self._fetch_balancing        = fetch_balancing
        self._fetch_errors           = fetch_errors
        self._cell_count_override    = cell_count_override
        self._temp_sensor_count_override = temp_sensor_count_override

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Open the serial port and perform an initial status read."""
        try:
            self._lib.connect(self.port)
            logger.info("Connected to BMS on %s", self.port)
        except Exception as exc:
            raise DalyBMSError(f"Cannot connect to {self.port}: {exc}") from exc

    def disconnect(self) -> None:
        """Close the serial port."""
        try:
            self._lib.disconnect()
            logger.info("Disconnected from BMS on %s", self.port)
        except Exception:
            pass

    def __enter__(self) -> "DalyBMS":
        self.connect()
        return self

    def __exit__(self, *_) -> None:
        self.disconnect()

    # ------------------------------------------------------------------
    # Aggregated read
    # ------------------------------------------------------------------

    def get_all_data(self) -> DalyBMSData:
        """
        Query all standard BMS data and return a :class:`DalyBMSData` snapshot.

        Only the commands whose corresponding ``fetch_*`` flag is ``True``
        (set in the constructor) are issued.  Each sub-command is attempted
        independently -- a failure in one does not abort the others.

        Raises :class:`DalyBMSError` if every attempted command raised an
        exception, which typically indicates that the serial port has been
        closed or the BMS is unresponsive.  This lets the caller (e.g.
        :class:`exporter.BMSPoller`) detect a dead connection and reconnect.
        """
        data = DalyBMSData()
        n_attempted: int = 0
        n_exceptions: int = 0

        # -- SOC / pack voltage / current ------------------------------------
        if self._fetch_soc:
            n_attempted += 1
            try:
                soc = self._lib.get_soc()
                if soc:
                    data.basic = BasicStatus(
                        pack_voltage=soc["total_voltage"],
                        # acquisition_voltage not provided by the dalybms library
                        acquisition_voltage=0.0,
                        pack_current=soc["current"],
                        soc_percent=soc["soc_percent"],
                    )
            except Exception as exc:
                n_exceptions += 1
                logger.warning("Failed to read SOC: %s", exc)

        # -- Cell voltage extremes -------------------------------------------
        if self._fetch_cell_voltage_range:
            n_attempted += 1
            try:
                cvr = self._lib.get_cell_voltage_range()
                if cvr:
                    data.cell_extremes = CellVoltageExtremes(
                        max_voltage=cvr["highest_voltage"],
                        max_cell_number=cvr["highest_cell"],
                        min_voltage=cvr["lowest_voltage"],
                        min_cell_number=cvr["lowest_cell"],
                    )
            except Exception as exc:
                n_exceptions += 1
                logger.warning("Failed to read cell voltage range: %s", exc)

        # -- Temperature extremes --------------------------------------------
        if self._fetch_temperature_range:
            n_attempted += 1
            try:
                tr = self._lib.get_temperature_range()
                if tr:
                    data.temp_extremes = TemperatureExtremes(
                        max_temperature=float(tr["highest_temperature"]),
                        max_sensor_number=tr["highest_sensor"],
                        min_temperature=float(tr["lowest_temperature"]),
                        min_sensor_number=tr["lowest_sensor"],
                    )
            except Exception as exc:
                n_exceptions += 1
                logger.warning("Failed to read temperature range: %s", exc)

        # -- MOSFET status ---------------------------------------------------
        if self._fetch_mosfet_status:
            n_attempted += 1
            try:
                mos = self._lib.get_mosfet_status()
                if mos:
                    if self._sinowealth:
                        # Sinowealth returns remaining_capacity_ah and pack_state list;
                        # infer MOS states from pack_state flags.
                        pack_state = mos.get("pack_state", [])
                        data.mos = MosStatus(
                            charge_mos_on="CHGMOS: Charging enabled" in pack_state,
                            discharge_mos_on="DSGMOS: Discharging enabled" in pack_state,
                            # bms_heartbeat not available on Sinowealth
                            bms_heartbeat=0,
                            remaining_capacity_ah=mos.get("remaining_capacity_ah", 0.0),
                        )
                    else:
                        data.mos = MosStatus(
                            charge_mos_on=bool(mos["charging_mosfet"]),
                            discharge_mos_on=bool(mos["discharging_mosfet"]),
                            # bms_heartbeat not provided by the dalybms library
                            bms_heartbeat=0,
                            remaining_capacity_ah=mos["capacity_ah"],
                        )
            except Exception as exc:
                n_exceptions += 1
                logger.warning("Failed to read MOSFET status: %s", exc)

        # -- BMS status (cell count, sensors, cycles) ------------------------
        if self._fetch_status:
            n_attempted += 1
            try:
                st = self._lib.get_status()
                if st:
                    if self._sinowealth:
                        # Sinowealth only provides cycles; cell/sensor counts will be
                        # back-filled after get_cell_voltages and get_temperatures run.
                        data.status = StatusInfo(
                            cell_count=0,
                            temperature_sensor_count=0,
                            charger_connected=False,
                            load_connected=False,
                            states=0,
                            cycles=st.get("cycles", 0),
                        )
                    else:
                        data.status = StatusInfo(
                            cell_count=st["cells"],
                            temperature_sensor_count=st["temperature_sensors"],
                            charger_connected=bool(st["charger_running"]),
                            load_connected=bool(st["load_running"]),
                            # raw state bits not provided by the dalybms library
                            states=0,
                            cycles=st["cycles"],
                        )
            except Exception as exc:
                n_exceptions += 1
                logger.warning("Failed to read BMS status: %s", exc)
        elif self._cell_count_override > 0 or self._temp_sensor_count_override > 0:
            # Status fetch is disabled -- synthesise a minimal StatusInfo from
            # the overrides so per-cell and per-sensor commands still work.
            data.status = StatusInfo(
                cell_count=self._cell_count_override,
                temperature_sensor_count=self._temp_sensor_count_override,
            )

        # The dalybms library uses its internal self.status dict (populated by
        # get_status()) to calculate how many response frames to expect for
        # cell voltages and temperatures.  When FETCH_STATUS is False we prime
        # that dict with the override counts so those commands keep working.
        # Sinowealth does not use this mechanism.
        if not self._sinowealth and not self._fetch_status and data.status:
            if self._lib.status is None:
                self._lib.status = {}
            self._lib.status["cells"] = data.status.cell_count
            self._lib.status["temperature_sensors"] = data.status.temperature_sensor_count

        # -- Per-cell voltages -----------------------------------------------
        if self._fetch_cell_voltages:
            n_attempted += 1
            try:
                cv = self._lib.get_cell_voltages()
                if cv:
                    data.cell_voltages = [cv[k] for k in sorted(cv.keys())]
            except Exception as exc:
                n_exceptions += 1
                logger.warning("Failed to read cell voltages: %s", exc)

        # -- Per-sensor temperatures -----------------------------------------
        if self._fetch_temperatures:
            n_attempted += 1
            try:
                temps = self._lib.get_temperatures()
                if temps:
                    # Both regular and Sinowealth return a dict; values are sorted
                    # by key (numbered for regular, named "external1"/"external2"
                    # for Sinowealth) — sorted() works correctly for both.
                    data.temperatures = [temps[k] for k in sorted(temps.keys())]
            except Exception as exc:
                n_exceptions += 1
                logger.warning("Failed to read temperatures: %s", exc)

        # Back-fill Sinowealth status cell/sensor counts from actual data.
        if self._sinowealth and data.status is not None:
            data.status.cell_count = len(data.cell_voltages)
            data.status.temperature_sensor_count = len(data.temperatures)

        # -- Cell balancing status -------------------------------------------
        if self._fetch_balancing:
            n_attempted += 1
            try:
                bal = self._lib.get_balancing_status()
                if bal and "error" not in bal and data.status:
                    data.cell_balance_active = [
                        bool(bal.get(i, False))
                        for i in range(1, data.status.cell_count + 1)
                    ]
                elif data.status:
                    data.cell_balance_active = [False] * data.status.cell_count
            except Exception as exc:
                n_exceptions += 1
                logger.warning("Failed to read balancing status: %s", exc)

        # -- Failure / alarm flags -------------------------------------------
        if self._fetch_errors:
            n_attempted += 1
            try:
                if self._sinowealth:
                    # Sinowealth returns a list of human-readable error strings;
                    # FailureFlags bit-fields are not applicable.
                    errors = self._lib.get_errors()
                    if errors:
                        logger.warning("BMS errors reported: %s", ", ".join(errors))
                else:
                    # NOTE: _read_request is a private dalybms method; it returns the
                    # raw 8-byte payload that get_errors() would receive.  We parse it
                    # directly to populate the structured FailureFlags dataclass.
                    raw_errors = self._lib._read_request("98")
                    if raw_errors is not False and raw_errors:
                        data.failures = _parse_failure_flags(raw_errors)
            except Exception as exc:
                n_exceptions += 1
                logger.warning("Failed to read failure flags: %s", exc)

        if n_attempted > 0 and n_exceptions == n_attempted:
            raise DalyBMSError(
                "Every BMS command raised an exception; the serial port may "
                "be closed or the BMS unresponsive."
            )

        return data
