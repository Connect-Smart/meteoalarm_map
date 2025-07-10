from homeassistant import config_entries
import voluptuous as vol
from .const import DOMAIN

class MeteoalarmMapConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            # splits landen op komma's, verwijder spaties
            user_input["countries"] = [c.strip().lower() for c in user_input["countries"].split(",")]
            return self.async_create_entry(title="Meteoalarm Map", data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("update_interval", default=10): int,
                vol.Optional("vacation_start", default="2025-08-01"): str,
                vol.Optional("vacation_end", default="2025-08-25"): str,
                vol.Optional("countries", default="italy, spain, france"): str
            })
        )
