from datetime import datetime
import logging

from homeassistant.helpers.entity import Entity
from .const import DOMAIN, SENSOR_NAME, RSS_FEED
from .rss_feed_reader import MeteoalarmRSSReader

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the Meteoalarm sensor from a config entry."""
    config = hass.data[DOMAIN]["config"]
    
    # Create shared RSS reader instance
    if "rss_reader" not in hass.data[DOMAIN]:
        hass.data[DOMAIN]["rss_reader"] = MeteoalarmRSSReader(RSS_FEED)
    
    async_add_entities([MeteoalarmSensor(config, hass.data[DOMAIN]["rss_reader"])], True)

class MeteoalarmSensor(Entity):
    def __init__(self, config, rss_reader):
        self._name = SENSOR_NAME
        self._state = None
        self._attributes = {}
        self._config = config
        self._rss_reader = rss_reader

    def update(self):
        """Update the sensor state and attributes."""
        try:
            # Get configuration
            countries = [c.lower() for c in self._config.get("countries", [])]
            start_date = datetime.strptime(self._config.get("vacation_start"), "%Y-%m-%d")
            end_date = datetime.strptime(self._config.get("vacation_end"), "%Y-%m-%d")

            # Fetch alerts using shared RSS reader
            sensor_data = self._rss_reader.get_alerts_for_sensor(countries, start_date, end_date)
            
            # Update sensor state and attributes
            self._state = sensor_data['total_count']
            self._attributes = {
                "alerts": sensor_data['alerts'],
                "countries_monitored": countries,
                "countries_with_alerts": sensor_data['countries_affected'],
                "vacation_period": f"{start_date.date()} to {end_date.date()}",
                "last_update": datetime.now().isoformat(),
                "feed_source": "RSS",
                "rss_last_fetch": self._rss_reader.last_update.isoformat() if self._rss_reader.last_update else None
            }
            
            # Add summary by level
            level_summary = {'red': 0, 'orange': 0, 'yellow': 0, 'green': 0, 'unknown': 0}
            for alert in sensor_data['alerts']:
                level = alert.get('level', 'unknown')
                if level in level_summary:
                    level_summary[level] += 1
                else:
                    level_summary['unknown'] += 1
            
            self._attributes["alerts_by_level"] = level_summary
            
            _LOGGER.info(
                "Sensor updated: %d alerts from %d countries (Red: %d, Orange: %d, Yellow: %d, Green: %d)",
                sensor_data['total_count'],
                sensor_data['countries_affected'],
                level_summary['red'],
                level_summary['orange'], 
                level_summary['yellow'],
                level_summary['green']
            )

        except Exception as e:
            _LOGGER.error("Failed to update sensor: %s", e)
            self._state = 0
            self._attributes = {
                "error": str(e),
                "last_error_time": datetime.now().isoformat()
            }

    @property
    def name(self):
        return self._name

    @property
    def state(self):
        return self._state

    @property
    def extra_state_attributes(self):
        return self._attributes

    @property
    def unique_id(self):
        """Return a unique ID for this sensor."""
        return f"{DOMAIN}_sensor"

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return "alerts"

    @property
    def icon(self):
        """Return the icon for this sensor."""
        if self._state is None:
            return "mdi:weather-lightning"
        elif self._state == 0:
            return "mdi:weather-sunny"
        elif self._state <= 5:
            return "mdi:weather-lightning"
        else:
            return "mdi:weather-lightning-rainy"