import logging
import os
import requests
import xml.etree.ElementTree as ET
from datetime import timedelta, datetime
from PIL import Image, ImageDraw, ImageFont
import io

from homeassistant.components.camera import Camera
from homeassistant.util import Throttle
from .const import DOMAIN, CAMERA_NAME, IMAGE_PATH, RSS_FEED

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
        
        # European country coordinates (approximate center positions)
        self.country_coords = {
            'italy': (450, 380),
            'spain': (250, 400),
            'france': (320, 320),
            'germany': (400, 280),
            'netherlands': (380, 240),
            'belgium': (360, 260),
            'portugal': (200, 400),
            'switzerland': (380, 340),
            'austria': (430, 320),
            'poland': (480, 260),
            'czech republic': (440, 300),
            'slovakia': (470, 300),
            'hungary': (470, 340),
            'slovenia': (430, 360),
            'croatia': (440, 380),
            'bosnia and herzegovina': (450, 400),
            'serbia': (480, 400),
            'montenegro': (460, 420),
            'albania': (460, 440),
            'greece': (500, 480),
            'bulgaria': (520, 420),
            'romania': (520, 360),
            'moldova': (540, 340),
            'ukraine': (580, 320),
            'belarus': (540, 260),
            'lithuania': (520, 220),
            'latvia': (520, 200),
            'estonia': (520, 180),
            'finland': (520, 120),
            'sweden': (450, 140),
            'norway': (420, 100),
            'denmark': (420, 220),
            'iceland': (180, 80),
            'united kingdom': (280, 200),
            'ireland': (240, 200),
            'luxembourg': (370, 280),
            'liechtenstein': (390, 340),
            'monaco': (340, 380),
            'san marino': (430, 380),
            'vatican city': (430, 390),
            'andorra': (300, 380),
            'malta': (430, 480),
            'cyprus': (600, 480),
            'turkey': (650, 450),
            'north macedonia': (480, 440)
        }
        
        # Alert level colors
        self.alert_colors = {
            'red': '#FF0000',
            'orange': '#FF8C00',
            'yellow': '#FFD700',
            'green': '#32CD32',
            'unknown': '#808080'
        }

    def _extract_country_from_title(self, title):
        """Extract country name from the RSS item title."""
        if ':' in title:
            return title.split(':')[0].strip().lower()
        elif ' - ' in title:
            return title.split(' - ')[0].strip().lower()
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

    def _get_alerts_data(self):
        """Fetch and parse alerts from RSS feed."""
        try:
            r = requests.get(RSS_FEED, timeout=15)
            r.raise_for_status()
            
            root = ET.fromstring(r.content)
            alerts_by_country = {}
            
            for item in root.findall('.//item'):
                title_elem = item.find('title')
                description_elem = item.find('description')
                
                if title_elem is None or description_elem is None:
                    continue
                    
                title = title_elem.text or ""
                description = description_elem.text or ""
                
                country = self._extract_country_from_title(title)
                if country and country in self.country_coords:
                    level = self._parse_awareness_level(title, description)
                    
                    # Keep the highest alert level per country
                    if country not in alerts_by_country:
                        alerts_by_country[country] = {'level': level, 'count': 1, 'titles': [title]}
                    else:
                        alerts_by_country[country]['count'] += 1
                        alerts_by_country[country]['titles'].append(title)
                        
                        # Update to highest priority level
                        current_level = alerts_by_country[country]['level']
                        level_priority = {'red': 4, 'orange': 3, 'yellow': 2, 'green': 1, 'unknown': 0}
                        if level_priority.get(level, 0) > level_priority.get(current_level, 0):
                            alerts_by_country[country]['level'] = level
            
            return alerts_by_country
        except Exception as e:
            _LOGGER.error("Error fetching alerts data: %s", e)
            return {}

    def _create_map_image(self, alerts_data):
        """Create a map image with weather alerts."""
        # Create base map image
        width, height = 800, 600
        img = Image.new('RGB', (width, height), color='#E6F3FF')  # Light blue background
        draw = ImageDraw.Draw(img)
        
        # Draw title
        try:
            title_font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 24)
        except:
            title_font = ImageFont.load_default()
            
        title = "Meteoalarm Weather Alerts - Europe"
        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        title_width = title_bbox[2] - title_bbox[0]
        draw.text(((width - title_width) // 2, 10), title, fill='black', font=title_font)
        
        # Draw Europe outline (simplified)
        # This is a very basic outline - you could enhance this with actual map data
        europe_outline = [
            (150, 100), (200, 80), (300, 70), (400, 60), (500, 70), (600, 90),
            (650, 120), (680, 200), (700, 300), (720, 400), (700, 450),
            (650, 480), (500, 500), (400, 520), (300, 510), (200, 480),
            (150, 450), (120, 400), (100, 350), (110, 250), (130, 150)
        ]
        draw.polygon(europe_outline, outline='gray', fill='#F0F8FF', width=2)
        
        # Draw country alerts
        try:
            country_font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", 12)
        except:
            country_font = ImageFont.load_default()
        
        for country, alert_info in alerts_data.items():
            if country in self.country_coords:
                x, y = self.country_coords[country]
                level = alert_info['level']
                count = alert_info['count']
                color = self.alert_colors.get(level, '#808080')
                
                # Draw alert circle
                radius = min(15 + (count * 3), 25)  # Size based on number of alerts
                draw.ellipse([x-radius, y-radius, x+radius, y+radius], 
                           fill=color, outline='black', width=2)
                
                # Draw country code in circle
                country_code = country[:3].upper()
                text_bbox = draw.textbbox((0, 0), country_code, font=country_font)
                text_width = text_bbox[2] - text_bbox[0]
                text_height = text_bbox[3] - text_bbox[1]
                draw.text((x - text_width//2, y - text_height//2), 
                         country_code, fill='white', font=country_font)
        
        # Draw legend
        legend_y = height - 120
        draw.text((20, legend_y - 20), "Alert Levels:", fill='black', font=country_font)
        
        legend_items = [
            ('Red - Extreme', '#FF0000'),
            ('Orange - Severe', '#FF8C00'),
            ('Yellow - Moderate', '#FFD700'),
            ('Green - Minor', '#32CD32')
        ]
        
        for i, (label, color) in enumerate(legend_items):
            y_pos = legend_y + (i * 20)
            draw.ellipse([20, y_pos, 35, y_pos + 15], fill=color, outline='black')
            draw.text((45, y_pos), label, fill='black', font=country_font)
        
        # Add timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
        draw.text((width - 150, height - 30), f"Updated: {timestamp}", fill='gray', font=country_font)
        
        # Add monitored countries info
        monitored = self._config.get("countries", [])
        if monitored:
            monitored_text = f"Monitoring: {', '.join(monitored[:5])}"  # Show first 5
            if len(monitored) > 5:
                monitored_text += f" (+{len(monitored)-5} more)"
            draw.text((20, height - 50), monitored_text, fill='blue', font=country_font)
        
        return img

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    def update(self):
        """Update the camera image by generating a custom map."""
        try:
            # Get alerts data from RSS feed
            alerts_data = self._get_alerts_data()
            
            # Create map image
            img = self._create_map_image(alerts_data)
            
            # Save image to bytes
            img_bytes = io.BytesIO()
            img.save(img_bytes, format='PNG')
            self._last_image = img_bytes.getvalue()
            
            # Also save to file
            os.makedirs(os.path.dirname(self._image_path), exist_ok=True)
            with open(self._image_path, "wb") as file:
                file.write(self._last_image)
            
            alert_count = len(alerts_data)
            _LOGGER.info("Successfully generated custom map with %d country alerts", alert_count)
            
        except Exception as e:
            _LOGGER.error("Error generating custom map: %s", e)
            self._create_error_image(str(e))

    def _create_error_image(self, error_msg):
        """Create an error image when map generation fails."""
        try:
            img = Image.new('RGB', (800, 600), color='lightgray')
            draw = ImageDraw.Draw(img)
            
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", 20)
            except:
                font = ImageFont.load_default()
            
            text = f"Meteoalarm Map\nError: {error_msg}"
            text_bbox = draw.textbbox((0, 0), text, font=font)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
            
            x = (800 - text_width) // 2
            y = (600 - text_height) // 2
            
            draw.text((x, y), text, fill='red', font=font)
            
            img_bytes = io.BytesIO()
            img.save(img_bytes, format='PNG')
            self._last_image = img_bytes.getvalue()
            
            os.makedirs(os.path.dirname(self._image_path), exist_ok=True)
            with open(self._image_path, "wb") as file:
                file.write(self._last_image)
                
        except Exception as e:
            _LOGGER.error("Could not create error image: %s", e)

    def camera_image(self):
        if self._last_image is None:
            self.update()
        return self._last_image

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        """Return a unique ID for this camera."""
        return f"{DOMAIN}_camera"