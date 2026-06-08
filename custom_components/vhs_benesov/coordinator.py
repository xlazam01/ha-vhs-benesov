import re
import logging
from datetime import timedelta, datetime, timezone
from http.cookies import SimpleCookie
from typing import Any

import aiohttp
from bs4 import BeautifulSoup
from yarl import URL

from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

try:
    from homeassistant.components.recorder.statistics import StatisticMeanType as _MeanType
    _MEAN_TYPE_NONE = _MeanType.NONE
except (ImportError, AttributeError):
    _MEAN_TYPE_NONE = None

_UNIT_CLASS_VOLUME = "volume"

from .const import DOMAIN, BASE_URL, UPDATE_INTERVAL_HOURS

_LOGGER = logging.getLogger(__name__)

_HOST_URL = URL(BASE_URL).origin()
_ENERGY_URL = f"{BASE_URL}/Site_Energie.aspx"
_ENTITY_STATISTIC_ID = "sensor.vhs_benesov_water_meter_water_meter_index"

# Safety cap for regular-update pagination (a few recent months is enough).
# Initial backfill ignores this and paginates until the portal says no more.
_MAX_PAGES = 36

# Config entry keys used to persist coordinator state across HA restarts
_KEY_BACKFILL_DONE = "initial_backfill_done"
_KEY_PAGE_HASHES   = "page_hashes"


def _extract_hidden(soup: BeautifulSoup, name: str) -> str:
    el = soup.find("input", {"name": name})
    return el["value"] if el else ""


def _inject_cookie(jar: aiohttp.CookieJar, name: str, value: str) -> None:
    """Manually insert a cookie into the aiohttp jar, bypassing SimpleCookie parsing.

    The portal sends two Set-Cookie headers for SE_Pilote_Cookie: first an empty
    expired one (to clear it), then the real value. aiohttp's SimpleCookie merges
    them and inherits the 1999 expiry onto the real value, causing it to be dropped.
    Direct injection avoids that merge entirely.
    """
    cookie: SimpleCookie = SimpleCookie()
    cookie[name] = value
    cookie[name]["path"] = "/"
    cookie[name]["domain"] = _HOST_URL.host
    jar.update_cookies(cookie, _HOST_URL)


