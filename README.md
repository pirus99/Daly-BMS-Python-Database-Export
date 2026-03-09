# Daly BMS Prometheus Exporter

A Python application that reads data from a **Daly BMS** (Battery Management System) via a USB-to-RS485 interface and exposes all metrics through a **Prometheus-compatible HTTP endpoint**.

---

## Features

- Reads all standard Daly BMS data frames (commands `0x90` – `0x98`):
  - Pack voltage, acquisition voltage, current, and State of Charge (SOC)
  - Min/max individual cell voltages and their cell numbers
  - Min/max temperature sensor readings
  - Charge and discharge MOSFET states
  - BMS heartbeat counter and remaining capacity
  - Individual cell voltages (labelled `cell="1"` … `cell="N"`)
  - Individual temperature sensor readings
  - Per-cell balance-active flags
  - Full alarm and fault flag decode (34 named bits)
- Serves all metrics at a configurable Prometheus `/metrics` endpoint
- Configurable via a `.env` file or environment variables (no code changes needed)
- Graceful handling of transient communication errors with `daly_bms_up` availability metric
- Supports multiple BMS instances via `BMS_INSTANCE` label

---

## Requirements

- Python 3.8+
- A USB-to-RS485 adapter (e.g. CH340/CP2102 based)
- Daly BMS connected via RS-485

### Python dependencies

```
pyserial>=3.5
prometheus-client>=0.17.0
python-dotenv>=1.0.0
```

Install them with:

```bash
pip install -r requirements.txt
```

---

## Quick start

1. **Clone and install dependencies**

   ```bash
   git clone https://github.com/pirus99/Daly-BMS-Python-Database-Export.git
   cd Daly-BMS-Python-Database-Export
   pip install -r requirements.txt
   ```

