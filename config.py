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


# ---------------------------------------------------------------------------
# Serial / RS-485 interface settings
# ---------------------------------------------------------------------------

# Path to the USB-to-RS485 serial device.
# Examples: /dev/ttyUSB0  (Linux)
#           /dev/tty.usbserial-XXXX  (macOS)
#           COM3  (Windows)
SERIAL_PORT: str = os.environ.get("SERIAL_PORT", "/dev/ttyUSB0")

# Baud rate for the RS-485 link.  Daly BMS units use 9600 baud by default.
BAUD_RATE: int = int(os.environ.get("BAUD_RATE", "9600"))

# Serial read timeout in seconds.
SERIAL_TIMEOUT: float = float(os.environ.get("SERIAL_TIMEOUT", "1.0"))


# ---------------------------------------------------------------------------
# Daly BMS device settings
# ---------------------------------------------------------------------------

# RS-485 device address of the BMS.
# The factory default is 0x40 (decimal 64).
# Accepts a hex string (e.g. "0x40") or a decimal string (e.g. "64").
_bms_addr_raw: str = os.environ.get("BMS_ADDRESS", "0x40")
BMS_ADDRESS: int = (
    int(_bms_addr_raw, 16)
    if _bms_addr_raw.startswith("0x") or _bms_addr_raw.startswith("0X")
    else int(_bms_addr_raw)
)

# Human-readable model name — used as a Prometheus label so you can tell
# multiple BMS units apart in Grafana.
BMS_MODEL: str = os.environ.get("BMS_MODEL", "Daly BMS")

# An optional instance identifier for setups with more than one BMS.
BMS_INSTANCE: str = os.environ.get("BMS_INSTANCE", "bms0")


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

# Number of consecutive communication errors before the exporter marks the BMS
# as unreachable (daly_bms_up == 0) and logs a warning.
MAX_CONSECUTIVE_ERRORS: int = int(os.environ.get("MAX_CONSECUTIVE_ERRORS", "3"))
