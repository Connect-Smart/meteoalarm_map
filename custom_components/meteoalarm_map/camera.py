import logging
import os
from datetime import timedelta, datetime
import geopandas as gpd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from io import BytesIO
import warnings
import asyncio
from meteoalarm import Meteoalarm

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
        
        # Initialize Meteoalarm client
        self._meteoalarm = Meteoalarm()
        
        # Alert level colors matching official Meteoalarm
        self.alert_colors = {
            'red': '#FF0000',      # Level 4 - Red - Extreme
            'orange': '#FF8C00',   # Level 3 - Orange - Severe  
            'yellow': '#FFD700',   # Level 2 - Yellow - Moderate
            'green': '#32CD32',    # Level 1 - Green - Minor
            'white': '#FFFFFF',    # Level 0 - White - No warning
            'unknown': '#CCCCCC',  # Gray - Unknown
            'no_alert': '#E8F4FD', # Light blue - Monitored, no alerts
            'not_monitored': '#F5F5F5'  # Very light gray - Not monitored
        }
        
        # Meteoalarm awareness level mapping
        self.level_mapping = {
            4: 'red',     # Extreme
            3: 'orange',  # Severe
            2: 'yellow',  # Moderate
            1: 'green',   # Minor
            0: 'white'    # No warning
        }
        
        # Country code to name mapping for common variations
        self.country_mappings = {
            'gb': 'united kingdom',
            'uk': 'united kingdom',
            'cz': 'czechia',
            'czech republic': 'czechia',
            'bosnia and herzegovina': 'bosnia and herzegovina',
            'bosnia': 'bosnia and herzegovina',
            'north macedonia': 'north macedonia',
            'macedonia': 'north macedonia',
            'the netherlands': 'netherlands',
            'holland': 'netherlands',
            'de': 'germany',
            'fr': 'france',
            'it': 'italy',
            'es': 'spain',
            'pt': 'portugal',
            'nl': 'netherlands',
            'be': 'belgium',
            'ch': 'switzerland',
            'at': 'austria',
            'pl': 'poland',
            'no': 'norway',
            'se': 'sweden',
            'fi': 'finland',
            'dk': 'denmark'
        }

    def _normalize_country_name(self, country):
        """Normalize country name for consistent matching."""
        if not country:
            return ""
        
        country_lower = country.lower().strip()
        return self.country_mappings.get(country_lower, country_lower)

    async def _get_alerts_data_async(self):
        """Fetch alerts data using the official Meteoalarm library."""
        try:
            alerts_by_country = {}
            monitored_countries = [c.lower() for c in self._config.get("countries", [])]
            
            _LOGGER.info("Fetching alerts for %d monitored countries", len(monitored_countries))
            
            # Get vacation period for filtering
            start_date = datetime.strptime(self._config.get("vacation_start"), "%Y-%m-%d").date()
            end_date = datetime.strptime(self._config.get("vacation_end"), "%Y-%m-%d").date()
            
            for country in monitored_countries:
                try:
                    # Normalize country name
                    normalized_country = self._normalize_country_name(country)
                    
                    # Get alerts for this country
                    alerts = await self._meteoalarm.get_alerts_async(country=normalized_country)
                    
                    if alerts:
                        country_alerts = []
                        max_level = 0
                        
                        for alert in alerts:
                            # Check if alert is within vacation period
                            alert_date = None
                            if hasattr(alert, 'onset') and alert.onset:
                                alert_date = alert.onset.date()
                            elif hasattr(alert, 'effective') and alert.effective:
                                alert_date = alert.effective.date()
                            else:
                                alert_date = datetime.now().date()
                            
                            if start_date <= alert_date <= end_date:
                                country_alerts.append(alert)
                                
                                # Track highest alert level
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
                                
                                max_level = max(max_level, level)
                        
                        if country_alerts:
                            alerts_by_country[normalized_country] = {
                                'level': self.level_mapping.get(max_level, 'unknown'),
                                'level_num': max_level,
                                'count': len(country_alerts),
                                'alerts': country_alerts,
                                'types': [getattr(alert, 'event', 'Unknown') for alert in country_alerts[:3]]  # First 3 types
                            }
                            
                            _LOGGER.info("Found %d alerts for %s (max level: %d)", 
                                       len(country_alerts), country, max_level)
                    
                except Exception as e:
                    _LOGGER.warning("Failed to get alerts for %s: %s", country, e)
                    continue
            
            _LOGGER.info("Successfully fetched alerts for %d countries", len(alerts_by_country))
            return alerts_by_country
            
        except Exception as e:
            _LOGGER.error("Error fetching alerts data with Meteoalarm library: %s", e)
            return {}

    def _get_alerts_data(self):
        """Synchronous wrapper for async alert fetching."""
        try:
            # Create new event loop if needed
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            return loop.run_until_complete(self._get_alerts_data_async())
        except Exception as e:
            _LOGGER.error("Error in sync wrapper for alerts: %s", e)
            return {}

    def _render_warning_map(self, warnings_by_country, monitored_countries):
        """Render the warning map using GeoPandas and matplotlib."""
        try:
            # Load world map data
            _LOGGER.info("Loading world map data...")
            world = gpd.read_file("https://raw.githubusercontent.com/datasets/geo-countries/master/data/countries.geojson")
            
            # Function to determine color for each country
            def get_country_color(admin_name):
                country_name = self._normalize_country_name(admin_name)
                
                # Check if country has warnings
                if country_name in warnings_by_country:
                    level = warnings_by_country[country_name]["level"]
                    return self.alert_colors.get(level, self.alert_colors['unknown'])
                
                # Check if country is monitored (without warnings)
                elif country_name in monitored_countries:
                    return self.alert_colors['no_alert']
                
                # Not monitored
                else:
                    return self.alert_colors['not_monitored']
            
            # Apply colors to countries
            world['warning_color'] = world['ADMIN'].apply(get_country_color)
            
            # Set up the plot with proper styling
            plt.style.use('default')
            fig, ax = plt.subplots(figsize=(14, 10))
            fig.patch.set_facecolor('white')
            
            # Focus on Europe bounds
            europe_bounds = [-15, 35, 35, 75]  # [west, south, east, north]
            ax.set_xlim(europe_bounds[0], europe_bounds[2])
            ax.set_ylim(europe_bounds[1], europe_bounds[3])
            
            # Plot the world map with colors
            world.plot(
                ax=ax, 
                color=world['warning_color'], 
                edgecolor='#333333', 
                linewidth=0.6,
                alpha=0.9
            )
            
            # Add title with vacation period info
            vacation_start = self._config.get("vacation_start", "Unknown")
            vacation_end = self._config.get("vacation_end", "Unknown")
            
            title = f'Meteoalarm Europe - Weather Warnings\n'
            title += f'Vacation Period: {vacation_start} to {vacation_end}\n'
            title += f'Updated: {datetime.now().strftime("%d/%m/%Y %H:%M UTC")}'
            
            ax.set_title(
                title,
                fontsize=16, 
                fontweight='bold',
                pad=20
            )
            
            # Remove axes
            ax.set_axis_off()
            
            # Create legend with official Meteoalarm levels
            legend_elements = [
                mpatches.Patch(color=self.alert_colors['red'], label='Red (Level 4) - Extreme Weather'),
                mpatches.Patch(color=self.alert_colors['orange'], label='Orange (Level 3) - Severe Weather'),
                mpatches.Patch(color=self.alert_colors['yellow'], label='Yellow (Level 2) - Moderate Weather'),
                mpatches.Patch(color=self.alert_colors['green'], label='Green (Level 1) - Minor Weather'),
                mpatches.Patch(color=self.alert_colors['white'], label='White (Level 0) - No Warning'),
                mpatches.Patch(color=self.alert_colors['no_alert'], label='Monitored - No Current Warnings'),
                mpatches.Patch(color=self.alert_colors['not_monitored'], label='Not Monitored')
            ]
            
            ax.legend(
                handles=legend_elements, 
                loc='lower left', 
                bbox_to_anchor=(0.02, 0.02),
                fontsize=9,
                frameon=True,
                fancybox=True,
                shadow=True,
                framealpha=0.95
            )
            
            # Add detailed statistics
            total_warnings = sum(w['count'] for w in warnings_by_country.values())
            countries_with_warnings = len(warnings_by_country)
            monitored_count = len(monitored_countries)
            
            # Count by level
            level_counts = {'red': 0, 'orange': 0, 'yellow': 0, 'green': 0}
            for warning in warnings_by_country.values():
                level = warning['level']
                if level in level_counts:
                    level_counts[level] += 1
            
            stats_text = f"""Official Meteoalarm Data
Monitoring: {monitored_count} countries
Countries with warnings: {countries_with_warnings}
Total active warnings: {total_warnings}

Warning Levels:
  Red (Extreme): {level_counts['red']} countries
  Orange (Severe): {level_counts['orange']} countries  
  Yellow (Moderate): {level_counts['yellow']} countries
  Green (Minor): {level_counts['green']} countries"""
            
            ax.text(
                0.98, 0.98, stats_text,
                transform=ax.transAxes,
                fontsize=9,
                verticalalignment='top',
                horizontalalignment='right',
                bbox=dict(boxstyle='round', facecolor='white', alpha=0.95, pad=0.8),
                family='monospace'
            )
            
            # Add warning details for countries with alerts
            if warnings_by_country:
                details_text = "Active Warnings:\n"
                for country, warning in list(warnings_by_country.items())[:5]:  # Show first 5
                    level_name = warning['level'].title()
                    count = warning['count']
                    types = ', '.join(warning['types'][:2])  # First 2 types
                    details_text += f"• {country.title()}: {level_name} ({count}x) - {types}\n"
                
                if len(warnings_by_country) > 5:
                    details_text += f"... and {len(warnings_by_country) - 5} more countries"
                
                ax.text(
                    0.02, 0.50, details_text,
                    transform=ax.transAxes,
                    fontsize=8,
                    verticalalignment='top',
                    horizontalalignment='left',
                    bbox=dict(boxstyle='round', facecolor='lightyellow', alpha=0.9, pad=0.5)
                )
            
            # Save to BytesIO buffer
            buffer = BytesIO()
            plt.savefig(
                buffer, 
                format='png', 
                dpi=200,
                bbox_inches='tight',
                facecolor='white',
                edgecolor='none',
                pad_inches=0.2
            )
            plt.close(fig)
            buffer.seek(0)
            
            _LOGGER.info("Successfully rendered official Meteoalarm map")
            return buffer.read()
            
        except Exception as e:
            _LOGGER.error("Error rendering Meteoalarm map: %s", e)
            if 'fig' in locals():
                plt.close(fig)
            return self._create_error_image(str(e))

    def _create_error_image(self, error_msg):
        """Create a simple error image using matplotlib."""
        try:
            fig, ax = plt.subplots(figsize=(10, 6))
            fig.patch.set_facecolor('lightgray')
            
            ax.text(
                0.5, 0.5, 
                f'Official Meteoalarm Library Error\n\n{error_msg}\n\nRetrying in {MIN_TIME_BETWEEN_UPDATES}...',
                transform=ax.transAxes,
                fontsize=14,
                ha='center',
                va='center',
                color='red',
                bbox=dict(boxstyle='round', facecolor='white', alpha=0.8)
            )
            
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
        """Update the camera image using official Meteoalarm library."""
        try:
            _LOGGER.info("Updating map with official Meteoalarm library...")
            
            # Get alerts data using official library
            alerts_data = self._get_alerts_data()
            
            # Get monitored countries (normalized)
            monitored_countries = [self._normalize_country_name(c) for c in self._config.get("countries", [])]
            
            # Render the map
            image_data = self._render_warning_map(alerts_data, monitored_countries)
            
            # Store the image
            self._last_image = image_data
            
            # Save to file
            os.makedirs(os.path.dirname(self._image_path), exist_ok=True)
            with open(self._image_path, "wb") as file:
                file.write(self._last_image)
            
            total_warnings = sum(w['count'] for w in alerts_data.values())
            countries_count = len(alerts_data)
            _LOGGER.info("Generated official Meteoalarm map: %d countries with %d total warnings", 
                        countries_count, total_warnings)
            
        except Exception as e:
            _LOGGER.error("Error generating official Meteoalarm map: %s", e)
            self._last_image = self._create_error_image(str(e))

    def camera_image(self, width=None, height=None):
        """Return camera image bytes."""
        if self._last_image is None:
            self.update()
        return self._last_image

    async def async_camera_image(self, width=None, height=height):
        """Return camera image bytes asynchronously.""" 
        return await self.hass.async_add_executor_job(self.camera_image, width, height)

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        """Return a unique ID for this camera."""
        return f"{DOMAIN}_camera"