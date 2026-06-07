# VHS Benešov Water Meter — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/xlazam01/ha-vhs-benesov.svg)](https://github.com/xlazam01/ha-vhs-benesov/releases)
[![HA version](https://img.shields.io/badge/Home%20Assistant-2023.4%2B-blue)](https://www.home-assistant.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Custom Home Assistant integration for customers of **VHS Benešov a.s.** (Vodohospodářská společnost Benešov). Scrapes the [SUEZ Pracdis](https://cz-sitr.suezsmartsolutions.com/eMIS.SE_VHS-Benesov/) customer portal and exposes your water meter data as native HA sensors — fully compatible with the **Energy dashboard**.

---

## Features

- **Current meter index** (m³) — absolute odometer reading, updates daily
- **Monthly consumption** (m³) — usage for the current reported month
- **24-month consumption history** — stored as sensor attributes
- Automatic session management with silent re-login on expiry
- Config Flow UI — no YAML editing required
- Password stored as masked field, excluded from diagnostics

---

## Sensors

| Entity | Device class | State class | Unit | Notes |
|---|---|---|---|---|
| `sensor.water_meter_index` | `water` | `total_increasing` | m³ | Use this in the Energy dashboard |
| `sensor.water_consumption_this_month` | `water` | `total` | m³ | Current month; attribute `month` shows label |
| `sensor.water_monthly_history` | `water` | `measurement` | m³ | Disabled by default; full history in attributes |

---

## Requirements

- Home Assistant **2023.4** or newer
- An account on the [VHS Benešov customer portal](https://cz-sitr.suezsmartsolutions.com/eMIS.SE_VHS-Benesov/Login.aspx) — your customer number and password
- Python package `beautifulsoup4` (installed automatically by HA on first load)

---

## Installation

### Via HACS (recommended)

1. Open **HACS** in Home Assistant.
2. Click the three-dot menu → **Custom repositories**.
3. Add `https://github.com/xlazam01/ha-vhs-benesov` with category **Integration**.
4. Click **Download** on the VHS Benešov card.
5. **Restart Home Assistant.**

### Manual

1. Download the latest release or clone this repo.
2. Copy the `custom_components/vhs_benesov/` folder into your HA config directory so the path is:
   ```
   /config/custom_components/vhs_benesov/
   ```
3. **Restart Home Assistant.**

---

## How to set up

### Step 1 — Add the integration

Go to **Settings → Devices & Services → Add Integration** and search for **VHS Benešov**.

![Add Integration search](docs/step1_search.png)

### Step 2 — Enter your credentials

| Field | What to enter |
|---|---|
| **Customer number** | Your login name for the portal (e.g. `6512000393`) |
| **Password** | Your portal password |

The integration validates the credentials live before saving. The password is masked and never shown in plain text.

![Credentials form](docs/step2_credentials.png)

### Step 3 — Done

Three sensors are created under a single **VHS Benešov Water Meter** device. The first data fetch runs immediately on setup; subsequent updates run every hour.

---

## Using with the Energy dashboard

1. Go to **Settings → Dashboards → Energy**.
2. Under **Water**, click **Add water source**.
3. Select **Water Meter Index** (`sensor.water_meter_index`).
4. Save.

Because the sensor uses `state_class: total_increasing`, HA automatically calculates daily, weekly, and monthly consumption statistics from the absolute readings.

---

## Update interval and data freshness

### How often HA polls

The coordinator fetches data from the portal every **1 hour**. An immediate fetch also runs on HA startup. You can force a manual refresh at any time from **Settings → Devices & Services → VHS Benešov → Reload**.

### How often the portal itself updates

The portal receives a new reading from the physical meter **once per day**, overnight (typically around 3–4 AM, based on the timestamp shown on the portal home page). Hourly polling therefore has no practical downside — the value in HA will simply be the same number for most of the day.

| Time of day | What HA shows |
|---|---|
| Midnight – ~4 AM | Previous day's reading (meter hasn't reported yet) |
| After ~4 AM | New reading — picked up on the next hourly poll |
| Rest of the day | Stable; won't change until the next overnight report |

### Does the HA value match the portal?

Yes, exactly. HA scrapes the same number shown on the portal home page. The `last_reading_date` attribute on the **Water Meter Index** sensor shows the timestamp of the reading as reported by the portal — you can compare it directly.

In **Developer Tools → States**, the sensor looks like this:

```
state:              586.369
last_reading_date:  6/5/2026 3:55 AM
```

### Changing the poll interval

Because the meter reports only once per day, you can safely increase the interval to reduce unnecessary requests. Edit `UPDATE_INTERVAL_HOURS` in `custom_components/vhs_benesov/const.py` and restart HA. A value of `6` or `12` works fine with no loss of data accuracy.

---

## Troubleshooting

### "Invalid customer number or password"

Verify that you can log in manually at the [portal](https://cz-sitr.suezsmartsolutions.com/eMIS.SE_VHS-Benesov/Login.aspx). Passwords are case-sensitive. Special characters (e.g. `$`) are supported.

### Sensors show `unavailable` after setup

- The portal may be temporarily down. The integration retries automatically on the next poll cycle.
- Check HA logs (`Settings → System → Logs`) and filter for `vhs_benesov`.

### Sensors stuck on old values

The portal updates readings overnight. If values haven't changed in more than 48 hours, check that the integration is not in an error state (the device card would show a warning banner).

### Portal structure changed

This integration scrapes HTML. If VHS Benešov or SUEZ update the portal layout, the scraper may break. Open an [issue](https://github.com/xlazam01/ha-vhs-benesov/issues) with a description of what changed.

---

## Technical notes

The portal runs on **SUEZ Pracdis GE** (ASP.NET WebForms). A few non-obvious behaviours that required workarounds:

- The login POST response sets the auth cookie (`SE_Pilote_Cookie`) via **two** `Set-Cookie` headers: first an empty expired one (delete), then the real value. Python's `http.cookies.SimpleCookie` merges them and inherits the 1999 expiry, causing `aiohttp` to drop the cookie silently. The integration parses raw response headers and injects the cookie directly into the jar.
- The session must be maintained between the GET (which generates `__VIEWSTATE` / `__EVENTVALIDATION`) and the POST. A fresh `aiohttp.CookieJar(unsafe=True)` is used per coordinator instance.
- Redirect following is disabled on the POST response (`allow_redirects=False`) to prevent aiohttp from losing the auth cookie while chasing the redirect chain.

---

## Disclaimer

This integration is not affiliated with VHS Benešov a.s. or SUEZ. It screen-scrapes the customer portal; a portal update may break it. Use at your own risk.

---

## Contributing

Pull requests welcome. Please open an issue first for anything beyond a small bug fix so we can agree on the approach.
