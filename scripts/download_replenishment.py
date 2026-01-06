import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import os
import time
import glob
import chromedriver_autoinstaller

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from config.settings import DOWNLOAD_DIR, logger
from config.secrets import OCS_EMAIL, OCS_PASSWORD


def _assert_creds():
    if not OCS_EMAIL or not OCS_PASSWORD:
        raise RuntimeError("Missing OCS_EMAIL or OCS_PASSWORD (set environment variables)")


def _clean_previous_xlsx(download_dir: str) -> None:
    """Optional: remove old xlsx files so we can reliably detect the new downloads."""
    for f in glob.glob(os.path.join(download_dir, "*.xlsx")):
        try:
            os.remove(f)
        except OSError:
            pass


def _wait_for_new_xlsx(download_dir: str, timeout: int = 180) -> str:
    """
    Wait for a new .xlsx file to appear and finish downloading (no .crdownload).
    Returns the full path of the latest .xlsx file.
    """
    start = time.time()
    while time.time() - start < timeout:
        time.sleep(1)

        # Ignore temporary Chrome download files
        crdownloads = glob.glob(os.path.join(download_dir, "*.crdownload"))
        xlsx_files = glob.glob(os.path.join(download_dir, "*.xlsx"))

        if xlsx_files and not crdownloads:
            # Pick most recently modified xlsx
            latest = max(xlsx_files, key=os.path.getmtime)
            return latest

    raise TimeoutError(f"Download did not complete within {timeout} seconds")


def run():
    """
    Logs into OCS wholesale portal and downloads:
      1) Order Template (btnExportOrder)
      2) Catalogue (EXPORT CATALOGUE -> START EXPORT)
    Saves both to DOWNLOAD_DIR and returns a dict of file paths.
    """
    _assert_creds()
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    # Remove old xlsx to make "latest file" detection unambiguous
    _clean_previous_xlsx(DOWNLOAD_DIR)

    chromedriver_autoinstaller.install()

    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_experimental_option(
        "prefs",
        {
            "download.default_directory": DOWNLOAD_DIR,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
        },
    )

    driver = webdriver.Chrome(options=chrome_options)
    wait = WebDriverWait(driver, 30)

    try:
        # ---------------- LOGIN ----------------
        signin_url = "https://www.ocswholesale.ca/Admin/Signin"
        driver.get(signin_url)
        logger.info(f"Opened signin page: {signin_url}")

        wait.until(EC.presence_of_element_located((By.ID, "Email"))).send_keys(OCS_EMAIL)
        driver.find_element(By.ID, "password").send_keys(OCS_PASSWORD)
        driver.find_element(By.ID, "btnLogin").click()
        logger.info("Submitted login form")

        # ---------------- SELECT STORE ----------------
        wait.until(EC.presence_of_element_located((By.ID, "hdnShortAddress")))
        store_address = driver.find_element(By.ID, "hdnShortAddress").get_attribute("value")
        logger.info(f"Store address detected: {store_address}")

        select_button = wait.until(EC.element_to_be_clickable((By.LINK_TEXT, "SELECT")))
        select_button.click()
        logger.info("Clicked SELECT")

        store_login_button = wait.until(EC.element_to_be_clickable((By.ID, "btnSubmit")))
        store_login_button.click()
        logger.info("Clicked store portal submit")

        # Wait for active cart button and click
        cart_button = wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "#btnCartWithItemCount.shopping-icn.active")
            )
        )
        driver.execute_script("arguments[0].click();", cart_button)
        logger.info("Opened active cart")

        # ---------------- EXPORT ORDER TEMPLATE ----------------
        export_order_btn = wait.until(EC.presence_of_element_located((By.ID, "btnExportOrder")))
        driver.execute_script("arguments[0].click();", export_order_btn)
        logger.info("Clicked Start Export for Order Template")

        order_template_file = _wait_for_new_xlsx(DOWNLOAD_DIR, timeout=240)
        logger.info(f"Order template downloaded: {order_template_file}")

        # ---------------- EXPORT CATALOGUE ----------------
        export_catalogue_link = wait.until(EC.presence_of_element_located((By.LINK_TEXT, "EXPORT CATALOGUE")))
        driver.execute_script("arguments[0].click();", export_catalogue_link)
        logger.info("Clicked EXPORT CATALOGUE")

        wait.until(EC.visibility_of_element_located((By.ID, "modalExportCataloge")))

        start_catalogue_btn = wait.until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//div[@id='modalExportCataloge']//a[contains(@class,'btn-primary') and contains(text(),'START EXPORT')]",
                )
            )
        )
        driver.execute_script("arguments[0].click();", start_catalogue_btn)
        logger.info("Clicked START EXPORT for Catalogue")

        catalogue_file = _wait_for_new_xlsx(DOWNLOAD_DIR, timeout=300)
        logger.info(f"Catalogue downloaded: {catalogue_file}")

        result = {
            "order_template_path": order_template_file,
            "order_template_name": os.path.basename(order_template_file),
            "catalogue_path": catalogue_file,
            "catalogue_name": os.path.basename(catalogue_file),
        }

        logger.info(f"Download step complete: {result}")
        return result

    finally:
        try:
            driver.quit()
        except Exception:
            pass
