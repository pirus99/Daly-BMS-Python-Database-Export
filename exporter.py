"""
Daly BMS Prometheus Exporter – main entry point.

Starts an HTTP server that serves Prometheus-formatted metrics at the
configured endpoint path, and a background thread that periodically polls the
BMS for fresh data.

Usage:
    python exporter.py

All settings are read from config.py (which itself falls back to environment
variables and an optional .env file).
"""

import logging
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Info,
    generate_latest,
)

import config
from daly_bms import DalyBMS, DalyBMSData, DalyBMSError, _LIB_LOGGER_NAME

# ---------------------------------------------------------------------------
# Logging
#
# Root logger is set to DEBUG so that child loggers (notably the dalybms
# library when BMS_VERBOSE is on) can generate records.  Output is gated by
# the *handler* level:
#   - default : INFO  (prevents debug-frame spam in production)
#   - BMS_DEBUG_LOG=true → lowered to DEBUG in main() so debug output appears
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
# Default gate: only INFO+ printed until BMS_DEBUG_LOG lowers it.
for _h in logging.getLogger().handlers:
    if _h.level == logging.NOTSET:
        _h.setLevel(logging.INFO)
logger = logging.getLogger("daly_bms_exporter")


# ---------------------------------------------------------------------------
# Prometheus metric definitions
# ---------------------------------------------------------------------------

_LABELS = ["model", "instance"]
_LABEL_VALUES = [config.BMS_MODEL, config.BMS_INSTANCE]

# -- BMS info ----------------------------------------------------------------
bms_info = Info(
    "daly_bms",
    "Static information about the Daly BMS instance",
)
bms_info.info(
    {
        "model": config.BMS_MODEL,
        "instance": config.BMS_INSTANCE,
        "serial_port": config.SERIAL_PORT,
        "connection_mode": "uart" if config.BMS_UART else "rs485",
    }
)

# -- Availability / scrape quality -------------------------------------------
bms_up = Gauge(
    "daly_bms_up",
    "1 if the BMS is reachable and returning valid data, 0 otherwise",
    _LABELS,
)
scrape_duration = Gauge(
    "daly_bms_scrape_duration_seconds",
    "Time taken to complete one full BMS data poll",
    _LABELS,
)
scrape_errors_total = Counter(
    "daly_bms_scrape_errors_total",
    "Total number of BMS communication errors since exporter start",
    _LABELS,
)

# -- Pack-level electrical ---------------------------------------------------
pack_voltage = Gauge(
    "daly_bms_pack_voltage_volts",
    "Battery pack voltage (V)",
    _LABELS,
)
acquisition_voltage = Gauge(
    "daly_bms_acquisition_voltage_volts",
    "Acquisition/terminal voltage (V)",
    _LABELS,
)
pack_current = Gauge(
    "daly_bms_pack_current_amperes",
    "Pack current (A); positive = charging, negative = discharging",
    _LABELS,
)
soc_percent = Gauge(
    "daly_bms_soc_percent",
    "State of charge (%)",
    _LABELS,
)
remaining_capacity = Gauge(
    "daly_bms_remaining_capacity_ampere_hours",
    "Estimated remaining capacity (Ah)",
    _LABELS,
)

# -- Cell voltage extremes ---------------------------------------------------
cell_voltage_max = Gauge(
    "daly_bms_cell_voltage_max_volts",
    "Highest individual cell voltage (V)",
    _LABELS,
)
cell_voltage_min = Gauge(
    "daly_bms_cell_voltage_min_volts",
    "Lowest individual cell voltage (V)",
    _LABELS,
)
cell_voltage_max_cell = Gauge(
    "daly_bms_cell_voltage_max_cell_number",
    "Cell number with the highest voltage",
    _LABELS,
)
cell_voltage_min_cell = Gauge(
    "daly_bms_cell_voltage_min_cell_number",
    "Cell number with the lowest voltage",
    _LABELS,
)
cell_voltage_delta = Gauge(
    "daly_bms_cell_voltage_delta_volts",
    "Difference between max and min cell voltages (V)",
    _LABELS,
)

# -- Temperature extremes ----------------------------------------------------
temperature_max = Gauge(
    "daly_bms_temperature_max_celsius",
    "Highest temperature reading (°C)",
    _LABELS,
)
temperature_min = Gauge(
    "daly_bms_temperature_min_celsius",
    "Lowest temperature reading (°C)",
    _LABELS,
)

