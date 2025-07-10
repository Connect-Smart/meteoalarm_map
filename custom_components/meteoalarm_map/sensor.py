import asyncio
from datetime import datetime, timedelta
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

    async def async_added_to_hass(self):
        """Start periodic updates every 5 minutes."""
        async def update_loop():
            while True:
                await self.hass.async_add_executor_job(self.update)
                await asyncio.sleep(300)

        self.hass.loop.create_task(update_loop())

    def update(self):
        """Update the sensor state and attributes."""
        try:
            countries = [c.lower() for c in self._config.get("countries", [])]
            start_date = datetime.strptime(self._config.get("vacation_start"), "%Y-%m-%d")
            end_date = datetime.strptime(self._config.get("vacation_end"), "%Y-%m-%d")
            
            # Extend date range to include more alerts (today and tomorrow)
            today = datetime.now().date()
            extended_start = min(start_date.date(), today)
            extended_end = max(end_date.date(), today + timedelta(days=1))
            
            extended_start_dt = datetime.combine(extended_start, datetime.min.time())
            extended_end_dt = datetime.combine(extended_end, datetime.max.time())

            sensor_data = self._rss_reader.get_alerts_for_sensor(countries, extended_start_dt, extended_end_dt)

            self._state = sensor_data['total_count']
            self._attributes = {
                "alerts": sensor_data['alerts'],
                "countries_monitored": countries,
                "countries_with_alerts": sensor_data['countries_affected'],
                "vacation_period": f"{start_date.date()} to {end_date.date()}",
                "extended_period": f"{extended_start} to {extended_end}",
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
        self._previous_alerts = set()  # Track individual alerts by GUID
        self._config = config
        self._rss_reader = rss_reader
        self._reset_task = None

    async def async_added_to_hass(self):
        """Start periodic updates every 5 minutes."""
        # Initial update to set baseline
        await self.hass.async_add_executor_job(self._initialize_baseline)
        
        async def update_loop():
            while True:
                await self.hass.async_add_executor_job(self.update)
                await asyncio.sleep(300)

        self.hass.loop.create_task(update_loop())

    def _initialize_baseline(self):
        """Initialize the baseline without triggering alerts."""
        try:
            countries = [c.lower() for c in self._config.get("countries", [])]
            start_date = datetime.strptime(self._config.get("vacation_start"), "%Y-%m-%d")
            end_date = datetime.strptime(self._config.get("vacation_end"), "%Y-%m-%d")
            
            # Extend date range 
            today = datetime.now().date()
            extended_start = min(start_date.date(), today)
            extended_end = max(end_date.date(), today + timedelta(days=1))
            
            extended_start_dt = datetime.combine(extended_start, datetime.min.time())
            extended_end_dt = datetime.combine(extended_end, datetime.max.time())

            data = self._rss_reader.get_alerts_for_sensor(countries, extended_start_dt, extended_end_dt)
            
            self._previous_total = data['total_count']
            
            # Track individual alerts by creating unique identifiers
            current_alerts = set()
            for alert in data['alerts']:
                alert_id = f"{alert['country']}_{alert['event']}_{alert['level']}_{alert['pub_date']}"
                current_alerts.add(alert_id)
            
            self._previous_alerts = current_alerts
            
            _LOGGER.info("Trigger sensor baseline initialized: %d alerts tracked", len(current_alerts))
            
        except Exception as e:
            _LOGGER.error("Failed to initialize trigger sensor baseline: %s", e)

    def update(self):
        try:
            countries = [c.lower() for c in self._config.get("countries", [])]
            start_date = datetime.strptime(self._config.get("vacation_start"), "%Y-%m-%d")
            end_date = datetime.strptime(self._config.get("vacation_end"), "%Y-%m-%d")
            
            # Extend date range
            today = datetime.now().date()
            extended_start = min(start_date.date(), today)
            extended_end = max(end_date.date(), today + timedelta(days=1))
            
            extended_start_dt = datetime.combine(extended_start, datetime.min.time())
            extended_end_dt = datetime.combine(extended_end, datetime.max.time())

            data = self._rss_reader.get_alerts_for_sensor(countries, extended_start_dt, extended_end_dt)
            new_total = data['total_count']
            
            # Track individual alerts
            current_alerts = set()
            for alert in data['alerts']:
                alert_id = f"{alert['country']}_{alert['event']}_{alert['level']}_{alert['pub_date']}"
                current_alerts.add(alert_id)
            
            # Check for new alerts
            new_alerts = current_alerts - self._previous_alerts
            
            if new_alerts:
                _LOGGER.info("Nieuwe waarschuwingen gedetecteerd: %d nieuwe alerts (totaal: %d -> %d)", 
                           len(new_alerts), self._previous_total, new_total)
                
                # Log details van nieuwe alerts
                for alert in data['alerts']:
                    alert_id = f"{alert['country']}_{alert['event']}_{alert['level']}_{alert['pub_date']}"
                    if alert_id in new_alerts:
                        _LOGGER.info("Nieuwe alert: %s - %s (%s)", 
                                   alert['country'], alert['event'], alert['level'])
                
                self._state = True
                self.async_schedule_update_ha_state()

                # Cancel vorige geplande reset als die er is
                if self._reset_task:
                    self._reset_task.cancel()
                    self._reset_task = None

                # Plan reset over 5 minuten
                self._reset_task = self.hass.loop.call_later(300, self._reset_callback)
            
            elif new_total != self._previous_total:
                _LOGGER.info("Alert count changed maar geen nieuwe alerts: %d -> %d", 
                           self._previous_total, new_total)

            # Update tracking variables
            self._previous_total = new_total
            self._previous_alerts = current_alerts

        except Exception as e:
            _LOGGER.error("Fout bij update trigger sensor: %s", e)

    def _reset_callback(self):
        """Reset de trigger sensor naar False."""
        _LOGGER.info("Resetting trigger sensor to False")
        self._state = False
        self._reset_task = None
        self.async_schedule_update_ha_state()

    @property
    def name(self):
        return self._name

    @property
    def state(self):
        return self._state

    @property
    def extra_state_attributes(self):
        return {
            "previous_total": self._previous_total,
            "tracked_alerts": len(self._previous_alerts),
            "last_reset": datetime.now().isoformat() if self._reset_task else None
        }

    @property
    def unique_id(self):
        return f"{DOMAIN}_alert_trigger_sensor"

    @property
    def device_class(self):
        return "running"

    @property
    def icon(self):
        return "mdi:alarm-light" if self._state else "mdi:alarm-light-off"