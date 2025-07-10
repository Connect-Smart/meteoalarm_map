import logging
import os
from datetime import timedelta, datetime
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Polygon
from matplotlib.collections import PatchCollection
from io import BytesIO
import warnings
import requests
import json
import xml.etree.ElementTree as ET

from homeassistant.components.camera import Camera
from homeassistant.util import Throttle
from .const import DOMAIN, CAMERA_NAME, IMAGE_PATH, RSS_FEED

# Suppress matplotlib warnings
warnings.filterwarnings('ignore')

_LOGGER = logging.getLogger(__name__)
MIN_TIME_BETWEEN_UPDATES = timedelta(minutes=10)

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the Meteoalarm camera from a config entry."""
    config = hass.data[DOMAIN]["config"]
    async_add_entities([MeteoalarmCamera(config)], True)

class MeteoalarmCamera(Camera):
    def __init__(self, config):
        super().__init__()
        self._name = CAMERA_NAME
        self._image_path = IMAGE_PATH
        self._last_image = None
        self._config = config
        
        # Alert level colors matching official Meteoalarm
        self.alert_colors = {
            'red': '#FF0000',      # Level 4 - Red - Extreme
            'orange': '#FF8C00',   # Level 3 - Orange - Severe  
            'yellow': '#FFD700',   # Level 2 - Yellow - Moderate
            'green': '#32CD32',    # Level 1 - Green - Minor
            'white': '#FFFFFF',    # Level 0 - White - No warning
            'unknown': '#CCCCCC',  # Gray - Unknown
            'no_alert': '#E8F4FD', # Light blue - Monitored, no alerts
            'not_monitored': '#F0F0F0'  # Light gray - Not monitored
        }
        
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
            'lt': 'lithuania'
        }
        
        # Cache for Europe map data
        self._europe_map_data = None

    def _normalize_country_name(self, country):
        """Normalize country name for consistent matching."""
        if not country:
            return ""
        
        country_lower = country.lower().strip()
        return self.country_mappings.get(country_lower, country_lower)

    def _extract_country_from_title(self, title):
        """Extract country name from the RSS item title."""
        if ':' in title:
            country = title.split(':')[0].strip().lower()
        elif ' - ' in title:
            country = title.split(' - ')[0].strip().lower()
        else:
            country = ""
        
        # Apply country mappings
        return self._normalize_country_name(country)

    def _parse_awareness_level(self, title, description):
        """Parse awareness level from title or description."""
        text = f"{title} {description}".lower()
        
        # Check for explicit level mentions first
        if 'red' in text or 'extreme' in text:
            return 'red'
        elif 'orange' in text or 'severe' in text:
            return 'orange'
        elif 'yellow' in text or 'moderate' in text:
            return 'yellow'
        elif 'green' in text or 'minor' in text:
            return 'green'
        
        # Fallback: try to determine from severity keywords
        if any(word in text for word in ['dangerous', 'life-threatening', 'catastrophic']):
            return 'red'
        elif any(word in text for word in ['significant', 'considerable', 'widespread']):
            return 'orange'
        elif any(word in text for word in ['possible', 'likely', 'expected']):
            return 'yellow'
            
        return 'unknown'

    def _get_alerts_from_rss(self):
        """Fetch alerts data from RSS feed."""
        try:
            alerts_by_country = {}
            monitored_countries = [c.lower() for c in self._config.get("countries", [])]
            
            _LOGGER.info("Fetching alerts from RSS feed for %d monitored countries", len(monitored_countries))
            
            # Get vacation period for filtering
            start_date = datetime.strptime(self._config.get("vacation_start"), "%Y-%m-%d").date()
            end_date = datetime.strptime(self._config.get("vacation_end"), "%Y-%m-%d").date()
            
            # Fetch RSS feed
            r = requests.get(RSS_FEED, timeout=15)
            r.raise_for_status()
            
            root = ET.fromstring(r.content)
            
            for item in root.findall('.//item'):
                title_elem = item.find('title')
                description_elem = item.find('description')
                pub_date_elem = item.find('pubDate')
                
                if title_elem is None or description_elem is None:
                    continue
                    
                title = title_elem.text or ""
                description = description_elem.text or ""
                pub_date = pub_date_elem.text if pub_date_elem is not None else ""
                
                country = self._extract_country_from_title(title)
                
                if country and country in monitored_countries:
                    try:
                        # Parse publication date
                        if pub_date:
                            try:
                                # RSS date format: "Wed, 10 Jul 2025 08:00:00 GMT"
                                event_time = datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %Z")
                            except ValueError:
                                # Try alternative format
                                event_time = datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %z")
                        else:
                            event_time = datetime.now()
                            
                        if start_date <= event_time.date() <= end_date:
                            level = self._parse_awareness_level(title, description)
                            
                            if country not in alerts_by_country:
                                alerts_by_country[country] = {
                                    'level': level,
                                    'count': 1,
                                    'titles': [title],
                                    'types': [self._extract_event_type(title, description)],
                                    'latest_date': pub_date
                                }
                            else:
                                alerts_by_country[country]['count'] += 1
                                alerts_by_country[country]['titles'].append(title)
                                alerts_by_country[country]['types'].append(self._extract_event_type(title, description))
                                
                                # Update to highest priority level
                                current_level = alerts_by_country[country]['level']
                                level_priority = {'red': 4, 'orange': 3, 'yellow': 2, 'green': 1, 'unknown': 0}
                                if level_priority.get(level, 0) > level_priority.get(current_level, 0):
                                    alerts_by_country[country]['level'] = level
                                    
                    except Exception as e:
                        _LOGGER.warning("Error processing alert '%s': %s", title, e)
                        continue
            
            _LOGGER.info("Successfully fetched %d countries with alerts from RSS feed", len(alerts_by_country))
            return alerts_by_country
            
        except Exception as e:
            _LOGGER.error("Error fetching alerts from RSS feed: %s", e)
            return {}

    def _extract_event_type(self, title, description):
        """Extract event type from title or description."""
        text = f"{title} {description}".lower()
        
        event_types = {
            'wind': ['wind', 'storm', 'gale'],
            'rain': ['rain', 'precipitation', 'shower'],
            'snow': ['snow', 'blizzard', 'snowfall'],
            'thunderstorm': ['thunderstorm', 'lightning', 'thunder'],
            'fog': ['fog', 'visibility', 'mist'],
            'temperature': ['temperature', 'heat', 'cold', 'frost', 'freeze'],
            'ice': ['ice', 'icing', 'freezing'],
            'flood': ['flood', 'flooding', 'water'],
            'coastal': ['coastal', 'sea', 'wave', 'tide']
        }
        
        for event_type, keywords in event_types.items():
            if any(keyword in text for keyword in keywords):
                return event_type
        
        return 'general'

    def _load_europe_map_data(self):
        """Load Europe map data from GeoJSON source."""
        if self._europe_map_data is not None:
            return self._europe_map_data
        
        try:
            # Try multiple GeoJSON sources for reliability
            geojson_sources = [
                "https://raw.githubusercontent.com/datasets/geo-countries/master/data/countries.geojson",
                "https://raw.githubusercontent.com/holtzy/D3-graph-gallery/master/DATA/world.geojson",
                "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_admin_0_countries.geojson"
            ]
            
            geojson_data = None
            for url in geojson_sources:
                try:
                    _LOGGER.info("Trying to load Europe map data from: %s", url)
                    response = requests.get(url, timeout=20)
                    response.raise_for_status()
                    geojson_data = response.json()
                    _LOGGER.info("Successfully loaded GeoJSON data from: %s", url)
                    break
                except Exception as e:
                    _LOGGER.warning("Failed to load from %s: %s", url, e)
                    continue
            
            if not geojson_data:
                _LOGGER.error("All GeoJSON sources failed, creating fallback data")
                return self._create_fallback_geojson()
            
            # Filter for European countries with comprehensive list
            european_countries = {
                'italy', 'spain', 'france', 'germany', 'united kingdom', 'poland',
                'netherlands', 'belgium', 'portugal', 'switzerland', 'austria',
                'norway', 'sweden', 'finland', 'denmark', 'czech republic',
                'slovakia', 'hungary', 'romania', 'bulgaria', 'greece',
                'croatia', 'slovenia', 'serbia', 'bosnia and herzegovina',
                'albania', 'montenegro', 'ireland', 'estonia', 'latvia',
                'lithuania', 'luxembourg', 'malta', 'cyprus', 'iceland',
                'ukraine', 'belarus', 'moldova', 'macedonia', 'kosovo',
                'czechia', 'north macedonia', 'turkey'
            }
            
            europe_features = []
            for feature in geojson_data.get('features', []):
                props = feature.get('properties', {})
                
                # Try multiple property names for country name
                country_name = None
                for prop_name in ['NAME', 'NAME_EN', 'ADMIN', 'name', 'country', 'Country']:
                    if prop_name in props:
                        country_name = props[prop_name]
                        break
                
                if not country_name:
                    continue
                
                country_name = country_name.lower()
                
                # Normalize country name
                normalized_name = self._normalize_country_name(country_name)
                
                if normalized_name in european_countries or country_name in european_countries:
                    # Add normalized name to properties
                    props['NORMALIZED_NAME'] = normalized_name if normalized_name in european_countries else country_name
                    europe_features.append(feature)
            
            if not europe_features:
                _LOGGER.warning("No European countries found in GeoJSON, creating fallback")
                return self._create_fallback_geojson()
            
            self._europe_map_data = {'type': 'FeatureCollection', 'features': europe_features}
            _LOGGER.info("Successfully loaded %d European countries from GeoJSON", len(europe_features))
            
            return self._europe_map_data
            
        except Exception as e:
            _LOGGER.error("Error loading Europe map data: %s", e)
            return self._create_fallback_geojson()

    def _create_fallback_geojson(self):
        """Create a simple fallback GeoJSON with basic European country shapes."""
        _LOGGER.info("Creating fallback GeoJSON data")
        
        # Simple polygon coordinates for major European countries
        fallback_countries = {
            'italy': [[[12.0, 46.0], [18.0, 40.0], [15.0, 37.0], [8.0, 39.0], [7.0, 44.0], [12.0, 46.0]]],
            'spain': [[[-9.0, 43.0], [3.0, 43.0], [3.0, 36.0], [-9.0, 36.0], [-9.0, 43.0]]],
            'france': [[[2.0, 51.0], [8.0, 49.0], [7.0, 43.0], [-1.0, 43.0], [-5.0, 48.0], [2.0, 51.0]]],
            'germany': [[[6.0, 55.0], [15.0, 54.0], [15.0, 47.0], [6.0, 47.0], [6.0, 55.0]]],
            'united kingdom': [[[-8.0, 60.0], [2.0, 60.0], [2.0, 50.0], [-8.0, 50.0], [-8.0, 60.0]]],
            'poland': [[[14.0, 54.0], [24.0, 54.0], [24.0, 49.0], [14.0, 49.0], [14.0, 54.0]]],
            'netherlands': [[[3.0, 54.0], [7.0, 54.0], [7.0, 51.0], [3.0, 51.0], [3.0, 54.0]]],
            'belgium': [[[2.5, 51.5], [6.5, 51.5], [6.5, 49.5], [2.5, 49.5], [2.5, 51.5]]],
            'portugal': [[[-9.5, 42.0], [-6.0, 42.0], [-6.0, 37.0], [-9.5, 37.0], [-9.5, 42.0]]],
            'switzerland': [[[6.0, 47.8], [10.5, 47.8], [10.5, 45.8], [6.0, 45.8], [6.0, 47.8]]],
            'austria': [[[9.5, 49.0], [17.0, 49.0], [17.0, 46.0], [9.5, 46.0], [9.5, 49.0]]],
            'norway': [[[5.0, 71.0], [31.0, 71.0], [31.0, 58.0], [5.0, 58.0], [5.0, 71.0]]],
            'sweden': [[[11.0, 69.0], [24.0, 69.0], [24.0, 55.0], [11.0, 55.0], [11.0, 69.0]]],
            'finland': [[[20.0, 70.0], [32.0, 70.0], [32.0, 60.0], [20.0, 60.0], [20.0, 70.0]]],
            'denmark': [[[8.0, 58.0], [13.0, 58.0], [13.0, 54.0], [8.0, 54.0], [8.0, 58.0]]],
            'czech republic': [[[12.0, 51.0], [19.0, 51.0], [19.0, 48.0], [12.0, 48.0], [12.0, 51.0]]],
            'slovakia': [[[17.0, 49.5], [22.5, 49.5], [22.5, 47.5], [17.0, 47.5], [17.0, 49.5]]],
            'hungary': [[[16.0, 48.5], [23.0, 48.5], [23.0, 45.5], [16.0, 45.5], [16.0, 48.5]]],
            'romania': [[[20.0, 48.0], [30.0, 48.0], [30.0, 43.0], [20.0, 43.0], [20.0, 48.0]]],
            'bulgaria': [[[22.0, 44.0], [29.0, 44.0], [29.0, 41.0], [22.0, 41.0], [22.0, 44.0]]],
            'greece': [[[19.0, 42.0], [28.0, 42.0], [28.0, 34.0], [19.0, 34.0], [19.0, 42.0]]],
            'croatia': [[[13.0, 46.5], [19.5, 46.5], [19.5, 42.5], [13.0, 42.5], [13.0, 46.5]]],
            'slovenia': [[[13.0, 47.0], [16.5, 47.0], [16.5, 45.0], [13.0, 45.0], [13.0, 47.0]]],
            'ireland': [[[-10.5, 55.5], [-5.5, 55.5], [-5.5, 51.5], [-10.5, 51.5], [-10.5, 55.5]]],
            'estonia': [[[21.0, 60.0], [28.0, 60.0], [28.0, 57.0], [21.0, 57.0], [21.0, 60.0]]],
            'latvia': [[[21.0, 58.0], [28.0, 58.0], [28.0, 55.0], [21.0, 55.0], [21.0, 58.0]]],
            'lithuania': [[[21.0, 56.5], [26.5, 56.5], [26.5, 53.5], [21.0, 53.5], [21.0, 56.5]]]
        }
        
        features = []
        for country_name, coordinates in fallback_countries.items():
            feature = {
                'type': 'Feature',
                'properties': {
                    'NAME': country_name.title(),
                    'NORMALIZED_NAME': country_name
                },
                'geometry': {
                    'type': 'Polygon',
                    'coordinates': coordinates
                }
            }
            features.append(feature)
        
        return {'type': 'FeatureCollection', 'features': features}

    def _create_country_polygons(self, map_data, warnings_by_country, monitored_countries):
        """Create matplotlib polygons for each country with appropriate colors."""
        patches = []
        colors = []
        
        if not map_data:
            return patches, colors
        
        for feature in map_data.get('features', []):
            try:
                props = feature.get('properties', {})
                country_name = props.get('NORMALIZED_NAME', props.get('NAME', '')).lower()
                
                geometry = feature.get('geometry', {})
                geom_type = geometry.get('type', '')
                coordinates = geometry.get('coordinates', [])
                
                # Determine country color
                if country_name in warnings_by_country:
                    color = self.alert_colors[warnings_by_country[country_name]['level']]
                elif country_name in monitored_countries:
                    color = self.alert_colors['no_alert']
                else:
                    color = self.alert_colors['not_monitored']
                
                # Process different geometry types
                if geom_type == 'Polygon':
                    # Single polygon
                    for ring in coordinates:
                        if len(ring) >= 3:  # Valid polygon needs at least 3 points
                            polygon = Polygon(ring, closed=True)
                            patches.append(polygon)
                            colors.append(color)
                
                elif geom_type == 'MultiPolygon':
                    # Multiple polygons (islands, etc.)
                    for polygon_coords in coordinates:
                        for ring in polygon_coords:
                            if len(ring) >= 3:
                                polygon = Polygon(ring, closed=True)
                                patches.append(polygon)
                                colors.append(color)
                
            except Exception as e:
                _LOGGER.debug("Error processing country polygon: %s", e)
                continue
        
        return patches, colors

    def _render_europe_map(self, warnings_by_country, monitored_countries):
        """Render a detailed Europe map with country polygons."""
        try:
            # Load Europe map data
            map_data = self._load_europe_map_data()
            
            if not map_data:
                return self._create_simple_fallback_map(warnings_by_country, monitored_countries)
            
            # Create matplotlib figure
            fig, ax = plt.subplots(figsize=(16, 12))
            fig.patch.set_facecolor('white')
            
            # Create country polygons
            patches, colors = self._create_country_polygons(map_data, warnings_by_country, monitored_countries)
            
            if patches:
                # Add country polygons to plot
                collection = PatchCollection(patches, facecolors=colors, edgecolors='black', 
                                           linewidths=0.5, alpha=0.8)
                ax.add_collection(collection)
                
                # Set Europe bounds
                ax.set_xlim(-25, 45)  # Longitude
                ax.set_ylim(35, 72)   # Latitude
            else:
                # Fallback to simple map
                return self._create_simple_fallback_map(warnings_by_country, monitored_countries)
            
            # Add title
            vacation_start = self._config.get("vacation_start", "Unknown")
            vacation_end = self._config.get("vacation_end", "Unknown")
            
            title = f'🌍 Meteoalarm Europe - Weather Warnings Map\n'
            title += f'🏖️ Vacation Period: {vacation_start} to {vacation_end}\n'
            title += f'🕐 Updated: {datetime.now().strftime("%d/%m/%Y %H:%M UTC")}'
            
            ax.set_title(title, fontsize=18, fontweight='bold', pad=25)
            
            # Remove axes
            ax.set_xticks([])
            ax.set_yticks([])
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)
            ax.spines['bottom'].set_visible(False)
            ax.spines['left'].set_visible(False)
            
            # Create legend
            legend_elements = [
                mpatches.Patch(color=self.alert_colors['red'], label='🔴 Red - Extreme Weather'),
                mpatches.Patch(color=self.alert_colors['orange'], label='🟠 Orange - Severe Weather'),
                mpatches.Patch(color=self.alert_colors['yellow'], label='🟡 Yellow - Moderate Weather'),
                mpatches.Patch(color=self.alert_colors['green'], label='🟢 Green - Minor Weather'),
                mpatches.Patch(color=self.alert_colors['white'], label='⚪ White - No Warning'),
                mpatches.Patch(color=self.alert_colors['no_alert'], label='💙 Monitored - No Warnings'),
                mpatches.Patch(color=self.alert_colors['not_monitored'], label='⚫ Not Monitored')
            ]
            
            ax.legend(handles=legend_elements, loc='lower left', bbox_to_anchor=(0.02, 0.02),
                     fontsize=11, frameon=True, fancybox=True, shadow=True, framealpha=0.95)
            
            # Add detailed statistics
            total_warnings = sum(w['count'] for w in warnings_by_country.values())
            countries_with_warnings = len(warnings_by_country)
            monitored_count = len(monitored_countries)
            
            # Count by level
            level_counts = {'red': 0, 'orange': 0, 'yellow': 0, 'green': 0, 'white': 0}
            for warning in warnings_by_country.values():
                level = warning['level']
                if level in level_counts:
                    level_counts[level] += 1
            
            stats_text = f"""🌦️ RSS Feed Data Source
