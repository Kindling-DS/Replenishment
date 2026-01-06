from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import chromedriver_autoinstaller
import os
import time

from config.settings import DOWNLOAD_DIR, logger
from config.secrets import OCS_EMAIL, OCS_PASSWORD

def run():
    chromedriver_autoinstaller.install()

    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    prefs = {"download.default_directory": DOWNLOAD_DIR}
    options.add_experimental_option("prefs", prefs)

    driver = webdriver.Chrome(options=options)
    logger.info("Chrome started")

    driver.get("https://www.ocswholesale.ca/Admin/Signin")

    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.ID, "Email"))
    ).send_keys(OCS_EMAIL)

    driver.find_element(By.ID, "password").send_keys(OCS_PASSWORD)
    driver.find_element(By.ID, "btnLogin").click()

    time.sleep(15)
    driver.quit()
    logger.info("Download step complete")
