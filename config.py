"""
Configuration for the Daly BMS Prometheus Exporter.

All values can be overridden by environment variables (or a .env file in the
same directory).  The name of each variable in the .env file / environment is
the same as the Python constant below.
"""

import os

# ---------------------------------------------------------------------------
# Load .env file if present (requires python-dotenv, which is in
# requirements.txt).  Values already present in the environment take
# precedence over those in .env.
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv is listed in requirements.txt; this is a safety net


def _bool(name: str, default: bool) -> bool:
    """Read a boolean environment variable (true/1/yes = True, empty = default)."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Serial / RS-485 interface settings
# ---------------------------------------------------------------------------

# Path to the USB-to-RS485 (or UART) serial device.
# Examples: /dev/ttyUSB0  (Linux)
#           /dev/tty.usbserial-XXXX  (macOS)
#           COM3  (Windows)
SERIAL_PORT: str = os.environ.get("SERIAL_PORT", "/dev/ttyUSB0")

# Set to "true" to communicate via UART instead of RS-485.
# This changes the dalybms library address from 4 (RS-485) to 8 (UART/Bluetooth).
BMS_UART: bool = _bool("BMS_UART", False)

# Set to "true" to enable verbose (DEBUG) mode in the dalybms library.
# This is a workaround for BMS firmware versions where the status command does
# not respond correctly at normal timing: the dalybms library's internal
# logger.debug() calls add small delays between serial operations that make
# the firmware respond reliably.
# Note: this flag does NOT control whether debug output appears in the log —
# use BMS_DEBUG_LOG for that.  With BMS_VERBOSE=true and BMS_DEBUG_LOG=false
# the timing workaround is active but the verbose frames are silently discarded.
BMS_VERBOSE: bool = _bool("BMS_VERBOSE", False)

# Set to "true" to show DEBUG-level output in the console / log files.
# When false (default) only INFO and above messages are printed, preventing
# log spam from the dalybms library's verbose frame output.
# Set both BMS_VERBOSE=true and BMS_DEBUG_LOG=true to see the raw serial frames
# sent/received by the dalybms library.
BMS_DEBUG_LOG: bool = _bool("BMS_DEBUG_LOG", False)

# Set to "true" when the BMS uses a Sinowealth chip instead of the standard
# Daly chip.  This selects the dalybms.DalyBMSSinowealth driver class.
BMS_SINOWEALTH: bool = _bool("BMS_SINOWEALTH", False)

# ---------------------------------------------------------------------------
# Daly BMS device settings
# ---------------------------------------------------------------------------

# Human-readable model name — used as a Prometheus label so you can tell
# multiple BMS units apart in Grafana.
BMS_MODEL: str = os.environ.get("BMS_MODEL", "Daly BMS")

# An optional instance identifier for setups with more than one BMS.
BMS_INSTANCE: str = os.environ.get("BMS_INSTANCE", "bms0")

# ---------------------------------------------------------------------------
# Data-fetch toggles
#
# Some BMS firmware versions do not respond correctly to every command.
# Set any of the flags below to "false" to skip that command entirely.
#
# Note: FETCH_CELL_VOLTAGES and FETCH_TEMPERATURES depend on knowing the
# number of cells/sensors.  When FETCH_STATUS is disabled you must supply
# BMS_CELL_COUNT and BMS_TEMP_SENSOR_COUNT so the correct number of frames
# is requested.
# ---------------------------------------------------------------------------

FETCH_SOC: bool                 = _bool("FETCH_SOC", True)
FETCH_CELL_VOLTAGE_RANGE: bool  = _bool("FETCH_CELL_VOLTAGE_RANGE", True)
FETCH_TEMPERATURE_RANGE: bool   = _bool("FETCH_TEMPERATURE_RANGE", True)
FETCH_MOSFET_STATUS: bool       = _bool("FETCH_MOSFET_STATUS", True)
FETCH_STATUS: bool              = _bool("FETCH_STATUS", True)
FETCH_CELL_VOLTAGES: bool       = _bool("FETCH_CELL_VOLTAGES", True)
FETCH_TEMPERATURES: bool        = _bool("FETCH_TEMPERATURES", True)
FETCH_BALANCING: bool           = _bool("FETCH_BALANCING", True)
FETCH_ERRORS: bool              = _bool("FETCH_ERRORS", True)

# Fallback cell / sensor counts used when FETCH_STATUS is false.
# Set these to the actual values for your pack so that FETCH_CELL_VOLTAGES
# and FETCH_TEMPERATURES still work.  0 means "skip those commands too".
BMS_CELL_COUNT: int        = int(os.environ.get("BMS_CELL_COUNT", "0"))
BMS_TEMP_SENSOR_COUNT: int = int(os.environ.get("BMS_TEMP_SENSOR_COUNT", "0"))


# ---------------------------------------------------------------------------
# Prometheus web server settings
# ---------------------------------------------------------------------------

# Address the HTTP server binds to.
# Use "0.0.0.0" to listen on all interfaces, or "127.0.0.1" for localhost only.
WEB_SERVER_ADDRESS: str = os.environ.get("WEB_SERVER_ADDRESS", "0.0.0.0")

# TCP port for the metrics HTTP server.
WEB_SERVER_PORT: int = int(os.environ.get("WEB_SERVER_PORT", "8000"))

# URL path that serves the Prometheus metrics (the /metrics endpoint).
METRICS_PATH: str = os.environ.get("METRICS_PATH", "/metrics")


# ---------------------------------------------------------------------------
# Polling / scrape settings
# ---------------------------------------------------------------------------

# How often (in seconds) the BMS is polled for fresh data.
POLL_INTERVAL: float = float(os.environ.get("POLL_INTERVAL", "10.0"))

# Maximum seconds a single poll cycle may run before it is considered hung.
# When exceeded the serial port is closed to interrupt the blocked read, a
# warning is logged, and the next poll starts at the normal interval.
# Defaults to POLL_INTERVAL so a hung poll never overlaps the next one.
POLL_TIMEOUT: float = float(os.environ.get("POLL_TIMEOUT", str(POLL_INTERVAL)))

# Number of consecutive communication errors before the exporter marks the BMS
# as unreachable (daly_bms_up == 0) and logs a warning.
MAX_CONSECUTIVE_ERRORS: int = int(os.environ.get("MAX_CONSECUTIVE_ERRORS", "3"))
