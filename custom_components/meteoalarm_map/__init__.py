from .const import DOMAIN
from homeassistant.config_entries import ConfigEntry

async def async_setup(hass, config):
    return True

async def async_setup_entry(hass, entry: ConfigEntry):
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["config"] = entry.data

    hass.async_create_task(
        hass.config_entries.async_forward_entry_setup(entry, "camera")
    )
    hass.async_create_task(
        hass.config_entries.async_forward_entry_setup(entry, "sensor")
    )
    return True

async def async_unload_entry(hass, entry):
    unload_camera = await hass.config_entries.async_forward_entry_unload(entry, "camera")
    unload_sensor = await hass.config_entries.async_forward_entry_unload(entry, "sensor")
    return unload_camera and unload_sensor
