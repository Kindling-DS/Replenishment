#!/usr/bin/env python3
"""
scripts/download_replenishment.py

OCS automation (Ubuntu 24.04 LTS compatible):
- Launches Google Chrome Stable headless
- Logs into https://www.ocswholesale.ca/Admin/Signin
- Dismisses notifications ("DISMISS ALL") if present
- Ensures you are inside portal (handles store selection page OR already-in-portal)
- Opens cart
- Downloads TWO Excel files:
    1) Order Template (btnExportOrder)
    2) Catalogue (EXPORT CATALOGUE -> START EXPORT)
- Saves both into config.settings.DOWNLOAD_DIR
- Logs to config.settings.logger

Reliability fixes for Ubuntu 24.04 headless:
- Explicit Chrome binary_location: /usr/bin/google-chrome-stable
- Explicit chromedriver: /usr/bin/chromedriver
- Unique, writable profile dir per run (prevents profile lock/corruption)
- Avoid fixed debugging port collisions with --remote-debugging-pipe
- ChromeDriver logging to /tmp/chromedriver.log
"""

import os
import sys
import time
import glob
import tempfile
import shutil
import atexit
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException


# ---- Ensure repo root is on sys.path (prevents "No module named config") ----
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config.settings import DOWNLOAD_DIR, logger  # noqa: E402
from config.secrets import OCS_EMAIL, OCS_PASSWORD  # noqa: E402


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


def _dump_debug(driver: webdriver.Chrome, prefix: str) -> None:
    """
    Writes debug HTML + screenshot into DOWNLOAD_DIR.
    """
    try:
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        html_path = os.path.join(DOWNLOAD_DIR, f"{prefix}.html")
        png_path = os.path.join(DOWNLOAD_DIR, f"{prefix}.png")

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(driver.page_source)

        driver.save_screenshot(png_path)
        logger.error(f"Saved debug HTML: {html_path}")
        logger.error(f"Saved debug screenshot: {png_path}")
    except Exception as e:
        logger.error(f"Failed to write debug artifacts: {e}")


def _dismiss_notifications_if_present(driver: webdriver.Chrome, wait_seconds: int = 6) -> bool:
    """
    Clicks notifications 'DISMISS ALL' if present. Non-fatal if not present.
    """
    short_wait = WebDriverWait(driver, wait_seconds)
    locators = [
        (By.XPATH, "//button[normalize-space()='DISMISS ALL']"),
        (By.XPATH, "//a[normalize-space()='DISMISS ALL']"),
        # case-insensitive fallback
        (
            By.XPATH,
            "//button[contains(translate(., 'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'DISMISS ALL')]",
        ),
        (
            By.XPATH,
            "//a[contains(translate(., 'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'DISMISS ALL')]",
        ),
    ]

    for by, sel in locators:
        try:
            btn = short_wait.until(EC.element_to_be_clickable((by, sel)))
            driver.execute_script("arguments[0].click();", btn)
            return True
        except TimeoutException:
            continue
        except Exception:
            continue

    return False


def _ensure_in_portal(driver: webdriver.Chrome, wait: WebDriverWait) -> None:
    """
    After login, you can land on either:
      A) Store selection page: hdnShortAddress + SELECT + btnSubmit
      B) Already inside portal: Place Order link exists (/sales/StartOrder)

    This function detects which state you're in and gets you into the portal.
    """
    portal_marker = (
        By.XPATH,
        "//a[contains(@href,'/sales/StartOrder') and normalize-space()='Place Order']",
    )
    store_marker = (By.ID, "hdnShortAddress")

    try:
        WebDriverWait(driver, 25).until(
            lambda d: d.find_elements(*portal_marker) or d.find_elements(*store_marker)
        )
    except TimeoutException:
        raise TimeoutException(
            f"Neither portal marker nor store-selection marker appeared. URL={driver.current_url} TITLE={driver.title}"
        )

    # If already in portal, nothing to do
    if driver.find_elements(*portal_marker):
        return

    # Otherwise, handle store selection flow
    wait.until(EC.presence_of_element_located(store_marker))
    wait.until(EC.element_to_be_clickable((By.LINK_TEXT, "SELECT"))).click()
    wait.until(EC.element_to_be_clickable((By.ID, "btnSubmit"))).click()

    # Confirm portal loaded
    wait.until(EC.presence_of_element_located(portal_marker))


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

    # Unique profile dir per run
    profile_parent = "/var/tmp" if os.path.isdir("/var/tmp") else "/tmp"
    profile_dir = tempfile.mkdtemp(prefix="ocs-chrome-profile-", dir=profile_parent)
    opts.add_argument(f"--user-data-dir={profile_dir}")
    opts.add_argument("--remote-debugging-pipe")

    # Optional hardening
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

    service = Service(CHROMEDRIVER_BIN, log_output=CHROMEDRIVER_LOG)
    driver = webdriver.Chrome(service=service, options=opts)

    # Cleanup profile dir on exit
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
    wait = WebDriverWait(driver, 45)

    try:
        # ---------------- LOGIN ----------------
        driver.get(OCS_SIGNIN_URL)
        logger.info(f"Opened signin page: {OCS_SIGNIN_URL}")

        wait.until(EC.presence_of_element_located((By.ID, "Email"))).send_keys(OCS_EMAIL)
        driver.find_element(By.ID, "password").send_keys(OCS_PASSWORD)
        driver.find_element(By.ID, "btnLogin").click()
        logger.info("Submitted login form")

        # Give page a moment to render UI
        try:
            WebDriverWait(driver, 10).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
        except Exception:
            pass

        # ---------------- NOTIFICATIONS ----------------
        if _dismiss_notifications_if_present(driver, wait_seconds=8):
            logger.info("Notifications dismissed (DISMISS ALL clicked)")
        else:
            logger.info("No notifications popup detected (or not clickable)")

        # ---------------- ENSURE PORTAL ----------------
        try:
            _ensure_in_portal(driver, wait)
            logger.info("In portal")
        except Exception as e:
            logger.error(f"Failed to reach portal: {e}")
            _dump_debug(driver, "debug_after_login_or_portal")
            raise

        # ---------------- OPEN CART ----------------
        cart_selectors = [
            (By.CSS_SELECTOR, "#btnCartWithItemCount.shopping-icn.active"),
            (By.CSS_SELECTOR, "#btnCartWithItemCount"),
        ]

        cart_btn = None
        for sel in cart_selectors:
            try:
                cart_btn = WebDriverWait(driver, 15).until(EC.presence_of_element_located(sel))
                break
            except TimeoutException:
                continue

        if not cart_btn:
            _dump_debug(driver, "debug_cart_not_found")
            raise TimeoutException("Cart button not found (active or generic)")

        driver.execute_script("arguments[0].click();", cart_btn)
        logger.info("Cart opened")

        # ---------------- EXPORT ORDER TEMPLATE ----------------
        before = _latest_mtime(os.path.join(DOWNLOAD_DIR, "*.xlsx"))

        export_order_btn = wait.until(EC.presence_of_element_located((By.ID, "btnExportOrder")))
        driver.execute_script("arguments[0].click();", export_order_btn)
        logger.info("Clicked Export Order Template")

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
                    "//div[@id='modalExportCataloge']//a[contains(@class,'btn-primary') and contains(translate(.,'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'START EXPORT')]",
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
        # Debug chromedriver startup/logins:
        #   tail -200 /tmp/chromedriver.log


if __name__ == "__main__":
    out = run()
    print(out)
