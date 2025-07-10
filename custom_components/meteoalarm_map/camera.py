import logging
import os
from datetime import timedelta, datetime
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
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
        
        # Initialize MeteoAlarm client
        self._meteoalarm = MeteoAlarm()
        
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
        
        # Country positions for visualization (approximate center coordinates)
        self.country_positions = {
            'italy': (13.0, 42.0),
            'spain': (-3.0, 40.0),
            'france': (2.0, 46.0),
            'germany': (10.0, 51.0),
            'united kingdom': (-2.0, 54.0),
            'poland': (20.0, 52.0),
            'netherlands': (5.0, 52.0),
            'belgium': (4.5, 50.5),
            'portugal': (-8.0, 39.5),
            'switzerland': (8.0, 47.0),
            'austria': (14.0, 47.5),
            'norway': (9.0, 61.0),
            'sweden': (15.0, 62.0),
            'finland': (26.0, 64.0),
            'denmark': (10.0, 56.0),
            'czechia': (15.5, 49.5),
            'slovakia': (19.5, 48.5),
            'hungary': (20.0, 47.0),
            'romania': (25.0, 46.0),
            'bulgaria': (25.0, 43.0),
            'greece': (22.0, 39.0),
            'croatia': (16.0, 45.0),
            'slovenia': (15.0, 46.0),
            'serbia': (21.0, 44.0),
            'bosnia and herzegovina': (18.0, 44.0),
            'albania': (20.0, 41.0),
            'montenegro': (19.0, 42.5),
            'ireland': (-8.0, 53.0),
            'estonia': (25.0, 59.0),
            'latvia': (25.0, 57.0),
            'lithuania': (24.0, 55.0),
            'luxembourg': (6.0, 49.5),
            'malta': (14.5, 35.8),
            'cyprus': (33.0, 35.0),
            'iceland': (-18.0, 65.0)
        }
        
        # Country name mappings
        self.country_mappings = {
            'gb': 'united kingdom',
            'uk': 'united kingdom',
            'cz': 'czechia',
            'czech republic': 'czechia',
            'bosnia': 'bosnia and herzegovina',
            'north macedonia': 'north macedonia',
            'macedonia': 'north macedonia',
            'the netherlands': 'netherlands',
            'holland': 'netherlands'
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
            
            _LOGGER.info("Fetching alerts for %d monitored countries using Meteoalarm library", len(monitored_countries))
            
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
                                'types': [getattr(alert, 'event', 'Unknown') for alert in country_alerts[:3]]
                            }
                            
                            _LOGGER.info("Found %d alerts for %s (max level: %d)", 
                                       len(country_alerts), country, max_level)
                    
                except Exception as e:
                    _LOGGER.warning("Failed to get alerts for %s: %s", country, e)
                    continue
            
            _LOGGER.info("Successfully fetched alerts for %d countries using official library", len(alerts_by_country))
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

    def _render_simple_map(self, warnings_by_country, monitored_countries):
        """Render a simple map visualization using matplotlib."""
        try:
            # Set up the plot
            fig, ax = plt.subplots(figsize=(14, 10))
            fig.patch.set_facecolor('white')
            
            # Set Europe bounds (longitude, latitude)
            europe_bounds = [-15, 35, 35, 75]  # [west, south, east, north]
            ax.set_xlim(europe_bounds[0], europe_bounds[2])
            ax.set_ylim(europe_bounds[1], europe_bounds[3])
            
            # Draw a simple Europe outline
            europe_coastline_x = [-10, -5, 0, 5, 10, 15, 20, 25, 30, 30, 25, 20, 15, 10, 5, 0, -5, -10, -10]
            europe_coastline_y = [36, 40, 43, 45, 50, 55, 60, 65, 70, 68, 65, 60, 55, 50, 45, 43, 40, 36, 36]
            ax.plot(europe_coastline_x, europe_coastline_y, 'k-', linewidth=2, alpha=0.3)
            ax.fill(europe_coastline_x, europe_coastline_y, color='#F0F8FF', alpha=0.3)
            
            # Plot countries as circles
            for country in monitored_countries:
                normalized_country = self._normalize_country_name(country)
                
                if normalized_country in self.country_positions:
                    lon, lat = self.country_positions[normalized_country]
                    
                    # Determine color and size based on alert level
                    if normalized_country in warnings_by_country:
                        warning = warnings_by_country[normalized_country]
                        color = self.alert_colors[warning['level']]
                        size = 300 + (warning['count'] * 100)  # Larger for more alerts
                        alpha = 0.8
                        
                        # Add alert count text
                        count_text = f"{warning['count']}"
                        ax.text(lon, lat, count_text, ha='center', va='center', 
                               fontsize=10, fontweight='bold', color='white')
                    else:
                        color = self.alert_colors['no_alert']
                        size = 200
                        alpha = 0.6
                    
                    # Plot country circle
                    ax.scatter(lon, lat, c=color, s=size, alpha=alpha, edgecolors='black', linewidth=2)
                    
                    # Add country label
                    ax.text(lon, lat-1.5, country.title(), ha='center', va='top', 
                           fontsize=8, fontweight='bold')
            
            # Add title with vacation period info
            vacation_start = self._config.get("vacation_start", "Unknown")
            vacation_end = self._config.get("vacation_end", "Unknown")
            
            title = f'Official Meteoalarm Europe - Weather Warnings\n'
            title += f'Vacation Period: {vacation_start} to {vacation_end}\n'
            title += f'Updated: {datetime.now().strftime("%d/%m/%Y %H:%M UTC")}'
            
            ax.set_title(title, fontsize=16, fontweight='bold', pad=20)
            
            # Remove axes ticks and labels
            ax.set_xticks([])
            ax.set_yticks([])
            
            # Create legend
            legend_elements = [
                plt.scatter([], [], c=self.alert_colors['red'], s=200, label='Red (Level 4) - Extreme'),
                plt.scatter([], [], c=self.alert_colors['orange'], s=200, label='Orange (Level 3) - Severe'),
                plt.scatter([], [], c=self.alert_colors['yellow'], s=200, label='Yellow (Level 2) - Moderate'),
                plt.scatter([], [], c=self.alert_colors['green'], s=200, label='Green (Level 1) - Minor'),
                plt.scatter([], [], c=self.alert_colors['no_alert'], s=200, label='No Warnings')
            ]
            
            ax.legend(handles=legend_elements, loc='lower left', bbox_to_anchor=(0.02, 0.02),
                     fontsize=10, frameon=True, fancybox=True, shadow=True, framealpha=0.9)
            
            # Add statistics box
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
            
            ax.text(0.98, 0.98, stats_text, transform=ax.transAxes, fontsize=10,
                   verticalalignment='top', horizontalalignment='right',
                   bbox=dict(boxstyle='round', facecolor='white', alpha=0.9, pad=0.8),
                   family='monospace')
            
            # Add warning details
            if warnings_by_country:
                details_text = "Active Warnings:\n"
                for country, warning in list(warnings_by_country.items())[:4]:
                    level_name = warning['level'].title()
                    count = warning['count']
                    types = ', '.join(warning['types'][:2])
                    details_text += f"• {country.title()}: {level_name} ({count}x)\n  {types}\n"
                
                if len(warnings_by_country) > 4:
                    details_text += f"... +{len(warnings_by_country) - 4} more countries"
                
                ax.text(0.02, 0.5, details_text, transform=ax.transAxes, fontsize=9,
                       verticalalignment='top', horizontalalignment='left',
                       bbox=dict(boxstyle='round', facecolor='lightyellow', alpha=0.9, pad=0.5))
            
            # Save to buffer
            buffer = BytesIO()
            plt.savefig(buffer, format='png', dpi=150, bbox_inches='tight',
                       facecolor='white', edgecolor='none', pad_inches=0.2)
            plt.close(fig)
            buffer.seek(0)
            
            _LOGGER.info("Successfully rendered simple Meteoalarm map")
            return buffer.read()
            
        except Exception as e:
            _LOGGER.error("Error rendering simple map: %s", e)
            if 'fig' in locals():
                plt.close(fig)
            return self._create_error_image(str(e))

    def _create_error_image(self, error_msg):
        """Create a simple error image."""
        try:
            fig, ax = plt.subplots(figsize=(10, 6))
            fig.patch.set_facecolor('lightgray')
            
            ax.text(0.5, 0.5, 
                   f'Official Meteoalarm Library Error\n\n{error_msg}\n\nRetrying in {MIN_TIME_BETWEEN_UPDATES}...',
                   transform=ax.transAxes, fontsize=14, ha='center', va='center', color='red',
                   bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))
            
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
            _LOGGER.info("Updating map with official Meteoalarm library (simple version)...")
            
            # Get alerts data using official library
            alerts_data = self._get_alerts_data()
            
            # Get monitored countries (normalized)
            monitored_countries = [self._normalize_country_name(c) for c in self._config.get("countries", [])]
            
            # Render the simple map
            image_data = self._render_simple_map(alerts_data, monitored_countries)
            
            # Store the image
            self._last_image = image_data
            
            # Save to file
            os.makedirs(os.path.dirname(self._image_path), exist_ok=True)
            with open(self._image_path, "wb") as file:
                file.write(self._last_image)
            
            total_warnings = sum(w['count'] for w in alerts_data.values())
            countries_count = len(alerts_data)
            _LOGGER.info("Generated simple Meteoalarm map: %d countries with %d total warnings", 
                        countries_count, total_warnings)
            
        except Exception as e:
            _LOGGER.error("Error generating simple Meteoalarm map: %s", e)
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