# -- MOS / protection status -------------------------------------------------
charge_mos_on = Gauge(
    "daly_bms_charge_mos_active",
    "1 if the charge MOSFET is enabled, 0 if disabled",
    _LABELS,
)
discharge_mos_on = Gauge(
    "daly_bms_discharge_mos_active",
    "1 if the discharge MOSFET is enabled, 0 if disabled",
    _LABELS,
)
bms_heartbeat = Gauge(
    "daly_bms_heartbeat_counter",
    "BMS internal heartbeat counter (increments with each communication)",
    _LABELS,
)

# -- Status ------------------------------------------------------------------
cell_count = Gauge(
    "daly_bms_cell_count",
    "Number of cells reported by the BMS",
    _LABELS,
)
temp_sensor_count = Gauge(
    "daly_bms_temperature_sensor_count",
    "Number of temperature sensors reported by the BMS",
    _LABELS,
)
charger_connected = Gauge(
    "daly_bms_charger_connected",
    "1 if a charger is connected",
    _LABELS,
)
load_connected = Gauge(
    "daly_bms_load_connected",
    "1 if a load is connected",
    _LABELS,
)
cycles_total = Gauge(
    "daly_bms_charge_discharge_cycles_total",
    "Total charge/discharge cycle count",
    _LABELS,
)

# -- Per-cell metrics (labelled by cell number) ------------------------------
_CELL_LABELS = _LABELS + ["cell"]
cell_voltage = Gauge(
    "daly_bms_cell_voltage_volts",
    "Individual cell voltage (V)",
    _CELL_LABELS,
)
cell_balance_active = Gauge(
    "daly_bms_cell_balance_active",
    "1 if cell balancing is currently active for this cell",
    _CELL_LABELS,
)

# -- Per-sensor temperature --------------------------------------------------
_SENSOR_LABELS = _LABELS + ["sensor"]
sensor_temperature = Gauge(
    "daly_bms_temperature_celsius",
    "Individual temperature sensor reading (°C)",
    _SENSOR_LABELS,
)

# -- Alarm / fault flags (individual bits) -----------------------------------
_FLAG_LABELS = _LABELS + ["alarm"]
alarm_flag = Gauge(
    "daly_bms_alarm_active",
    "1 when the named alarm condition is active, 0 otherwise",
    _FLAG_LABELS,
)

# Mapping: FailureFlags attribute name → human-readable alarm label
_ALARM_ATTRS = {
    "cell_overvoltage_alarm_l1": "cell_overvoltage_l1",
    "cell_overvoltage_alarm_l2": "cell_overvoltage_l2",
    "cell_undervoltage_alarm_l1": "cell_undervoltage_l1",
    "cell_undervoltage_alarm_l2": "cell_undervoltage_l2",
    "pack_overvoltage_alarm_l1": "pack_overvoltage_l1",
    "pack_overvoltage_alarm_l2": "pack_overvoltage_l2",
    "pack_undervoltage_alarm_l1": "pack_undervoltage_l1",
    "pack_undervoltage_alarm_l2": "pack_undervoltage_l2",
    "charge_overtemp_alarm_l1": "charge_overtemp_l1",
    "charge_overtemp_alarm_l2": "charge_overtemp_l2",
    "charge_undertemp_alarm_l1": "charge_undertemp_l1",
    "charge_undertemp_alarm_l2": "charge_undertemp_l2",
    "discharge_overtemp_alarm_l1": "discharge_overtemp_l1",
    "discharge_overtemp_alarm_l2": "discharge_overtemp_l2",
    "discharge_undertemp_alarm_l1": "discharge_undertemp_l1",
    "discharge_undertemp_alarm_l2": "discharge_undertemp_l2",
    "charge_overcurrent_alarm_l1": "charge_overcurrent_l1",
    "charge_overcurrent_alarm_l2": "charge_overcurrent_l2",
    "discharge_overcurrent_alarm_l1": "discharge_overcurrent_l1",
    "discharge_overcurrent_alarm_l2": "discharge_overcurrent_l2",
    "soc_low_alarm_l1": "soc_low_l1",
    "soc_low_alarm_l2": "soc_low_l2",
    "cell_voltage_diff_alarm_l1": "cell_voltage_diff_l1",
    "cell_voltage_diff_alarm_l2": "cell_voltage_diff_l2",
    "cell_temp_diff_alarm_l1": "cell_temp_diff_l1",
    "cell_temp_diff_alarm_l2": "cell_temp_diff_l2",
    "charge_mos_overtemp_alarm_l1": "charge_mos_overtemp_l1",
    "charge_mos_overtemp_alarm_l2": "charge_mos_overtemp_l2",
    "discharge_mos_overtemp_alarm_l1": "discharge_mos_overtemp_l1",
    "discharge_mos_overtemp_alarm_l2": "discharge_mos_overtemp_l2",
    "charge_mos_fault": "charge_mos_fault",
    "discharge_mos_fault": "discharge_mos_fault",
    "temp_sensor_fault": "temp_sensor_fault",
    "cell_fault": "cell_fault",
    "sampling_circuit_fault": "sampling_circuit_fault",
    "cell_balance_fault": "cell_balance_fault",
}


