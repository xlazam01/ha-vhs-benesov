import re
import logging
from datetime import timedelta
from typing import Any

import aiohttp
from bs4 import BeautifulSoup

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, BASE_URL, UPDATE_INTERVAL_HOURS

_LOGGER = logging.getLogger(__name__)


def _extract_hidden(soup: BeautifulSoup, name: str) -> str:
    el = soup.find("input", {"name": name})
    return el["value"] if el else ""


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
            # unsafe=True: accept cookies that lack a Domain attribute (common in ASP.NET)
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

        async with session.post(login_url, data=post_data, allow_redirects=True) as resp:
            if "Login.aspx" in str(resp.url):
                raise UpdateFailed("Login failed — check VHS Benešov credentials")
            _LOGGER.debug("Logged in, redirected to %s", resp.url)

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

        # --- Current meter index from Site.aspx ---
        site_html = await self._fetch_with_reauth("Site.aspx")

        m = re.search(r"val > ([\d.]+)\)", site_html)
        if m:
            data["meter_index"] = float(m.group(1))
        else:
            _LOGGER.warning("Could not parse meter index from Site.aspx")

        m = re.search(r"Index Base of <span[^>]*>([^<]+)</span>", site_html)
        if m:
            data["last_reading_date"] = m.group(1).strip()

        # --- Monthly consumption from ConsoMois ---
        conso_html = await self._fetch_with_reauth(
            "Site_Energie.aspx?Affichage=ConsoMois"
        )
        soup = BeautifulSoup(conso_html, "html.parser")

        labels = [td.get_text(strip=True) for td in soup.find_all("td", class_="TableauEnergieLabel")]
        values = [
            span.get_text(strip=True)
            for span in soup.find_all("span", class_="CouleurConsommationEau")
        ]

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
