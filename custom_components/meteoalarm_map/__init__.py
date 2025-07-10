from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .const import DOMAIN, RSS_FEED
from .rss_feed_reader import MeteoalarmRSSReader

async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the Meteoalarm Map component."""
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up Meteoalarm Map from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["config"] = entry.data

    # Create shared RSS reader instance
    hass.data[DOMAIN]["rss_reader"] = MeteoalarmRSSReader(RSS_FEED)

    # Forward the setup to the camera and sensor platforms
    await hass.config_entries.async_forward_entry_setups(entry, ["camera", "sensor"])

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["camera", "sensor"])
    
    if unload_ok:
        # Clean up the shared RSS reader and config data
        hass.data[DOMAIN].pop("rss_reader", None)
        hass.data[DOMAIN].pop("config", None)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN, None)
    
    return unload_ok