# ---------------------------------------------------------------------------
# Metric update helpers
# ---------------------------------------------------------------------------

def _update_metrics(data: DalyBMSData) -> None:
    """Push a :class:`DalyBMSData` snapshot into the Prometheus metric store."""

    lv = _LABEL_VALUES  # shortcut

    if data.basic:
        pack_voltage.labels(*lv).set(data.basic.pack_voltage)
        acquisition_voltage.labels(*lv).set(data.basic.acquisition_voltage)
        pack_current.labels(*lv).set(data.basic.pack_current)
        soc_percent.labels(*lv).set(data.basic.soc_percent)

    if data.cell_extremes:
        cell_voltage_max.labels(*lv).set(data.cell_extremes.max_voltage)
        cell_voltage_min.labels(*lv).set(data.cell_extremes.min_voltage)
        cell_voltage_max_cell.labels(*lv).set(data.cell_extremes.max_cell_number)
        cell_voltage_min_cell.labels(*lv).set(data.cell_extremes.min_cell_number)
        delta = data.cell_extremes.max_voltage - data.cell_extremes.min_voltage
        cell_voltage_delta.labels(*lv).set(round(delta, 4))

    if data.temp_extremes:
        temperature_max.labels(*lv).set(data.temp_extremes.max_temperature)
        temperature_min.labels(*lv).set(data.temp_extremes.min_temperature)

    if data.mos:
        charge_mos_on.labels(*lv).set(int(data.mos.charge_mos_on))
        discharge_mos_on.labels(*lv).set(int(data.mos.discharge_mos_on))
        bms_heartbeat.labels(*lv).set(data.mos.bms_heartbeat)
        remaining_capacity.labels(*lv).set(data.mos.remaining_capacity_ah)

    if data.status:
        cell_count.labels(*lv).set(data.status.cell_count)
        temp_sensor_count.labels(*lv).set(data.status.temperature_sensor_count)
        charger_connected.labels(*lv).set(int(data.status.charger_connected))
        load_connected.labels(*lv).set(int(data.status.load_connected))
        cycles_total.labels(*lv).set(data.status.cycles)

    for idx, voltage in enumerate(data.cell_voltages, start=1):
        cell_voltage.labels(*lv, str(idx)).set(voltage)

    for idx, temp in enumerate(data.temperatures, start=1):
        sensor_temperature.labels(*lv, str(idx)).set(temp)

    for idx, active in enumerate(data.cell_balance_active, start=1):
        cell_balance_active.labels(*lv, str(idx)).set(int(active))

    if data.failures:
        for attr, label in _ALARM_ATTRS.items():
            alarm_flag.labels(*lv, label).set(int(getattr(data.failures, attr, False)))


# ---------------------------------------------------------------------------
# Background polling thread
# ---------------------------------------------------------------------------

