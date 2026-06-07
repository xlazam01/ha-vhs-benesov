import re
import logging
from datetime import timedelta
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup
from yarl import URL

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, BASE_URL, UPDATE_INTERVAL_HOURS

_LOGGER = logging.getLogger(__name__)

_HOST_URL = URL(BASE_URL).origin()


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

            # The server sends SE_Pilote_Cookie as two Set-Cookie headers: first an
            # empty expired one (delete), then the real value. aiohttp's SimpleCookie
            # merge inherits the 1999 expiry onto the real value and drops it.
            # Parse raw headers and inject the last non-empty value directly.
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
        """Fetch a page; return None if redirected to login (session expired)."""
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
            data["last_reading_date"] = m.group(1).strip()

        conso_html = await self._fetch_with_reauth("Site_Energie.aspx?Affichage=ConsoMois")
        soup = BeautifulSoup(conso_html, "html.parser")

        labels = [td.get_text(strip=True) for td in soup.find_all("td", class_="TableauEnergieLabel")]
        values = [span.get_text(strip=True) for span in soup.find_all("span", class_="CouleurConsommationEau")]

        monthly: dict[str, float] = {}
        for label, value in zip(labels, values):
            try:
                monthly[label] = float(value)
            except ValueError:
                pass

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
        return data