2. **Configure the application**

   Copy the example environment file and edit it for your setup:

   ```bash
   cp .env.example .env
   $EDITOR .env
   ```

   At minimum you need to set `SERIAL_PORT` to the device path of your
   USB-to-RS485 adapter (see [Configuration](#configuration) below).

3. **Run the exporter**

   ```bash
   python exporter.py
   ```

   You should see output like:

   ```
   2024-01-15T12:00:00  INFO      daly_bms_exporter  === Daly BMS Prometheus Exporter ===
   2024-01-15T12:00:00  INFO      daly_bms_exporter  Model    : Daly BMS
   2024-01-15T12:00:00  INFO      daly_bms_exporter  Port     : /dev/ttyUSB0 @ 9600 baud
   2024-01-15T12:00:00  INFO      daly_bms_exporter  Metrics  : http://0.0.0.0:8000/metrics
   ```

4. **Scrape with Prometheus**

   Add the exporter to your `prometheus.yml`:

   ```yaml
   scrape_configs:
     - job_name: daly_bms
       static_configs:
         - targets: ['localhost:8000']
   ```

5. **Visualise in Grafana**

   Use the exposed labels (`model`, `instance`, `cell`, `sensor`, `alarm`) to
   build dashboards.  All metrics are prefixed with `daly_bms_`.

---

## Configuration

All configuration is done via environment variables.  If a `.env` file exists
in the same directory as `exporter.py`, it is loaded automatically.
Environment variables set in the shell take precedence over `.env`.

| Variable | Default | Description |
|---|---|---|
| `SERIAL_PORT` | `/dev/ttyUSB0` | Path to the USB-to-RS485 device (e.g. `COM3` on Windows) |
| `BAUD_RATE` | `9600` | RS-485 baud rate (Daly default) |
| `SERIAL_TIMEOUT` | `1.0` | Serial read timeout in seconds |
| `BMS_ADDRESS` | `0x40` | RS-485 address of the BMS (hex or decimal) |
| `BMS_MODEL` | `Daly BMS` | Human-readable model name (used as a Prometheus label) |
| `BMS_INSTANCE` | `bms0` | Instance identifier for multi-BMS setups |
| `WEB_SERVER_ADDRESS` | `0.0.0.0` | Address the HTTP server binds to |
| `WEB_SERVER_PORT` | `8000` | TCP port for the metrics endpoint |
| `METRICS_PATH` | `/metrics` | URL path served to Prometheus |
| `POLL_INTERVAL` | `10.0` | How often the BMS is polled (seconds) |
| `MAX_CONSECUTIVE_ERRORS` | `3` | Errors before `daly_bms_up` is set to 0 |

---

## Exposed Prometheus metrics

### Availability

| Metric | Type | Description |
|---|---|---|
| `daly_bms_up` | Gauge | `1` if BMS is reachable, `0` otherwise |
| `daly_bms_scrape_duration_seconds` | Gauge | Time to complete one full poll |
| `daly_bms_scrape_errors_total` | Counter | Total communication errors since start |

### Electrical

| Metric | Type | Description |
|---|---|---|
| `daly_bms_pack_voltage_volts` | Gauge | Battery pack voltage (V) |
| `daly_bms_acquisition_voltage_volts` | Gauge | Acquisition / terminal voltage (V) |
| `daly_bms_pack_current_amperes` | Gauge | Pack current (A); positive = charging |
| `daly_bms_soc_percent` | Gauge | State of charge (%) |
| `daly_bms_remaining_capacity_ampere_hours` | Gauge | Remaining capacity (Ah) |

### Cell voltages

| Metric | Labels | Description |
|---|---|---|
| `daly_bms_cell_voltage_max_volts` | `model`, `instance` | Highest cell voltage (V) |
| `daly_bms_cell_voltage_min_volts` | `model`, `instance` | Lowest cell voltage (V) |
| `daly_bms_cell_voltage_delta_volts` | `model`, `instance` | Max − min cell voltage (V) |
| `daly_bms_cell_voltage_max_cell_number` | `model`, `instance` | Cell number with highest voltage |
| `daly_bms_cell_voltage_min_cell_number` | `model`, `instance` | Cell number with lowest voltage |
| `daly_bms_cell_voltage_volts` | `model`, `instance`, `cell` | Per-cell voltage (V) |

### Temperatures

| Metric | Labels | Description |
|---|---|---|
| `daly_bms_temperature_max_celsius` | `model`, `instance` | Highest temperature (°C) |
| `daly_bms_temperature_min_celsius` | `model`, `instance` | Lowest temperature (°C) |
| `daly_bms_temperature_celsius` | `model`, `instance`, `sensor` | Per-sensor temperature (°C) |

### Status

| Metric | Type | Description |
|---|---|---|
| `daly_bms_charge_mos_active` | Gauge | `1` if charge MOS is enabled |
| `daly_bms_discharge_mos_active` | Gauge | `1` if discharge MOS is enabled |
| `daly_bms_heartbeat_counter` | Gauge | BMS internal heartbeat counter |
| `daly_bms_cell_count` | Gauge | Number of cells |
| `daly_bms_temperature_sensor_count` | Gauge | Number of temperature sensors |
| `daly_bms_charger_connected` | Gauge | `1` if a charger is connected |
| `daly_bms_load_connected` | Gauge | `1` if a load is connected |
| `daly_bms_charge_discharge_cycles_total` | Gauge | Total charge/discharge cycles |
| `daly_bms_cell_balance_active` | Gauge (per cell) | `1` if cell balancing is active |

### Alarms and faults

The metric `daly_bms_alarm_active{alarm="<name>"}` is set to `1` when an
alarm condition is active.  The following `alarm` label values are available:

`cell_overvoltage_l1`, `cell_overvoltage_l2`, `cell_undervoltage_l1`,
`cell_undervoltage_l2`, `pack_overvoltage_l1`, `pack_overvoltage_l2`,
`pack_undervoltage_l1`, `pack_undervoltage_l2`, `charge_overtemp_l1`,
`charge_overtemp_l2`, `charge_undertemp_l1`, `charge_undertemp_l2`,
`discharge_overtemp_l1`, `discharge_overtemp_l2`, `discharge_undertemp_l1`,
`discharge_undertemp_l2`, `charge_overcurrent_l1`, `charge_overcurrent_l2`,
`discharge_overcurrent_l1`, `discharge_overcurrent_l2`, `soc_low_l1`,
`soc_low_l2`, `cell_voltage_diff_l1`, `cell_voltage_diff_l2`,
`cell_temp_diff_l1`, `cell_temp_diff_l2`, `charge_mos_overtemp_l1`,
`charge_mos_overtemp_l2`, `discharge_mos_overtemp_l1`,
`discharge_mos_overtemp_l2`, `charge_mos_fault`, `discharge_mos_fault`,
`temp_sensor_fault`, `cell_fault`, `sampling_circuit_fault`,
`cell_balance_fault`.

---

## File structure

```
.
├── config.py          # All configuration constants (env-var backed)
├── daly_bms.py        # Daly BMS RS-485 protocol implementation
├── exporter.py        # Prometheus HTTP server + polling loop (entry point)
├── requirements.txt   # Python dependencies
├── .env.example       # Example environment / config file
└── tests/
    └── test_daly_bms.py  # Unit tests (no hardware required)
```

---

## Running tests

```bash
pip install pytest
python -m pytest tests/ -v
```

---

## Wiring

```
Daly BMS  ──RS-485──  USB-to-RS485 adapter  ──USB──  PC / Raspberry Pi
```

Connect the A and B differential pair of the RS-485 adapter to the
corresponding terminals on the Daly BMS communication port.  No external
power is required on the RS-485 bus; the BMS supplies it.

> **Note:** Make sure your user account has permission to access the serial
> device (e.g. `sudo usermod -aG dialout $USER` on Debian/Ubuntu, then log
> out and back in).

---

## License

MIT
