import logging
import os
from datetime import timedelta

from homeassistant.components.camera import Camera
from homeassistant.helpers.entity import Entity
from homeassistant.util import Throttle
from .const import DOMAIN, CAMERA_NAME, IMAGE_PATH, URL

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

_LOGGER = logging.getLogger(__name__)
MIN_TIME_BETWEEN_UPDATES = timedelta(minutes=10)

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    async_add_entities([MeteoalarmCamera()], True)

class MeteoalarmCamera(Camera):
    def __init__(self):
        super().__init__()
        self._name = CAMERA_NAME
        self._image_path = IMAGE_PATH
        self._last_image = None

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    def update(self):
        try:
            options = Options()
            options.add_argument("--headless")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--window-size=1280,720")
            options.add_argument("--disable-gpu")

            driver = webdriver.Chrome(ChromeDriverManager().install(), options=options)
            driver.get(URL)
            driver.implicitly_wait(10)
            driver.save_screenshot(self._image_path)
            driver.quit()

            with open(self._image_path, "rb") as file:
                self._last_image = file.read()
        except Exception as e:
            _LOGGER.error("Error generating map screenshot: %s", e)

    def camera_image(self):
        if self._last_image is None:
            self.update()
        return self._last_image

    @property
    def name(self):
        return self._name
