import os
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import mysql.connector


def load_dotenv(dotenv_path: Path) -> None:
    """
    Minimal .env loader (no external dependency).
    Loads KEY=VALUE lines into os.environ if not already set.
    """
    if not dotenv_path.exists():
        return

    for raw in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


def require_env(name: str) -> str:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        raise RuntimeError(f"Missing required env var: {name}")
    return v


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]  # Replenishment/
    load_dotenv(repo_root / ".env")

    # ---- MySQL env (from .env) ----
    mysql_host = os.getenv("MYSQL_HOST", "127.0.0.1")
    mysql_port = int(os.getenv("MYSQL_PORT", "3306"))
    mysql_user = require_env("MYSQL_USER")
    mysql_password = require_env("MYSQL_PASSWORD")
    mysql_db = require_env("MYSQL_DB")

    # ---- Excel file (fixed default path) ----
    # Default: Replenishment/data/outputs/final_date.xlsx
    excel_path = os.getenv("EXCEL_PATH", str(repo_root / "data" / "outputs" / "final_date.xlsx"))
    excel_file = Path(excel_path)

    if not excel_file.exists():
        raise FileNotFoundError(f"Excel file not found: {excel_file}")

    # Run id: date-based (override with RUN_ID if you want)
    run_id = os.getenv("RUN_ID") or datetime.now().strftime("%Y%m%d_%H%M%S")
    source_file = excel_file.name

    # Read Excel (first sheet by default)
    sheet_name = os.getenv("EXCEL_SHEET")  # optional
    df = pd.read_excel(excel_file, sheet_name=sheet_name)
    df = df.dropna(how="all")

    if df.empty:
        print("No rows found (empty after dropping blank rows). Nothing to insert.")
        return

    # Normalize NaN -> None for JSON
    df_obj = df.where(pd.notnull(df), None)

    # Detect Location column (optional)
    location_col = None
    for c in df_obj.columns:
        if c.strip().lower() == "location":
            location_col = c
            break

    records = []
    for i, row in df_obj.iterrows():
        row_dict = row.to_dict()
        row_json = json.dumps(row_dict, default=str)

        location_val = None
        if location_col:
            v = row_dict.get(location_col)
            location_val = None if v is None else str(v)

        records.append((run_id, source_file, int(i), location_val, row_json))

    conn = mysql.connector.connect(
        host=mysql_host,
        port=mysql_port,
        user=mysql_user,
        password=mysql_password,
        database=mysql_db,
    )
    cur = conn.cursor()

    insert_sql = """
        INSERT INTO excel_ingest
          (run_id, source_file, row_index, location, row_json)
        VALUES
          (%s, %s, %s, %s, CAST(%s AS JSON))
    """

    batch_size = 1000
    for start in range(0, len(records), batch_size):
        batch = records[start:start + batch_size]
        cur.executemany(insert_sql, batch)
        conn.commit()
        print(f"Inserted {start + len(batch)} / {len(records)} rows")

    cur.close()
    conn.close()

    print(f"Done. run_id={run_id}, file={source_file}, rows={len(records)}")


if __name__ == "__main__":
    main()
