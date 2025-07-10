import logging
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Dict, List, Optional

_LOGGER = logging.getLogger(__name__)

class MeteoalarmRSSReader:
    """Centralized RSS feed reader for Meteoalarm data."""
    
    def __init__(self, rss_url: str):
        self.rss_url = rss_url
        self._cached_data = None
        self._last_update = None
        
        # Country name mappings for consistent naming
        self.country_mappings = {
            'gb': 'united kingdom',
            'uk': 'united kingdom',
            'great britain': 'united kingdom',
            'england': 'united kingdom',
            'scotland': 'united kingdom',
            'wales': 'united kingdom',
            'northern ireland': 'united kingdom',
            'cz': 'czech republic',
            'czechia': 'czech republic',
            'bosnia': 'bosnia and herzegovina',
            'north macedonia': 'macedonia',
            'macedonia (the former yugoslav republic of)': 'macedonia',
            'the former yugoslav republic of macedonia': 'macedonia',
            'the netherlands': 'netherlands',
            'holland': 'netherlands',
            'de': 'germany',
            'deutschland': 'germany',
            'fr': 'france',
            'it': 'italy',
            'italia': 'italy',
            'es': 'spain',
            'españa': 'spain',
            'pt': 'portugal',
            'nl': 'netherlands',
            'be': 'belgium',
            'ch': 'switzerland',
            'at': 'austria',
            'pl': 'poland',
            'no': 'norway',
            'se': 'sweden',
            'fi': 'finland',
            'dk': 'denmark',
            'ie': 'ireland',
            'gr': 'greece',
            'bg': 'bulgaria',
            'ro': 'romania',
            'hu': 'hungary',
            'hr': 'croatia',
            'si': 'slovenia',
            'sk': 'slovakia',
            'ee': 'estonia',
            'lv': 'latvia',
            'lt': 'lithuania',
            'ua': 'ukraine',
            'rs': 'serbia',
            'ba': 'bosnia and herzegovina',
            'mk': 'macedonia',
            'il': 'israel',
            'cy': 'cyprus'
        }

    def _normalize_country_name(self, country: str) -> str:
        """Normalize country name for consistent matching."""
        if not country:
            return ""
        
        country_lower = country.lower().strip()
        return self.country_mappings.get(country_lower, country_lower)

    def _extract_country_from_title(self, title: str) -> str:
        """Extract country name from the RSS item title."""
        title = title.strip().lower()
        
        # Remove "meteoalarm " prefix
        if title.startswith('meteoalarm '):
            country = title[11:].strip()
        else:
            country = title
        
        # Apply country mappings
        return self._normalize_country_name(country)

    def _parse_awareness_level_from_description(self, description: str) -> str:
        """Parse awareness level from HTML description using data attributes."""
        try:
            # Look for data-awareness-level attribute in the description
            import re
            level_matches = re.findall(r'data-awareness-level="(\d+)"', description)
            if level_matches:
                # Get the highest level found
                max_level = max(int(level) for level in level_matches)
                level_map = {
                    1: 'green',
                    2: 'yellow', 
                    3: 'orange',
                    4: 'red'
                }
                return level_map.get(max_level, 'unknown')
        except Exception as e:
            _LOGGER.debug("Error parsing awareness level from description: %s", e)
        
        return 'unknown'

    def _parse_awareness_type_from_description(self, description: str) -> List[str]:
        """Parse awareness types from HTML description using data attributes."""
        try:
            import re
            type_matches = re.findall(r'data-awareness-type="(\d+)"', description)
            if type_matches:
                # Map awareness types based on Meteoalarm standard
                type_map = {
                    '1': 'wind',
                    '2': 'snow',
                    '3': 'thunderstorm', 
                    '4': 'fog',
                    '5': 'temperature',
                    '6': 'coastal',
                    '7': 'forest_fire',
                    '8': 'avalanche',
                    '9': 'rain',
                    '10': 'flood',
                    '11': 'rain_flood',
                    '12': 'fire'
                }
                return [type_map.get(t, f'type_{t}') for t in set(type_matches)]
        except Exception as e:
            _LOGGER.debug("Error parsing awareness types from description: %s", e)
        
        return ['unknown']

    def _parse_time_periods(self, description: str) -> List[Dict]:
        """Parse time periods from description."""
        try:
            import re
            # Find all time periods in the format: From: 2025-07-10T11:03:12+00:00 Until: 2025-07-10T12:03:12+00:00
            time_pattern = r'<b>From: </b><i>([^<]+)</i><b> Until: </b><i>([^<]+)</i>'
            matches = re.findall(time_pattern, description)
            
            periods = []
            for from_time, until_time in matches:
                try:
                    from_dt = datetime.fromisoformat(from_time.replace('Z', '+00:00'))
                    until_dt = datetime.fromisoformat(until_time.replace('Z', '+00:00'))
                    periods.append({
                        'from': from_dt,
                        'until': until_dt,
                        'from_str': from_time,
                        'until_str': until_time
                    })
                except ValueError as e:
                    _LOGGER.debug("Error parsing time period: %s", e)
                    continue
            
            return periods
        except Exception as e:
            _LOGGER.debug("Error parsing time periods: %s", e)
            return []

    def _is_alert_relevant(self, alert_time: datetime, periods: List[Dict], start_date: datetime, end_date: datetime) -> bool:
        """Check if alert is relevant for the given date range."""
        try:
            # Check if alert publication date is within range
            if start_date.date() <= alert_time.date() <= end_date.date():
                return True
            
            # Check if any period overlaps with the date range
            for period in periods:
                period_start = period['from'].date()
                period_end = period['until'].date()
                
                # Check if there's any overlap
                if not (period_end < start_date.date() or period_start > end_date.date()):
                    return True
            
            return False
        except Exception as e:
            _LOGGER.debug("Error checking alert relevance: %s", e)
            return True  # Default to including the alert if we can't determine

    def fetch_alerts(self, monitored_countries: List[str], start_date: datetime, end_date: datetime) -> Dict:
        """
        Fetch and parse alerts from RSS feed.
        
        Args:
            monitored_countries: List of country names to monitor
            start_date: Start date for filtering alerts
            end_date: End date for filtering alerts
            
        Returns:
            Dict with alert data grouped by country
        """
        try:
            _LOGGER.info("Fetching alerts from RSS feed for %d monitored countries", len(monitored_countries))
            
            # Normalize monitored countries
            normalized_countries = [self._normalize_country_name(c) for c in monitored_countries]
            _LOGGER.debug("Normalized countries: %s", normalized_countries)
            
            # Fetch RSS feed
            response = requests.get(self.rss_url, timeout=15)
            response.raise_for_status()
            
            # Parse XML
            root = ET.fromstring(response.content)
            
            alerts_by_country = {}
            total_items_processed = 0
            
            # Process each RSS item
            for item in root.findall('.//item'):
                title_elem = item.find('title')
                description_elem = item.find('description')
                pub_date_elem = item.find('pubDate')
                link_elem = item.find('link')
                guid_elem = item.find('guid')
                
                if title_elem is None or description_elem is None:
                    continue
                
                total_items_processed += 1
                
                title = title_elem.text or ""
                description = description_elem.text or ""
                pub_date = pub_date_elem.text if pub_date_elem is not None else ""
                link = link_elem.text if link_elem is not None else ""
                guid = guid_elem.text if guid_elem is not None else ""
                
                # Extract country from title
                country = self._extract_country_from_title(title)
                _LOGGER.debug("Processing: %s -> country: %s", title, country)
                
                if country and country in normalized_countries:
                    try:
                        # Parse publication date
                        if pub_date:
                            try:
                                # Use JavaScript Date parsing which is more flexible
                                event_time = datetime.strptime(pub_date, "%a, %d %b %y %H:%M:%S %z")
                            except ValueError:
                                try:
                                    event_time = datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %z")
                                except ValueError:
                                    _LOGGER.warning("Could not parse date: %s", pub_date)
                                    event_time = datetime.now()
                        else:
                            event_time = datetime.now()
                        
                        # Parse alert details from description
                        level = self._parse_awareness_level_from_description(description)
                        types = self._parse_awareness_type_from_description(description)
                        periods = self._parse_time_periods(description)
                        
                        # Check if alert is relevant (more flexible date checking)
                        if self._is_alert_relevant(event_time, periods, start_date, end_date):
                            # Count individual alerts within the description
                            import re
                            alert_count = len(re.findall(r'data-awareness-level="(\d+)"', description))
                            if alert_count == 0:
                                alert_count = 1  # Fallback to 1 if no specific alerts found
                            
                            # Create alert object
                            alert = {
                                "country": country,
                                "title": title,
                                "level": level,
                                "types": types,
                                "description": description[:500] + "..." if len(description) > 500 else description,
                                "pub_date": pub_date,
                                "event_time": event_time,
                                "link": link,
                                "guid": guid,
                                "periods": periods,
                                "alert_count": alert_count,
                                "raw_description": description
                            }
                            
                            # Group by country
                            if country not in alerts_by_country:
                                alerts_by_country[country] = {
                                    'level': level,
                                    'count': alert_count,
                                    'alerts': [alert],
                                    'types': types.copy(),
                                    'latest_date': pub_date,
                                    'highest_level_numeric': self._level_to_numeric(level)
                                }
                            else:
                                alerts_by_country[country]['count'] += alert_count
                                alerts_by_country[country]['alerts'].append(alert)
                                
                                # Add new types
                                for alert_type in types:
                                    if alert_type not in alerts_by_country[country]['types']:
                                        alerts_by_country[country]['types'].append(alert_type)
                                
                                # Update to highest priority level
                                current_level_numeric = alerts_by_country[country]['highest_level_numeric']
                                new_level_numeric = self._level_to_numeric(level)
                                
                                if new_level_numeric > current_level_numeric:
                                    alerts_by_country[country]['level'] = level
                                    alerts_by_country[country]['highest_level_numeric'] = new_level_numeric
                                    alerts_by_country[country]['latest_date'] = pub_date
                            
                            _LOGGER.debug("Added alert for %s: %d alerts, level %s", 
                                        country, alert_count, level)
                    
                    except Exception as e:
                        _LOGGER.warning("Error processing alert '%s': %s", title, e)
                        continue
            
            # Cache the results
            self._cached_data = alerts_by_country
            self._last_update = datetime.now()
            
            total_countries_with_alerts = len(alerts_by_country)
            total_alerts = sum(country_data['count'] for country_data in alerts_by_country.values())
            
            _LOGGER.info(
                "Successfully processed %d RSS items, found %d countries with %d total alerts",
                total_items_processed, total_countries_with_alerts, total_alerts
            )
            
            # Debug output
            for country, data in alerts_by_country.items():
                _LOGGER.debug("Country %s: %d alerts, level %s, types %s", 
                            country, data['count'], data['level'], data['types'])
            
            return alerts_by_country
            
        except requests.exceptions.RequestException as e:
            _LOGGER.error("Failed to fetch RSS feed - Network error: %s", e)
            return {}
        except ET.ParseError as e:
            _LOGGER.error("Failed to parse RSS XML: %s", e)
            return {}
        except Exception as e:
            _LOGGER.error("Failed to process RSS feed: %s", e)
            return {}

    def _level_to_numeric(self, level: str) -> int:
        """Convert alert level to numeric value for comparison."""
        level_map = {
            'red': 4,
            'orange': 3, 
            'yellow': 2,
            'green': 1,
            'white': 0,
            'unknown': 0
        }
        return level_map.get(level, 0)

    def get_alerts_for_sensor(self, monitored_countries: List[str], start_date: datetime, end_date: datetime) -> Dict:
        """Get alerts formatted for sensor use."""
        alerts_data = self.fetch_alerts(monitored_countries, start_date, end_date)
        
        # Convert to sensor format
        sensor_alerts = []
        total_alert_count = 0
        
        for country, data in alerts_data.items():
            for alert in data['alerts']:
                # Create individual sensor alerts for each alert type found
                for i in range(alert['alert_count']):
                    sensor_alert = {
                        "country": country,
                        "event": alert['title'],
                        "level": alert['level'],
                        "type": ', '.join(alert['types']) if alert['types'] else 'unknown',
                        "description": alert['description'],
                        "pub_date": alert['pub_date'],
                        "link": alert['link']
                    }
                    sensor_alerts.append(sensor_alert)
                    total_alert_count += 1
        
        return {
            'alerts': sensor_alerts,
            'total_count': total_alert_count,
            'countries_affected': len(alerts_data),
            'alerts_by_country': alerts_data
        }

    def get_alerts_for_camera(self, monitored_countries: List[str], start_date: datetime, end_date: datetime) -> Dict:
        """Get alerts formatted for camera/map visualization."""
        alerts_data = self.fetch_alerts(monitored_countries, start_date, end_date)
        
        # Format for camera use - simplified structure
        camera_alerts = {}
        for country, data in alerts_data.items():
            camera_alerts[country] = {
                'level': data['level'],
                'count': data['count'],
                'types': data['types'],
                'titles': [alert['title'] for alert in data['alerts']],
                'latest_date': data['latest_date'],
                'periods': data['alerts'][0].get('periods', []) if data['alerts'] else []
            }
        
        return camera_alerts

    @property
    def last_update(self) -> Optional[datetime]:
        """Get the timestamp of the last successful update."""
        return self._last_update

    @property
    def cached_data(self) -> Optional[Dict]:
        """Get the cached alert data."""
        return self._cached_data