from datetime import datetime
import logging
import requests

from homeassistant.helpers.entity import Entity
from .const import DOMAIN, SENSOR_NAME, GEOJSON_FEED

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, config_entry, async_add_entities):
    config = hass.data[DOMAIN]["config"]
    async_add_entities([MeteoalarmSensor(config)], True)

class MeteoalarmSensor(Entity):
    def __init__(self, config):
        self._name = SENSOR_NAME
        self._state = None
        self._attributes = {}
        self._config = config

    def update(self):
        try:
            r = requests.get(GEOJSON_FEED, timeout=10)
            data = r.json()
            countries = [c.lower() for c in self._config.get("countries", [])]
            start = datetime.strptime(self._config.get("vacation_start"), "%Y-%m-%d")
            end = datetime.strptime(self._config.get("vacation_end"), "%Y-%m-%d")

            alerts = []
            for feature in data.get("features", []):
                props = feature.get("properties", {})
                country = props.get("country", "").lower()
                if country in countries:
                    event_time = datetime.strptime(props["onset"], "%Y-%m-%dT%H:%M:%S%z").date()
                    if start.date() <= event_time <= end.date():
                        alerts.append({
                            "country": country,
                            "event": props.get("event"),
                            "level": props.get("awareness_level", "unknown"),
                            "type": props.get("awareness_type", "unknown")
                        })

            self._state = len(alerts)
            self._attributes = {"alerts": alerts}

        except Exception as e:
            _LOGGER.error("Failed to fetch meteoalarm data: %s", e)
            self._state = 0
            self._attributes = {}

    @property
    def name(self):
        return self._name

    @property
    def state(self):
        return self._state

    @property
    def extra_state_attributes(self):
        return self._attributes
