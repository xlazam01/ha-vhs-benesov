import logging

import voluptuous as vol
import aiohttp
from bs4 import BeautifulSoup

from homeassistant import config_entries
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import DOMAIN, BASE_URL

_LOGGER = logging.getLogger(__name__)


async def _test_credentials(username: str, password: str) -> None:
    """Raise ValueError on bad credentials, Exception on connection error."""
    login_url = f"{BASE_URL}/Login.aspx"

    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
        async with session.get(login_url) as resp:
            html = await resp.text()

        soup = BeautifulSoup(html, "html.parser")

        def val(name: str) -> str:
            el = soup.find("input", {"name": name})
            return el["value"] if el else ""

        viewstate = val("__VIEWSTATE")
        eventval = val("__EVENTVALIDATION")

        if not viewstate or not eventval:
            raise ConnectionError("Could not parse login form")

        post_data = {
            "__VIEWSTATE": viewstate,
            "__VIEWSTATEGENERATOR": val("__VIEWSTATEGENERATOR"),
            "__EVENTVALIDATION": eventval,
            "ctl00$PHZonePrincipale$TextBoxIdentifiant": username,
            "ctl00$PHZonePrincipale$TextBoxMotDePasse": password,
            "ctl00$PHZonePrincipale$ButtonConnexion": "Connection",
            "ctl00$PHZonePrincipale$HiddenResolution": "1920x1080",
            "ctl00$PHZonePrincipale$HiddenMessageConnexion": "Connection in progress",
        }

        async with session.post(login_url, data=post_data, allow_redirects=False) as resp:
            location = resp.headers.get("Location", "")
            if resp.status not in (301, 302, 303, 307, 308) or "Login.aspx" in location:
                raise ValueError("invalid_auth")


class VHSBenesovConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                await _test_credentials(
                    user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
                )
            except ValueError:
                errors["base"] = "invalid_auth"
            except Exception as err:  # noqa: BLE001
                _LOGGER.exception("VHS: unexpected error during credential test: %s", err)
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(user_input[CONF_USERNAME])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"VHS Benešov ({user_input[CONF_USERNAME]})",
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="username")
                    ),
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
                    ),
                }
            ),
            errors=errors,
        )