def _parse_label(label: str) -> datetime | None:
    """Try all known portal date formats; return UTC start-of-period datetime or None."""
    label = label.strip()
    candidates: list[tuple[str, dict]] = [
        ("%m/%d/%Y %I:%M %p", {}),          # "6/8/2026 10:00 AM"  — hourly
        ("%m/%d/%Y %H:%M",    {}),          # "6/8/2026 10:00"
        ("%m/%d/%Y",          {}),          # "6/8/2026"           — daily
        ("%d/%m/%Y",          {}),          # "8/6/2026" EU
        ("%b %Y",             {"day": 1}),  # "Jun 2026"           — monthly
        ("%B %Y",             {"day": 1}),  # "June 2026"
        ("%m/%Y",             {"day": 1}),  # "06/2026"
        ("%Y",                {"month": 1, "day": 1}),  # "2026"   — yearly
    ]
    for fmt, overrides in candidates:
        try:
            dt = datetime.strptime(label, fmt)
            dt = dt.replace(**overrides, hour=0, minute=0, second=0, microsecond=0,
                            tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            continue
    return None


def _parse_consumption_table(html: str) -> dict[str, float]:
    """Extract {label: value} from a Site_Energie.aspx response."""
    soup = BeautifulSoup(html, "html.parser")
    labels = [td.get_text(strip=True)
              for td in soup.find_all("td", class_="TableauEnergieLabel")]
    values = [span.get_text(strip=True)
              for span in soup.find_all("span", class_="CouleurConsommationEau")]
    result: dict[str, float] = {}
    for label, value in zip(labels, values):
        try:
            result[label] = float(value)
        except ValueError:
            pass
    return result


class VHSBenesovCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(hours=UPDATE_INTERVAL_HOURS),
        )
        self._username: str = entry.data[CONF_USERNAME]
        self._password: str = entry.data[CONF_PASSWORD]
        self._session: aiohttp.ClientSession | None = None
        self._entry = entry
        # Persisted: True after the one-time full historical backfill on first setup.
        self._initial_backfill_done: bool = bool(entry.data.get(_KEY_BACKFILL_DONE))
        # Persisted: page_key (first label) → hash(consumption dict).
        # Lets regular updates skip pages whose data hasn't changed.
        self._page_hashes: dict[str, int] = dict(entry.data.get(_KEY_PAGE_HASHES, {}))
        self._nav_logged: bool = False  # one-shot navigation structure log

    def _persist_state(self) -> None:
        """Save backfill flag and page hashes into the config entry."""
        self.hass.config_entries.async_update_entry(
            self._entry,
            data={
                **self._entry.data,
                _KEY_BACKFILL_DONE: self._initial_backfill_done,
                _KEY_PAGE_HASHES:   self._page_hashes,
            },
        )

    def _session_ok(self) -> bool:
        return self._session is not None and not self._session.closed

    def _ensure_session(self) -> aiohttp.ClientSession:
        if not self._session_ok():
            self._session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        return self._session

    async def close(self) -> None:
        if self._session_ok():
            await self._session.close()

    async def _login(self) -> None:
        session = self._ensure_session()
        login_url = f"{BASE_URL}/Login.aspx"

        async with session.get(login_url) as resp:
            html = await resp.text()

        soup = BeautifulSoup(html, "html.parser")
        post_data = {
            "__VIEWSTATE": _extract_hidden(soup, "__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": _extract_hidden(soup, "__VIEWSTATEGENERATOR"),
            "__EVENTVALIDATION": _extract_hidden(soup, "__EVENTVALIDATION"),
            "ctl00$PHZonePrincipale$TextBoxIdentifiant": self._username,
            "ctl00$PHZonePrincipale$TextBoxMotDePasse": self._password,
            "ctl00$PHZonePrincipale$ButtonConnexion": "Connection",
            "ctl00$PHZonePrincipale$HiddenResolution": "1920x1080",
            "ctl00$PHZonePrincipale$HiddenMessageConnexion": "Connection in progress",
        }

        async with session.post(login_url, data=post_data, allow_redirects=False) as resp:
            location = resp.headers.get("Location", "")
            if resp.status not in (301, 302, 303, 307, 308) or "Login.aspx" in location:
                raise UpdateFailed("Login failed — check VHS Benešov credentials")

            auth_value = None
            for raw in resp.headers.getall("Set-Cookie", []):
                if raw.startswith("SE_Pilote_Cookie="):
                    v = raw.split("=", 1)[1].split(";")[0].strip()
                    if v:
                        auth_value = v

        if auth_value:
            _inject_cookie(session.cookie_jar, "SE_Pilote_Cookie", auth_value)
            _LOGGER.debug("Injected SE_Pilote_Cookie (%d chars)", len(auth_value))
        else:
            _LOGGER.warning("SE_Pilote_Cookie not found in POST response — may fail")

    async def _get(self, path: str) -> str | None:
        session = self._ensure_session()
        async with session.get(f"{BASE_URL}/{path}", allow_redirects=True) as resp:
            if "Login.aspx" in str(resp.url):
                return None
            return await resp.text()

    async def _fetch_with_reauth(self, path: str) -> str:
        html = await self._get(path)
        if html is None:
            _LOGGER.debug("Session expired, re-authenticating")
            await self._login()
            html = await self._get(path)
        if html is None:
            raise UpdateFailed(f"Unable to fetch {path} after re-authentication")
        return html

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self._fetch_data()
        except UpdateFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"Unexpected error: {err}") from err

    async def _fetch_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {}

        site_html = await self._fetch_with_reauth("Site.aspx")

        m = re.search(r"val > ([\d.]+)\)", site_html)
        if m:
            data["meter_index"] = float(m.group(1))
        else:
            _LOGGER.warning("Could not parse meter index from Site.aspx")

        m = re.search(r"Index Base of <span[^>]*>([^<]+)</span>", site_html)
        if m:
            raw = m.group(1).strip()
            try:
                dt = datetime.strptime(raw, "%m/%d/%Y %I:%M %p").replace(tzinfo=timezone.utc)
                data["last_reading_date"] = dt.isoformat()
            except ValueError:
                data["last_reading_date"] = raw

        conso_html = await self._fetch_with_reauth("Site_Energie.aspx?Affichage=ConsoMois")
        monthly = _parse_consumption_table(conso_html)
        data["monthly_consumption"] = monthly
        if monthly:
            last_month = list(monthly.keys())[-1]
            data["current_month_label"] = last_month
            data["current_month_consumption"] = monthly[last_month]

        _LOGGER.debug(
            "Fetched: index=%.3f m³, this month=%s=%.3f m³",
            data.get("meter_index", 0),
            data.get("current_month_label", "?"),
            data.get("current_month_consumption", 0),
        )

        meter_index = data.get("meter_index")
        if meter_index is not None:
            if not self._initial_backfill_done:
                # First integration setup: import full history once.
                await self._initial_backfill(meter_index)
                self._initial_backfill_done = True
                self._persist_state()
            else:
                # Regular update: only import changed pages at finest granularity.
                changed = await self._fetch_courbe_changed()
                if changed:
                    stats = self._build_stats_autounit(meter_index, changed, "CourbeMois")
                    if stats:
                        self._import(stats, "CourbeMois 6h", len(stats))
                    self._persist_state()

        return data

    # ------------------------------------------------------------------ #
    #  Backfill (first setup only)                                        #
    # ------------------------------------------------------------------ #

    async def _initial_backfill(self, meter_index: float) -> None:
        """Import full history on first integration setup.

        Paginates through all available CourbeMois pages at 6h granularity with no
        page limit — stops only when the portal has no further history to offer.
        """
        try:
            all_consumption = await self._fetch_courbe_changed(max_pages=None)
        except Exception as err:
            _LOGGER.warning("vhs_benesov: CourbeMois initial fetch failed: %s", err)
            return

        if all_consumption:
            stats = self._build_stats_autounit(meter_index, all_consumption, "CourbeMois")
            if stats:
                self._import(stats, "CourbeMois 6h (initial)", len(stats))

    # ------------------------------------------------------------------ #
    #  CourbeMois pagination                                              #
    # ------------------------------------------------------------------ #

    async def _fetch_courbe_changed(
        self, max_pages: int | None = _MAX_PAGES
    ) -> dict[str, float]:
        """Fetch CourbeMois pages whose data has changed since last run.

        Navigates from current month backwards.  Stops as soon as a page's hash
        matches the stored value — older pages are immutable once fully past.
        On first run (_page_hashes empty) every available page is fetched.
        max_pages=None removes the page cap (used for initial backfill).
        """
        merged: dict[str, float] = {}
        html = await self._fetch_with_reauth("Site_Energie.aspx?Affichage=CourbeMois")

        if not self._nav_logged:
            self._log_nav_structure(html)
            self._nav_logged = True

        visited_keys: set[str] = set()
        page_num = 0

        while max_pages is None or page_num < max_pages:
            consumption = _parse_consumption_table(html)
            if not consumption:
                _LOGGER.debug("vhs_benesov: CourbeMois page %d empty, stopping", page_num)
                break

            page_key = next(iter(consumption))
            if page_key in visited_keys:
                _LOGGER.debug("vhs_benesov: loop at page %d (%r)", page_num, page_key)
                break
            visited_keys.add(page_key)

            page_hash = hash(frozenset(consumption.items()))
            if self._page_hashes.get(page_key) == page_hash:
                _LOGGER.debug(
                    "vhs_benesov: page %d (%r) unchanged, stopping pagination",
                    page_num, page_key,
                )
                break

            merged.update(consumption)
            self._page_hashes[page_key] = page_hash
            _LOGGER.debug(
                "vhs_benesov: page %d (%r): %d entries, hash changed",
                page_num, page_key, len(consumption),
            )

            prev_html = await self._navigate_prev(html)
            page_num += 1
            if prev_html is None:
                _LOGGER.info(
                    "vhs_benesov: reached oldest available CourbeMois page after %d page(s)",
                    page_num,
                )
                break
            html = prev_html

        return merged

    async def _navigate_prev(self, html: str) -> str | None:
        """POST the CourbeMois form to navigate to the previous month.

        Returns the new page HTML, or None if no previous-month control was found.
        """
        soup = BeautifulSoup(html, "html.parser")
        form_data: dict[str, str] = {
            "__VIEWSTATE":          _extract_hidden(soup, "__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": _extract_hidden(soup, "__VIEWSTATEGENERATOR"),
            "__EVENTVALIDATION":    _extract_hidden(soup, "__EVENTVALIDATION"),
            "__EVENTTARGET":        "",
            "__EVENTARGUMENT":      "",
        }

        _PREV_KEYWORDS = ("prec", "precedent", "prev", "previous", "avant", "◄", "<<", "&lt;")

        # Pattern 1: regular <input type="submit"> whose name or value looks like "previous"
        for btn in soup.find_all("input", {"type": "submit"}):
            name  = btn.get("name",  "")
            value = btn.get("value", "")
            if any(kw in (name + value).lower() for kw in _PREV_KEYWORDS):
                form_data[name] = value
                break

        # Pattern 2: <a href="javascript:__doPostBack(...)"> link
        else:
            for a in soup.find_all("a", href=True):
                href = a["href"]
                text = a.get_text()
                if "__doPostBack" not in href:
                    continue
                if any(kw in (href + text).lower() for kw in _PREV_KEYWORDS):
                    m = re.search(r"__doPostBack\('([^']+)'", href)
                    if m:
                        form_data["__EVENTTARGET"] = m.group(1).replace("\\x24", "$")
                        form_data["__EVENTARGUMENT"] = ""
                        break
            else:
                # Pattern 3: onclick attribute on any element
                for el in soup.find_all(onclick=True):
                    onclick = el.get("onclick", "")
                    if "__doPostBack" not in onclick:
                        continue
                    if any(kw in (onclick + el.get_text()).lower() for kw in _PREV_KEYWORDS):
                        m = re.search(r"__doPostBack\('([^']+)'", onclick)
                        if m:
                            form_data["__EVENTTARGET"] = m.group(1).replace("\\x24", "$")
                            form_data["__EVENTARGUMENT"] = ""
                            break
                else:
                    return None

        session = self._ensure_session()
        async with session.post(_ENERGY_URL, data=form_data, allow_redirects=True) as resp:
            if "Login.aspx" in str(resp.url):
                return None
            new_html = await resp.text()

        if not _parse_consumption_table(new_html):
            return None
        return new_html

    def _log_nav_structure(self, html: str) -> None:
        """Log CourbeMois navigation elements once for diagnostic purposes."""
        soup = BeautifulSoup(html, "html.parser")
        submit_btns = [
            f"name={b.get('name','')!r} value={b.get('value','')!r}"
            for b in soup.find_all("input", {"type": "submit"})
        ]
        postback_links = [
            f"text={a.get_text(strip=True)!r} href={a['href'][:80]!r}"
            for a in soup.find_all("a", href=re.compile(r"doPostBack"))
        ]
        onclick_els = [
            f"tag={el.name} text={el.get_text(strip=True)!r} onclick={el['onclick'][:80]!r}"
            for el in soup.find_all(onclick=re.compile(r"doPostBack"))
        ]
        _LOGGER.info(
            "vhs_benesov: CourbeMois nav structure — submit buttons: %s | "
            "postback links: %s | onclick elements: %s",
            submit_btns or "(none)",
            postback_links or "(none)",
            onclick_els or "(none)",
        )

    # ------------------------------------------------------------------ #
    #  Statistics helpers                                                  #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_stats(
        meter_index: float,
        consumption: dict[str, float],
        divisor: float = 1.0,
    ) -> list[dict]:
        """Reconstruct meter readings at period-start by working backwards from current index."""
        dated: list[tuple[datetime, float]] = []
        for label, raw_value in consumption.items():
            dt = _parse_label(label)
            if dt is not None:
                dated.append((dt, raw_value / divisor))

        if not dated:
            return []

        dated.sort(key=lambda x: x[0])

        stats: list[dict] = []
        running = meter_index
        for dt, value in reversed(dated):
            running -= value
            stats.insert(0, {"start": dt, "state": running, "sum": running})
        return stats

    @staticmethod
    def _build_stats_autounit(
        meter_index: float, consumption: dict[str, float], affichage: str
    ) -> list[dict]:
        """Build stats, auto-detecting if values are in liters (÷1000 → m³)."""
        stats = VHSBenesovCoordinator._build_stats(meter_index, consumption)
        if stats and stats[0]["state"] < 0:
            stats_l = VHSBenesovCoordinator._build_stats(meter_index, consumption, divisor=1000.0)
            if stats_l and stats_l[0]["state"] >= 0:
                _LOGGER.info("vhs_benesov: %s values are in liters (÷1000 → m³)", affichage)
                return stats_l
            _LOGGER.warning(
                "vhs_benesov: %s gives negative readings even after ÷1000 "
                "(raw sum=%.1f, meter=%.3f) — skipping",
                affichage, sum(consumption.values()), meter_index,
            )
            return []
        return stats

    def _import(self, stats: list[dict], label: str, count: int) -> None:
        """Call async_import_statistics for a prepared stats list."""
        meta_kwargs: dict = {
            "has_mean": False,
            "has_sum": True,
            "name": None,
            "source": "recorder",
            "statistic_id": _ENTITY_STATISTIC_ID,
            "unit_of_measurement": UnitOfVolume.CUBIC_METERS,
            "unit_class": _UNIT_CLASS_VOLUME,
        }
        if _MEAN_TYPE_NONE is not None:
            meta_kwargs["mean_type"] = _MEAN_TYPE_NONE
        metadata = StatisticMetaData(**meta_kwargs)

        _LOGGER.info(
            "vhs_benesov: importing %d %s stats, range %.3f–%.3f m³",
            count, label, stats[0]["state"], stats[-1]["state"],
        )
        try:
            async_import_statistics(self.hass, metadata, stats)
        except Exception as err:
            _LOGGER.error("vhs_benesov: failed to import %s statistics: %s", label, err)
