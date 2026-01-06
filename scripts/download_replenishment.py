#!/usr/bin/env python3
"""
scripts/download_replenishment.py

OCS automation (Ubuntu 24.04.3 LTS compatible):
- Launches Google Chrome Stable headless
- Logs into https://www.ocswholesale.ca/Admin/Signin
- Selects store portal
- Opens cart
- Downloads TWO Excel files:
    1) Order Template (btnExportOrder)
    2) Catalogue (EXPORT CATALOGUE -> START EXPORT)
- Saves both into config.settings.DOWNLOAD_DIR
- Logs to config.settings.logger

Reliability fixes for Ubuntu 24.04 headless:
- Explicit Chrome binary_location: /usr/bin/google-chrome-stable
- Unique, writable profile dir per run (prevents profile lock / corruption across runs)
- Avoid fixed remote debugging port collisions using --remote-debugging-pipe
- ChromeDriver logging to /tmp/chromedriver.log for root-cause visibility
- Uses system chromedriver explicitly: /usr/bin/chromedriver (keep if you want)
"""

import os
import sys
import time
import glob
import tempfile
import shutil
import atexit
from pathlib import Path

# ---- Ensure repo root is on sys.path (prevents "No module named config") ----
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from config.settings import DOWNLOAD_DIR, logger
from config.secrets import OCS_EMAIL, OCS_PASSWORD


OCS_SIGNIN_URL = "https://www.ocswholesale.ca/Admin/Signin"
CHROME_BIN = "/usr/bin/google-chrome-stable"
CHROMEDRIVER_BIN = "/usr/bin/chromedriver"
CHROMEDRIVER_LOG = "/tmp/chromedriver.log"


def _assert_creds() -> None:
    if not OCS_EMAIL or not OCS_PASSWORD:
        raise RuntimeError(
            "Missing OCS credentials. Set env vars OCS_EMAIL and OCS_PASSWORD "
            "(and ensure config/secrets.py loads them)."
        )


def _latest_mtime(path_glob: str) -> float:
    """Return latest mtime among matches, or 0 if none exist."""
    paths = glob.glob(path_glob)
    if not paths:
        return 0.0
    return max(os.path.getmtime(p) for p in paths)


def _wait_for_new_xlsx(download_dir: str, since_mtime: float, timeout: int = 240) -> str:
    """
    Wait until a NEW .xlsx appears in download_dir after since_mtime and completes
    (no *.crdownload exists). Returns full path to newest completed xlsx.
    """
    start = time.time()
    while time.time() - start < timeout:
        time.sleep(1)

        # If Chrome is still writing, skip
        if glob.glob(os.path.join(download_dir, "*.crdownload")):
            continue

        xlsx_files = glob.glob(os.path.join(download_dir, "*.xlsx"))
        if not xlsx_files:
            continue

        newest = max(xlsx_files, key=os.path.getmtime)
        if os.path.getmtime(newest) > since_mtime:
            return newest

    raise TimeoutError(f"Download did not complete within {timeout} seconds")


def _make_driver(download_dir: str) -> webdriver.Chrome:
    os.makedirs(download_dir, exist_ok=True)

    opts = Options()
    opts.binary_location = CHROME_BIN

    # Headless + stability flags for Ubuntu 24.04
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")

    # Critical reliability fixes:
    # 1) Unique profile dir per run (prevents "session not created" from profile locks)
    # 2) Use debugging pipe (prevents fixed-port collisions like 9222)
    # Use /var/tmp if /tmp is mounted noexec; /tmp is usually fine.
    profile_parent = "/var/tmp" if os.path.isdir("/var/tmp") else "/tmp"
    profile_dir = tempfile.mkdtemp(prefix="ocs-chrome-profile-", dir=profile_parent)
    opts.add_argument(f"--user-data-dir={profile_dir}")
    opts.add_argument("--remote-debugging-pipe")

    # Optional hardening (often helps on minimal servers)
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--disable-background-networking")
    opts.add_argument("--disable-breakpad")

    # Download behavior
    opts.add_experimental_option(
        "prefs",
        {
            "download.default_directory": download_dir,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
        },
    )

    # Log chromedriver output for diagnosing startup failures
    service = Service(CHROMEDRIVER_BIN, log_output=CHROMEDRIVER_LOG)

    driver = webdriver.Chrome(service=service, options=opts)

    # Ensure profile is cleaned up at process exit
    atexit.register(lambda: shutil.rmtree(profile_dir, ignore_errors=True))

    return driver


def run() -> dict:
    """
    Returns:
      {
        "order_template_path": "...",
        "order_template_name": "...",
        "catalogue_path": "...",
        "catalogue_name": "..."
      }
    """
    _assert_creds()
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    driver = _make_driver(DOWNLOAD_DIR)
    wait = WebDriverWait(driver, 30)

    try:
        # ---------------- LOGIN ----------------
        driver.get(OCS_SIGNIN_URL)
        logger.info(f"Opened signin page: {OCS_SIGNIN_URL}")

        wait.until(EC.presence_of_element_located((By.ID, "Email"))).send_keys(OCS_EMAIL)
        driver.find_element(By.ID, "password").send_keys(OCS_PASSWORD)
        driver.find_element(By.ID, "btnLogin").click()
        logger.info("Submitted login form")

        # ---------------- SELECT STORE ----------------
        wait.until(EC.presence_of_element_located((By.ID, "hdnShortAddress")))
        store_address = driver.find_element(By.ID, "hdnShortAddress").get_attribute("value")
        logger.info(f"Store address detected: {store_address}")

        wait.until(EC.element_to_be_clickable((By.LINK_TEXT, "SELECT"))).click()
        logger.info("Clicked SELECT")

        wait.until(EC.element_to_be_clickable((By.ID, "btnSubmit"))).click()
        logger.info("Clicked store portal submit")

        # Open cart
        cart_button = wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "#btnCartWithItemCount.shopping-icn.active")
            )
        )
        driver.execute_script("arguments[0].click();", cart_button)
        logger.info("Opened active cart")

        # ---------------- EXPORT ORDER TEMPLATE ----------------
        before = _latest_mtime(os.path.join(DOWNLOAD_DIR, "*.xlsx"))

        export_order_btn = wait.until(EC.presence_of_element_located((By.ID, "btnExportOrder")))
        driver.execute_script("arguments[0].click();", export_order_btn)
        logger.info("Clicked Start Export for Order Template")

        order_template_file = _wait_for_new_xlsx(DOWNLOAD_DIR, since_mtime=before, timeout=300)
        logger.info(f"Order template downloaded: {order_template_file}")

        # ---------------- EXPORT CATALOGUE ----------------
        before = _latest_mtime(os.path.join(DOWNLOAD_DIR, "*.xlsx"))

        export_catalogue_link = wait.until(
            EC.presence_of_element_located((By.LINK_TEXT, "EXPORT CATALOGUE"))
        )
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

        catalogue_file = _wait_for_new_xlsx(DOWNLOAD_DIR, since_mtime=before, timeout=420)
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
        # If you want to debug failures, check:
        #   tail -200 /tmp/chromedriver.log


if __name__ == "__main__":
    out = run()
    print(out)