📊 Monitoring: {monitored_count} countries
⚠️ Countries with warnings: {countries_with_warnings}
🚨 Total active warnings: {total_warnings}

📈 Warning Distribution:
   🔴 Red (Extreme): {level_counts['red']} countries
   🟠 Orange (Severe): {level_counts['orange']} countries  
   🟡 Yellow (Moderate): {level_counts['yellow']} countries
   🟢 Green (Minor): {level_counts['green']} countries
   ⚪ White (No warning): {level_counts['white']} countries"""
            
            ax.text(0.98, 0.98, stats_text, transform=ax.transAxes, fontsize=11,
                   verticalalignment='top', horizontalalignment='right',
                   bbox=dict(boxstyle='round', facecolor='white', alpha=0.95, pad=1.0),
                   family='monospace')
            
            # Add warning details for countries with alerts
            if warnings_by_country:
                details_text = "🚨 Active Warnings:\n"
                for country, warning in list(warnings_by_country.items())[:6]:
                    level_name = warning['level'].title()
                    count = warning['count']
                    types = ', '.join(set(warning['types'][:3]))  # Unique types, first 3
                    emoji = {'red': '🔴', 'orange': '🟠', 'yellow': '🟡', 'green': '🟢'}.get(warning['level'], '⚪')
                    details_text += f"{emoji} {country.title()}: {level_name} ({count}x)\n   🌪️ {types}\n"
                
                if len(warnings_by_country) > 6:
                    details_text += f"... and {len(warnings_by_country) - 6} more countries"
                
                ax.text(0.02, 0.65, details_text, transform=ax.transAxes, fontsize=10,
                       verticalalignment='top', horizontalalignment='left',
                       bbox=dict(boxstyle='round', facecolor='lightyellow', alpha=0.95, pad=0.8))
            
            # Add branding
            ax.text(0.5, 0.02, '🌦️ Powered by Meteoalarm RSS Feed', 
                   transform=ax.transAxes, fontsize=10, ha='center', va='bottom',
                   bbox=dict(boxstyle='round', facecolor='lightblue', alpha=0.8))
            
            # Save to buffer
            buffer = BytesIO()
            plt.savefig(buffer, format='png', dpi=200, bbox_inches='tight',
                       facecolor='white', edgecolor='none', pad_inches=0.3)
            plt.close(fig)
            buffer.seek(0)
            
            _LOGGER.info("Successfully rendered detailed Europe map with country polygons")
            return buffer.read()
            
        except Exception as e:
            _LOGGER.error("Error rendering Europe map: %s", e)
            if 'fig' in locals():
                plt.close(fig)
            return self._create_simple_fallback_map(warnings_by_country, monitored_countries)

    def _create_simple_fallback_map(self, warnings_by_country, monitored_countries):
        """Create a simple fallback map if detailed map fails."""
        try:
            fig, ax = plt.subplots(figsize=(12, 8))
            fig.patch.set_facecolor('white')
            
            # Simple Europe outline
            ax.text(0.5, 0.5, '🗺️ Detailed Europe Map\nTemporarily Unavailable\n\n📊 Using Simple View', 
                   transform=ax.transAxes, fontsize=16, ha='center', va='center',
                   bbox=dict(boxstyle='round', facecolor='lightgray', alpha=0.8))
            
            # Add statistics
            total_warnings = sum(w['count'] for w in warnings_by_country.values())
            countries_with_warnings = len(warnings_by_country)
            
            stats_text = f"""⚠️ Current Warnings: {total_warnings}
