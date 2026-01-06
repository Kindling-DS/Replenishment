import os
import logging

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA_DIR = os.path.join(BASE_DIR, "data")
DOWNLOAD_DIR = os.path.join(DATA_DIR, "downloads")
MASTER_DIR = os.path.join(DATA_DIR, "masters")
OUTPUT_DIR = os.path.join(DATA_DIR, "outputs")
LOG_DIR = os.path.join(BASE_DIR, "logs")

for d in [DATA_DIR, DOWNLOAD_DIR, MASTER_DIR, OUTPUT_DIR, LOG_DIR]:
    os.makedirs(d, exist_ok=True)

logging.basicConfig(
    filename=os.path.join(LOG_DIR, "pipeline.log"),
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("replenishment")
