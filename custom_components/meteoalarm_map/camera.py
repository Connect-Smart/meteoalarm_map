import logging
import os
from datetime import timedelta

from homeassistant.components.camera import Camera
from homeassistant.helpers.entity import Entity
from homeassistant.util import Throttle
from .const import DOMAIN, CAMERA_NAME, IMAGE_PATH, URL

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import time

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

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    def update(self):
        try:
            options = Options()
            options.add_argument("--headless")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--window-size=1280,720")
            options.add_argument("--disable-gpu")
            options.add_argument("--disable-extensions")
            options.add_argument("--disable-plugins")
            options.add_argument("--disable-images")
            options.add_argument("--remote-debugging-port=9222")

            # Try different approaches to get ChromeDriver
            driver = None
            
            # First try: Use system ChromeDriver if available
            try:
                service = Service()  # Use system chromedriver
                driver = webdriver.Chrome(service=service, options=options)
                _LOGGER.info("Using system ChromeDriver")
            except Exception as e1:
                _LOGGER.debug("System ChromeDriver failed: %s", e1)
                
                # Second try: Use webdriver-manager
                try:
                    service = Service(ChromeDriverManager().install())
                    driver = webdriver.Chrome(service=service, options=options)
                    _LOGGER.info("Using ChromeDriver from webdriver-manager")
                except Exception as e2:
                    _LOGGER.debug("webdriver-manager failed: %s", e2)
                    
                    # Third try: Manual path (common locations)
                    chromedriver_paths = [
                        "/usr/bin/chromedriver",
                        "/usr/local/bin/chromedriver",
                        "/opt/chromedriver",
                        "/home/homeassistant/.local/bin/chromedriver"
                    ]
                    
                    for path in chromedriver_paths:
                        if os.path.exists(path):
                            try:
                                service = Service(path)
                                driver = webdriver.Chrome(service=service, options=options)
                                _LOGGER.info("Using ChromeDriver from: %s", path)
                                break
                            except Exception as e3:
                                _LOGGER.debug("ChromeDriver at %s failed: %s", path, e3)
                                continue
            
            if driver is None:
                raise Exception("Could not initialize ChromeDriver. Please install ChromeDriver manually.")

            driver.get(URL)
            driver.implicitly_wait(15)
            
            # Wait a bit more for the map to load
            import time
            time.sleep(5)
            
            # Ensure the directory exists
            os.makedirs(os.path.dirname(self._image_path), exist_ok=True)
            
            driver.save_screenshot(self._image_path)
            driver.quit()

            with open(self._image_path, "rb") as file:
                self._last_image = file.read()
                
            _LOGGER.info("Successfully captured Meteoalarm map screenshot")
        except Exception as e:
            _LOGGER.error("Error generating map screenshot: %s", e)
            if 'driver' in locals() and driver:
                try:
                    driver.quit()
                except:
                    pass

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