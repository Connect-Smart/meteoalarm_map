import asyncio
from datetime import datetime
import logging

from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN, SENSOR_NAME, RSS_FEED
from .rss_feed_reader import MeteoalarmRSSReader

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the Meteoalarm sensors from a config entry."""
    config = hass.data[DOMAIN]["config"]

    # Create shared RSS reader instance
    if "rss_reader" not in hass.data[DOMAIN]:
        hass.data[DOMAIN]["rss_reader"] = MeteoalarmRSSReader(RSS_FEED)

    rss_reader = hass.data[DOMAIN]["rss_reader"]

    main_sensor = MeteoalarmSensor(config, rss_reader)
    alert_trigger_sensor = MeteoalarmAlertTriggerSensor(config, rss_reader)

    # Store for access if needed
    hass.data[DOMAIN]["alert_trigger_sensor"] = alert_trigger_sensor

    async_add_entities([main_sensor, alert_trigger_sensor], True)


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
            countries = [c.lower() for c in self._config.get("countries", [])]
            start_date = datetime.strptime(self._config.get("vacation_start"), "%Y-%m-%d")
            end_date = datetime.strptime(self._config.get("vacation_end"), "%Y-%m-%d")

            sensor_data = self._rss_reader.get_alerts_for_sensor(countries, start_date, end_date)

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

            level_summary = {'red': 0, 'orange': 0, 'yellow': 0, 'green': 0, 'unknown': 0}
            for alert in sensor_data['alerts']:
                level = alert.get('level', 'unknown')
                level_summary[level] = level_summary.get(level, 0) + 1

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
        return f"{DOMAIN}_sensor"

    @property
    def unit_of_measurement(self):
        return "alerts"

    @property
    def icon(self):
        if self._state is None:
            return "mdi:weather-lightning"
        elif self._state == 0:
            return "mdi:weather-sunny"
        elif self._state <= 5:
            return "mdi:weather-lightning"
        else:
            return "mdi:weather-lightning-rainy"


class MeteoalarmAlertTriggerSensor(Entity):
    def __init__(self, config, rss_reader):
        self._name = "Meteoalarm Alert Trigger"
        self._state = False
        self._previous_total = 0
        self._config = config
        self._rss_reader = rss_reader
        self._reset_task = None

    async def async_added_to_hass(self):
        """Start automatische update elke 5 minuten."""
        async def update_loop():
            while True:
                await self.hass.async_add_executor_job(self.update)
                await asyncio.sleep(300)  # 5 minuten

        self.hass.loop.create_task(update_loop())

    def update(self):
        try:
            countries = [c.lower() for c in self._config.get("countries", [])]
            start_date = datetime.strptime(self._config.get("vacation_start"), "%Y-%m-%d")
            end_date = datetime.strptime(self._config.get("vacation_end"), "%Y-%m-%d")

            data = self._rss_reader.get_alerts_for_sensor(countries, start_date, end_date)
            new_total = data['total_count']

            if new_total > self._previous_total:
                _LOGGER.info("Nieuwe waarschuwing gedetecteerd (%d > %d)", new_total, self._previous_total)
                self._state = True
                self.async_schedule_update_ha_state()

                if self._reset_task:
                    self._reset_task()

                self._reset_task = async_call_later(self.hass, 300, self._reset)

            self._previous_total = new_total

        except Exception as e:
            _LOGGER.error("Fout bij update trigger sensor: %s", e)

    def _reset(self, _):
        self._state = False
        self.async_schedule_update_ha_state()

    @property
    def name(self):
        return self._name

    @property
    def state(self):
        return self._state

    @property
    def unique_id(self):
        return f"{DOMAIN}_alert_trigger_sensor"

    @property
    def device_class(self):
        return "running"

    @property
    def icon(self):
        return "mdi:alarm-light"
