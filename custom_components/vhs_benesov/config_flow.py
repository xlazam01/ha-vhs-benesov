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


async def _test_credentials(username: str, password: str) -> None:
    """Raise ValueError if credentials are rejected."""
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar()) as session:
        async with session.get(f"{BASE_URL}/Login.aspx") as resp:
            html = await resp.text()

        soup = BeautifulSoup(html, "html.parser")

        def val(name: str) -> str:
            el = soup.find("input", {"name": name})
            return el["value"] if el else ""

        post_data = {
            "__VIEWSTATE": val("__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": val("__VIEWSTATEGENERATOR"),
            "__EVENTVALIDATION": val("__EVENTVALIDATION"),
            "ctl00$PHZonePrincipale$TextBoxIdentifiant": username,
            "ctl00$PHZonePrincipale$TextBoxMotDePasse": password,
            "ctl00$PHZonePrincipale$ButtonConnexion": "Connection",
            "ctl00$PHZonePrincipale$HiddenResolution": "1920x1080",
            "ctl00$PHZonePrincipale$HiddenMessageConnexion": "Connection in progress",
        }

        async with session.post(
            f"{BASE_URL}/Login.aspx", data=post_data, allow_redirects=True
        ) as resp:
            if "Login.aspx" in str(resp.url):
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
            except Exception:  # noqa: BLE001
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