📊 Countries affected: {countries_with_warnings}
🌍 Monitoring: {len(monitored_countries)} countries"""
            
            ax.text(0.02, 0.98, stats_text, transform=ax.transAxes, fontsize=12,
                   verticalalignment='top', horizontalalignment='left',
                   bbox=dict(boxstyle='round', facecolor='yellow', alpha=0.9))
            
            ax.set_xlim(0, 1)
            ax.set_ylim(0, 1)
            ax.set_axis_off()
            
            buffer = BytesIO()
            plt.savefig(buffer, format='png', dpi=150, bbox_inches='tight')
            plt.close(fig)
            buffer.seek(0)
            
            return buffer.read()
            
        except Exception as e:
            _LOGGER.error("Error creating fallback map: %s", e)
            return self._create_error_image(str(e))

    def _create_error_image(self, error_msg):
        """Create a simple error image."""
        try:
            fig, ax = plt.subplots(figsize=(10, 6))
            fig.patch.set_facecolor('lightcoral')
            
            ax.text(0.5, 0.5, 
                   f'❌ Meteoalarm Map Error\n\n{error_msg}\n\n🔄 Retrying in {MIN_TIME_BETWEEN_UPDATES}...',
                   transform=ax.transAxes, fontsize=14, ha='center', va='center', color='darkred',
                   bbox=dict(boxstyle='round', facecolor='white', alpha=0.9))
            
            ax.set_xlim(0, 1)
            ax.set_ylim(0, 1)
            ax.set_axis_off()
            
            buffer = BytesIO()
            plt.savefig(buffer, format='png', bbox_inches='tight')
            plt.close(fig)
            buffer.seek(0)
            
            return buffer.read()
            
        except Exception as e:
            _LOGGER.error("Could not create error image: %s", e)
            return b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x01\x00\x00\x00\x01\x00\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x00\x00\x00\x00\x00\x01\x00\x01'

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    def update(self):
        """Update the camera image using RSS feed data and custom Europe map."""
        try:
            _LOGGER.info("Updating detailed Europe map with RSS feed data...")
            
            # Get alerts data from RSS
            alerts_data = self._get_alerts_from_rss()
            
            # Get monitored countries (normalized)
            monitored_countries = [self._normalize_country_name(c) for c in self._config.get("countries", [])]
            
            # Render the detailed Europe map
            image_data = self._render_europe_map(alerts_data, monitored_countries)
            
            # Store the image
            self._last_image = image_data
            
            # Save to file
            os.makedirs(os.path.dirname(self._image_path), exist_ok=True)
            with open(self._image_path, "wb") as file:
                file.write(self._last_image)
            
            total_warnings = sum(w['count'] for w in alerts_data.values())
            countries_count = len(alerts_data)
            _LOGGER.info("Generated detailed Europe map: %d countries with %d total warnings", 
                        countries_count, total_warnings)
            
        except Exception as e:
            _LOGGER.error("Error generating detailed Europe map: %s", e)
            self._last_image = self._create_error_image(str(e))

    def camera_image(self, width=None, height=None):
        """Return camera image bytes."""
        if self._last_image is None:
            self.update()
        return self._last_image

    async def async_camera_image(self, width=None, height=None):
        """Return camera image bytes asynchronously."""
        return await self.hass.async_add_executor_job(self.camera_image, width, height)

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        """Return a unique ID for this camera."""
        return f"{DOMAIN}_camera"