import logging
import os
from datetime import timedelta, datetime
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Polygon
from matplotlib.collections import PatchCollection
from io import BytesIO
import warnings
import asyncio
from meteoalarm import MeteoAlarm
import requests
import json

from homeassistant.components.camera import Camera
from homeassistant.util import Throttle
from .const import DOMAIN, CAMERA_NAME, IMAGE_PATH

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
        
        # Initialize MeteoAlarm client with monitored countries
        monitored_countries = [c.lower() for c in config.get("countries", [])]
        self._meteoalarm = MeteoAlarm(countries=monitored_countries)
        
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
        
        # Meteoalarm awareness level mapping
        self.level_mapping = {
            4: 'red',     # Extreme
            3: 'orange',  # Severe
            2: 'yellow',  # Moderate
            1: 'green',   # Minor
            0: 'white'    # No warning
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

    def _load_europe_map_data(self):
        """Load Europe map data from GeoJSON source."""
        if self._europe_map_data is not None:
            return self._europe_map_data
        
        try:
            # Use a reliable source for European countries GeoJSON
            geojson_url = "https://raw.githubusercontent.com/holtzy/D3-graph-gallery/master/DATA/world.geojson"
            
            _LOGGER.info("Loading Europe map data from GeoJSON...")
            response = requests.get(geojson_url, timeout=30)
            response.raise_for_status()
            
            geojson_data = response.json()
            
            # Filter for European countries
            european_countries = [
                'italy', 'spain', 'france', 'germany', 'united kingdom', 'poland',
                'netherlands', 'belgium', 'portugal', 'switzerland', 'austria',
                'norway', 'sweden', 'finland', 'denmark', 'czech republic',
                'slovakia', 'hungary', 'romania', 'bulgaria', 'greece',
                'croatia', 'slovenia', 'serbia', 'bosnia and herzegovina',
                'albania', 'montenegro', 'ireland', 'estonia', 'latvia',
                'lithuania', 'luxembourg', 'malta', 'cyprus', 'iceland',
                'ukraine', 'belarus', 'moldova', 'macedonia', 'kosovo'
            ]
            
            europe_features = []
            for feature in geojson_data.get('features', []):
                props = feature.get('properties', {})
                country_name = props.get('NAME', '').lower()
                
                # Normalize country name
                normalized_name = self._normalize_country_name(country_name)
                
                if normalized_name in european_countries or country_name in european_countries:
                    # Add normalized name to properties
                    props['NORMALIZED_NAME'] = normalized_name
                    europe_features.append(feature)
            
            self._europe_map_data = {'type': 'FeatureCollection', 'features': europe_features}
            _LOGGER.info("Loaded %d European countries from GeoJSON", len(europe_features))
            
            return self._europe_map_data
            
        except Exception as e:
            _LOGGER.error("Error loading Europe map data: %s", e)
            return None

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

    async def _get_alerts_data_async(self):
        """Fetch alerts data using the official Meteoalarm library."""
        try:
            alerts_by_country = {}
            monitored_countries = [c.lower() for c in self._config.get("countries", [])]
            
            _LOGGER.info("Fetching alerts for %d monitored countries using Meteoalarm library", len(monitored_countries))
            
            # Get vacation period for filtering
            start_date = datetime.strptime(self._config.get("vacation_start"), "%Y-%m-%d").date()
            end_date = datetime.strptime(self._config.get("vacation_end"), "%Y-%m-%d").date()
            
            # Get all alerts (MeteoAlarm is already configured with countries)
            alerts = await self._meteoalarm.async_get_alerts()
            
            if alerts:
                for alert in alerts:
                    try:
                        # Get country from alert
                        country = None
                        if hasattr(alert, 'country'):
                            country = alert.country.lower()
                        elif hasattr(alert, 'area'):
                            # Try to extract country from area
                            area = alert.area.lower()
                            for monitored in monitored_countries:
                                if monitored in area:
                                    country = monitored
                                    break
                        
                        if not country:
                            continue
                        
                        # Normalize country name
                        normalized_country = self._normalize_country_name(country)
                        
                        # Check if alert is within vacation period
                        alert_date = None
                        if hasattr(alert, 'onset') and alert.onset:
                            alert_date = alert.onset.date()
                        elif hasattr(alert, 'effective') and alert.effective:
                            alert_date = alert.effective.date()
                        else:
                            alert_date = datetime.now().date()
                        
                        if start_date <= alert_date <= end_date:
                            # Track highest alert level per country
                            if hasattr(alert, 'awareness_level'):
                                level = alert.awareness_level
                            elif hasattr(alert, 'severity'):
                                # Map severity to level if needed
                                severity_map = {
                                    'Extreme': 4, 'Severe': 3, 
                                    'Moderate': 2, 'Minor': 1, 
                                    'Unknown': 0
                                }
                                level = severity_map.get(alert.severity, 0)
                            else:
                                level = 0
                            
                            if normalized_country not in alerts_by_country:
                                alerts_by_country[normalized_country] = {
                                    'level': self.level_mapping.get(level, 'unknown'),
                                    'level_num': level,
                                    'count': 1,
                                    'alerts': [alert],
                                    'types': [getattr(alert, 'event', 'Unknown')]
                                }
                            else:
                                alerts_by_country[normalized_country]['count'] += 1
                                alerts_by_country[normalized_country]['alerts'].append(alert)
                                alerts_by_country[normalized_country]['types'].append(getattr(alert, 'event', 'Unknown'))
                                
                                # Update to highest priority level
                                current_level = alerts_by_country[normalized_country]['level_num']
                                if level > current_level:
                                    alerts_by_country[normalized_country]['level'] = self.level_mapping.get(level, 'unknown')
                                    alerts_by_country[normalized_country]['level_num'] = level
                        
                    except Exception as e:
                        _LOGGER.warning("Error processing alert: %s", e)
                        continue
            
            _LOGGER.info("Successfully fetched alerts for %d countries using official library", len(alerts_by_country))
            return alerts_by_country
            
        except Exception as e:
            _LOGGER.error("Error fetching alerts data with Meteoalarm library: %s", e)
            return {}

    def _get_alerts_data(self):
        """Synchronous wrapper for async alert fetching."""
        try:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            return loop.run_until_complete(self._get_alerts_data_async())
        except Exception as e:
            _LOGGER.error("Error in sync wrapper for alerts: %s", e)
            return {}

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
            
            title = f'Official Meteoalarm Europe - Weather Warnings Map\n'
            title += f'Vacation Period: {vacation_start} to {vacation_end}\n'
            title += f'Updated: {datetime.now().strftime("%d/%m/%Y %H:%M UTC")}'
            
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
                mpatches.Patch(color=self.alert_colors['red'], label='Red (Level 4) - Extreme Weather'),
                mpatches.Patch(color=self.alert_colors['orange'], label='Orange (Level 3) - Severe Weather'),
                mpatches.Patch(color=self.alert_colors['yellow'], label='Yellow (Level 2) - Moderate Weather'),
                mpatches.Patch(color=self.alert_colors['green'], label='Green (Level 1) - Minor Weather'),
                mpatches.Patch(color=self.alert_colors['white'], label='White (Level 0) - No Warning'),
                mpatches.Patch(color=self.alert_colors['no_alert'], label='Monitored - No Current Warnings'),
                mpatches.Patch(color=self.alert_colors['not_monitored'], label='Not Monitored')
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
            
            stats_text = f"""🌍 Official Meteoalarm Data
📊 Monitoring: {monitored_count} countries
⚠️  Countries with warnings: {countries_with_warnings}
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
                    details_text += f"{emoji} {country.title()}: {level_name} ({count}x)\n   {types}\n"
                
                if len(warnings_by_country) > 6:
                    details_text += f"... and {len(warnings_by_country) - 6} more countries"
                
                ax.text(0.02, 0.65, details_text, transform=ax.transAxes, fontsize=10,
                       verticalalignment='top', horizontalalignment='left',
                       bbox=dict(boxstyle='round', facecolor='lightyellow', alpha=0.95, pad=0.8))
            
            # Add branding
            ax.text(0.5, 0.02, '🌦️ Powered by Official Meteoalarm API', 
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
            ax.text(0.5, 0.5, '🗺️ Detailed Europe Map\nTemporarily Unavailable\n\nUsing Simple View', 
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
        """Update the camera image using official Meteoalarm library and custom Europe map."""
        try:
            _LOGGER.info("Updating detailed Europe map with official Meteoalarm library...")
            
            # Get alerts data using official library
            alerts_data = self._get_alerts_data()
            
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