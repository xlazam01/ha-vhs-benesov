# VHS Benešov Water Meter — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/xlazam01/ha-vhs-benesov.svg)](https://github.com/xlazam01/ha-vhs-benesov/releases)
[![HA version](https://img.shields.io/badge/Home%20Assistant-2023.4%2B-blue)](https://www.home-assistant.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Custom Home Assistant integration for customers of **VHS Benešov a.s.** (Vodohospodářská společnost Benešov), powered by the [SUEZ Pracdis](https://cz-sitr.suezsmartsolutions.com) smart water portal.

Scrapes the customer portal and exposes your water meter data as native Home Assistant sensors — compatible with the **Energy dashboard** water tracking.

---

## Features

- Current absolute meter index (m³) — works with the HA Energy dashboard
- Current month consumption (m³)
- Full month-by-month history as sensor attributes (24 months)
- Automatic session management with re-login on expiry
- Config Flow UI setup — no YAML editing required
- Polling every hour (configurable in code)

---

## Sensors created

| Entity | Device class | State class | Description |
|---|---|---|---|
| `sensor.water_meter_index` | `water` | `total_increasing` | Absolute meter reading in m³. Use this in the Energy dashboard. |
| `sensor.water_consumption_this_month` | `water` | `total` | Consumption in the current (latest reported) month. |
| `sensor.water_monthly_history` | `water` | `measurement` | Disabled by default. State = last month; attributes contain the full monthly history dict. |

---

## Requirements

- Home Assistant 2023.4 or newer
- A customer account on the [VHS Benešov portal](https://cz-sitr.suezsmartsolutions.com/eMIS.SE_VHS-Benesov/Login.aspx)
- Python package `beautifulsoup4` (installed automatically by HA)

---

## Installation

### Via HACS (recommended)

1. Open HACS in Home Assistant.
2. Click **Custom repositories** → add this repo URL with category **Integration**.
3. Search for **VHS Benešov** and install.
4. Restart Home Assistant.

### Manual

1. Copy the `custom_components/vhs_benesov` folder into your HA config directory:
   ```
   /config/custom_components/vhs_benesov/
   ```
2. Restart Home Assistant.

---

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **VHS Benešov**.
3. Enter your portal credentials:
   - **Customer number** — the number you use to log in (e.g. `6512000XXX`)
   - **Password** — your portal password
4. Click **Submit**. The integration validates the credentials live and creates the sensors.

Credentials are stored in HA's internal config entry storage and the password field is masked throughout the UI.

---

## Energy dashboard

Add the **Water Meter Index** sensor (`sensor.water_meter_index`) to the Energy dashboard under **Water** → **Water consumption from a water metre**. Because its state class is `total_increasing`, HA will automatically calculate daily/weekly/monthly consumption statistics.

---

## Data refresh

The portal is polled every **1 hour**. The portal itself updates readings approximately once per day (overnight), so hourly polling is conservative and avoids unnecessary load.

---

## Troubleshooting

**Sensors show `unavailable` after setup**
- Check HA logs for `vhs_benesov` errors.
- The portal may be temporarily down — the integration will retry on the next poll cycle.

**Login keeps failing**
- Verify your credentials by logging in manually at the [portal](https://cz-sitr.suezsmartsolutions.com/eMIS.SE_VHS-Benesov/Login.aspx).
- The portal uses ASP.NET session tokens; if the login page structure changes after a portal update, open an issue.

**Meter index looks wrong**
- The value comes from the animated odometer on the portal home page. If the portal changes its JS structure, open an issue with the relevant HTML snippet.

---

## Disclaimer

This integration is not affiliated with VHS Benešov a.s. or SUEZ. It screen-scrapes the customer portal; a portal update may break it. Use at your own risk.

---

## Contributing

Pull requests welcome. Open an issue first for anything beyond a small bug fix.
