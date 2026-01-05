import os
import time
import chromedriver_autoinstaller

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ======================================================
# SECURITY: ENVIRONMENT VARIABLES
# ======================================================
EMAIL = os.getenv("OCS_EMAIL")
PASSWORD = os.getenv("OCS_PASSWORD")

if not EMAIL or not PASSWORD:
    raise RuntimeError("OCS_EMAIL or OCS_PASSWORD environment variables not set")

# ======================================================
# PATHS
# ======================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "..", "data", "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ======================================================
# CHROME SETUP
# ======================================================
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
        "directory_upgrade": True,
        "safebrowsing.enabled": True,
    },
)

driver = webdriver.Chrome(options=chrome_options)
wait = WebDriverWait(driver, 20)

# ======================================================
# LOGIN
# ======================================================
driver.get("https://www.ocswholesale.ca/Admin/Signin")

wait.until(EC.presence_of_element_located((By.ID, "Email"))).send_keys(EMAIL)
driver.find_element(By.ID, "password").send_keys(PASSWORD)
driver.find_element(By.ID, "btnLogin").click()

# ======================================================
# STORE SELECTION
# ======================================================
wait.until(EC.presence_of_element_located((By.ID, "hdnShortAddress")))

select_button = wait.until(EC.element_to_be_clickable((By.LINK_TEXT, "SELECT")))
select_button.click()

store_login_button = wait.until(EC.element_to_be_clickable((By.ID, "btnSubmit")))
store_login_button.click()

cart_button = wait.until(
    EC.presence_of_element_located(
        (By.CSS_SELECTOR, "#btnCartWithItemCount.shopping-icn.active")
    )
)
driver.execute_script("arguments[0].click();", cart_button)

# ======================================================
# HELPERS
# ======================================================
def wait_for_complete_file(directory, timeout=120):
    start = time.time()
    while time.time() - start < timeout:
        time.sleep(1)
        files = [
            f for f in os.listdir(directory)
            if f.endswith(".xlsx") and not f.endswith(".crdownload")
        ]
        if files:
            files.sort(key=lambda x: os.path.getmtime(os.path.join(directory, x)))
            return os.path.join(directory, files[-1])
    raise TimeoutError("Download did not complete")

# ======================================================
# EXPORT ORDER TEMPLATE
# ======================================================
export_btn = wait.until(EC.presence_of_element_located((By.ID, "btnExportOrder")))
driver.execute_script("arguments[0].click();", export_btn)

order_template_file = wait_for_complete_file(DOWNLOAD_DIR)

# ======================================================
# EXPORT CATALOGUE
# ======================================================
catalogue_link = wait.until(
    EC.presence_of_element_located((By.LINK_TEXT, "EXPORT CATALOGUE"))
)
driver.execute_script("arguments[0].click();", catalogue_link)

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

catalogue_file = wait_for_complete_file(DOWNLOAD_DIR)

# ======================================================
# CLEANUP
# ======================================================
driver.quit()

print(
    {
        "order_template": order_template_file,
        "catalogue": catalogue_file,
    }
)
