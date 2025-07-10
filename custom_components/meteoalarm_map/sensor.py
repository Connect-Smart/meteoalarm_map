from datetime import datetime
import logging
import requests
import xml.etree.ElementTree as ET
import re

from homeassistant.helpers.entity import Entity
from .const import DOMAIN, SENSOR_NAME, RSS_FEED

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the Meteoalarm sensor from a config entry."""
    config = hass.data[DOMAIN]["config"]
    async_add_entities([MeteoalarmSensor(config)], True)

class MeteoalarmSensor(Entity):
    def __init__(self, config):
        self._name = SENSOR_NAME
        self._state = None
        self._attributes = {}
        self._config = config

    def _extract_country_from_title(self, title):
        title = title.strip().lower()
        known_prefixes = ['meteoalarm ']
        for prefix in known_prefixes:
            if title.startswith(prefix):
                return title[len(prefix):].strip()
        if ':' in title:
            return title.split(':')[0].strip()
        elif ' - ' in title:
            return title.split(' - ')[0].strip()
        return ""

    def _parse_awareness_level(self, title, description):
        """Parse awareness level from title or description."""
        text = f"{title} {description}".lower()
        if 'red' in text:
            return 'red'
        elif 'orange' in text:
            return 'orange'
        elif 'yellow' in text:
            return 'yellow'
        elif 'green' in text:
            return 'green'
        return 'unknown'

    def _parse_awareness_type(self, title, description):
        """Parse awareness type from title or description."""
        text = f"{title} {description}".lower()
        weather_types = {
            'wind': ['wind', 'storm', 'gale'],
            'rain': ['rain', 'precipitation', 'shower'],
            'snow': ['snow', 'blizzard'],
            'thunderstorm': ['thunderstorm', 'lightning'],
            'fog': ['fog', 'visibility'],
            'temperature': ['temperature', 'heat', 'cold', 'frost'],
            'ice': ['ice', 'freezing'],
            'flood': ['flood', 'flooding']
        }
        
        for weather_type, keywords in weather_types.items():
            if any(keyword in text for keyword in keywords):
                return weather_type
        return 'unknown'

    def update(self):
        try:
            r = requests.get(RSS_FEED, timeout=15)
            r.raise_for_status()
            
            # Parse RSS XML
            root = ET.fromstring(r.content)
            
            countries = [c.lower() for c in self._config.get("countries", [])]
            start = datetime.strptime(self._config.get("vacation_start"), "%Y-%m-%d")
            end = datetime.strptime(self._config.get("vacation_end"), "%Y-%m-%d")

            alerts = []
            
            # Find all RSS items
            for item in root.findall('.//item'):
                title_elem = item.find('title')
                description_elem = item.find('description')
                pub_date_elem = item.find('pubDate')
                link_elem = item.find('link')
                
                if title_elem is None or description_elem is None:
                    continue
                    
                title = title_elem.text or ""
                description = description_elem.text or ""
                pub_date = pub_date_elem.text if pub_date_elem is not None else ""
                link = link_elem.text if link_elem is not None else ""
                
                # Extract country from title
                country = self._extract_country_from_title(title)
                
                if country and country in countries:
                    try:
                        # Parse publication date
                        if pub_date:
                            # RSS date format: "Wed, 10 Jul 2025 08:00:00 GMT"
                            event_time = datetime.strptime(pub_date, "%a, %d %b %y %H:%M:%S %z")
                        else:
                            event_time = datetime.now()
                            
                        if start.date() <= event_time.date() <= end.date():
                            alert = {
                                "country": country,
                                "event": title,
                                "level": self._parse_awareness_level(title, description),
                                "type": self._parse_awareness_type(title, description),
                                "description": description[:200] + "..." if len(description) > 200 else description,
                                "pub_date": pub_date,
                                "link": link
                            }
                            alerts.append(alert)
                            
                    except Exception as e:
                        _LOGGER.warning("Error processing alert '%s': %s", title, e)
                        continue

            self._state = len(alerts)
            self._attributes = {
                "alerts": alerts,
                "countries_monitored": countries,
                "vacation_period": f"{start.date()} to {end.date()}",
                "last_update": datetime.now().isoformat(),
                "feed_source": "RSS"
            }
            
            _LOGGER.info("Found %d weather alerts from RSS feed for monitored countries", len(alerts))

        except requests.exceptions.RequestException as e:
            _LOGGER.error("Failed to fetch RSS feed - Network error: %s", e)
            self._state = 0
            self._attributes = {"error": f"Network error: {str(e)}"}
        except ET.ParseError as e:
            _LOGGER.error("Failed to parse RSS XML: %s", e)
            self._state = 0
            self._attributes = {"error": f"XML parse error: {str(e)}"}
        except Exception as e:
            _LOGGER.error("Failed to process RSS feed: %s", e)
            self._state = 0
            self._attributes = {"error": str(e)}

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
        return "mdi:weather-lightning"