class BMSPoller(threading.Thread):
    """
    Daemon thread that polls the BMS every :attr:`interval` seconds.

    Each poll runs in a short-lived worker thread.  If the worker has not
    finished within *poll_timeout* seconds the serial port is closed to
    unblock any pending read, a warning is logged, and the poller moves on to
    the next interval.  This ensures no two polls ever run concurrently.
    """

    def __init__(self, bms: DalyBMS, interval: float, poll_timeout: float) -> None:
        super().__init__(name="bms-poller", daemon=True)
        self._bms = bms
        self._interval = interval
        self._poll_timeout = poll_timeout
        self._consecutive_errors = 0
        self._needs_reconnect = False

    def run(self) -> None:
        logger.info(
            "BMS poller started — interval=%.1f s, timeout=%.1f s",
            self._interval,
            self._poll_timeout,
        )
        while True:
            start = time.monotonic()

            # Run the actual BMS communication in a short-lived daemon thread
            # so we can apply a hard timeout without blocking the poller loop.
            poll_result: dict = {}

            def _poll() -> None:
                try:
                    if self._needs_reconnect:
                        self._bms.connect()
                        self._needs_reconnect = False
                    poll_result["data"] = self._bms.get_all_data()
                except Exception as exc:  # noqa: BLE001
                    poll_result["error"] = exc

            worker = threading.Thread(target=_poll, daemon=True, name="bms-poll-worker")
            worker.start()
            worker.join(timeout=self._poll_timeout)
            elapsed = time.monotonic() - start

            if worker.is_alive():
                # The poll thread is still blocked (likely on a serial read).
                self._consecutive_errors += 1
                scrape_errors_total.labels(*_LABEL_VALUES).inc()
                scrape_duration.labels(*_LABEL_VALUES).set(elapsed)
                bms_up.labels(*_LABEL_VALUES).set(0)
                logger.warning(
                    "BMS poll timed out after %.1f s (timeout=%.1f s); "
                    "closing serial port to interrupt the hung request.",
                    elapsed,
                    self._poll_timeout,
                )
                # Close the serial port so the worker's pending read raises
                # a SerialException and the thread can exit cleanly.
                try:
                    self._bms.disconnect()
                except Exception:
                    pass
                # Give the worker a moment to exit; proceed regardless.
                worker.join(timeout=1.0)
                if worker.is_alive():
                    logger.warning("Poll worker did not exit after disconnect; proceeding.")
                # Reconnect on the next poll so communication can resume.
                self._needs_reconnect = True

            elif "error" in poll_result:
                exc = poll_result["error"]
                self._consecutive_errors += 1
                scrape_errors_total.labels(*_LABEL_VALUES).inc()
                scrape_duration.labels(*_LABEL_VALUES).set(elapsed)
                # Schedule a reconnect for the next poll; this restores the
                # serial connection after a timeout-triggered disconnect or any
                # other communication failure (e.g. USB hot-unplug).
                self._needs_reconnect = True
                if self._consecutive_errors >= config.MAX_CONSECUTIVE_ERRORS:
                    bms_up.labels(*_LABEL_VALUES).set(0)
                    logger.error(
                        "BMS unreachable after %d consecutive errors: %s",
                        self._consecutive_errors,
                        exc,
                    )
                else:
                    logger.warning("BMS poll error (%d): %s", self._consecutive_errors, exc)

            elif "data" in poll_result:
                data = poll_result["data"]
                if data is not None:
                    _update_metrics(data)
                    self._consecutive_errors = 0
                    bms_up.labels(*_LABEL_VALUES).set(1)
                    scrape_duration.labels(*_LABEL_VALUES).set(elapsed)
                    logger.debug("Polled BMS in %.3f s", elapsed)

            # Sleep for whatever remains of the poll interval.
            sleep_time = max(0.0, self._interval - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class MetricsHandler(BaseHTTPRequestHandler):
    """Minimal HTTP request handler that serves Prometheus metrics."""

    def do_GET(self) -> None:  # noqa: N802
        if self.path == config.METRICS_PATH:
            output = generate_latest()
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_LATEST)
            self.send_header("Content-Length", str(len(output)))
            self.end_headers()
            self.wfile.write(output)
        elif self.path == "/":
            body = (
                b"<html><body>"
                b"<h1>Daly BMS Prometheus Exporter</h1>"
                b'<p><a href="' + config.METRICS_PATH.encode() + b'">'
                + config.METRICS_PATH.encode()
                + b"</a></p></body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            body = b"Not Found"
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: N802
        logger.debug("HTTP %s", fmt % args)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    # BMS_DEBUG_LOG: lower the root handler level to DEBUG so debug output
    # (including dalybms verbose frames when BMS_VERBOSE is also true) appears
    # in the console / logfiles.  Without this flag only INFO+ is printed.
    if config.BMS_DEBUG_LOG:
        for h in logging.getLogger().handlers:
            h.setLevel(logging.DEBUG)

    logger.info("=== Daly BMS Prometheus Exporter ===")
    logger.info("Model      : %s", config.BMS_MODEL)
    logger.info("Instance   : %s", config.BMS_INSTANCE)
    logger.info(
        "Port       : %s (%s%s)",
        config.SERIAL_PORT,
        "UART" if config.BMS_UART else "RS-485",
        ", Sinowealth" if config.BMS_SINOWEALTH else "",
    )
    logger.info("Poll       : %.1f s (timeout: %.1f s)", config.POLL_INTERVAL, config.POLL_TIMEOUT)
    logger.info(
        "BMS_VERBOSE: %s  BMS_DEBUG_LOG: %s",
        "on" if config.BMS_VERBOSE else "off",
        "on" if config.BMS_DEBUG_LOG else "off",
    )
    logger.info(
        "Metrics    : http://%s:%d%s",
        config.WEB_SERVER_ADDRESS,
        config.WEB_SERVER_PORT,
        config.METRICS_PATH,
    )

    if config.BMS_VERBOSE and not config.BMS_DEBUG_LOG:
        logger.info(
            "BMS_VERBOSE is on (--status timing workaround active); "
            "set BMS_DEBUG_LOG=true to see the raw serial frames."
        )

    # Log which data categories are enabled / disabled.
    fetch_map = {
        "soc": config.FETCH_SOC,
        "cell_voltage_range": config.FETCH_CELL_VOLTAGE_RANGE,
        "temperature_range": config.FETCH_TEMPERATURE_RANGE,
        "mosfet_status": config.FETCH_MOSFET_STATUS,
        "status": config.FETCH_STATUS,
        "cell_voltages": config.FETCH_CELL_VOLTAGES,
        "temperatures": config.FETCH_TEMPERATURES,
        "balancing": config.FETCH_BALANCING,
        "errors": config.FETCH_ERRORS,
    }
    disabled = [k for k, v in fetch_map.items() if not v]
    if disabled:
        logger.info("Disabled data categories: %s", ", ".join(disabled))

    # dalybms address: 4 = RS-485, 8 = UART/Bluetooth (ignored for Sinowealth)
    bms_address = 8 if config.BMS_UART else 4
    bms = DalyBMS(
        port=config.SERIAL_PORT,
        address=bms_address,
        sinowealth=config.BMS_SINOWEALTH,
        verbose=config.BMS_VERBOSE,
        fetch_soc=config.FETCH_SOC,
        fetch_cell_voltage_range=config.FETCH_CELL_VOLTAGE_RANGE,
        fetch_temperature_range=config.FETCH_TEMPERATURE_RANGE,
        fetch_mosfet_status=config.FETCH_MOSFET_STATUS,
        fetch_status=config.FETCH_STATUS,
        fetch_cell_voltages=config.FETCH_CELL_VOLTAGES,
        fetch_temperatures=config.FETCH_TEMPERATURES,
        fetch_balancing=config.FETCH_BALANCING,
        fetch_errors=config.FETCH_ERRORS,
        cell_count_override=config.BMS_CELL_COUNT,
        temp_sensor_count_override=config.BMS_TEMP_SENSOR_COUNT,
    )

    try:
        bms.connect()
    except DalyBMSError as exc:
        logger.error("Could not connect to BMS: %s", exc)
        logger.error(
            "Please check SERIAL_PORT in config.py / .env and verify that "
            "the serial adapter is connected."
        )
        sys.exit(1)

    # Initialise availability metric as unknown before first poll.
    bms_up.labels(*_LABEL_VALUES).set(0)

    # Start background poller.
    poller = BMSPoller(bms, interval=config.POLL_INTERVAL, poll_timeout=config.POLL_TIMEOUT)
    poller.start()

    # Start HTTP server (blocks until Ctrl-C).
    server = HTTPServer((config.WEB_SERVER_ADDRESS, config.WEB_SERVER_PORT), MetricsHandler)
    logger.info(
        "HTTP server listening on %s:%d",
        config.WEB_SERVER_ADDRESS,
        config.WEB_SERVER_PORT,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down …")
    finally:
        server.server_close()
        bms.disconnect()


if __name__ == "__main__":
    main()
