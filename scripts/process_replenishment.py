import sys
from pathlib import Path

# Repo root on sys.path (prevents "No module named config")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import os
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from config.settings import (
    DOWNLOAD_DIR,
    MASTER_DIR,
    OUTPUT_DIR,
    logger,
)
from config.secrets import (
    COVA_CLIENT_ID,
    COVA_CLIENT_SECRET,
    COVA_USERNAME,
    COVA_PASSWORD,
)

# ----------------------------
# CONFIG FLAGS
# ----------------------------
DAYS_LOOKBACK_SALES = 28
SECURITY_DAYS_NON_FLOWTHROUGH = 6
SECURITY_DAYS_FLOWTHROUGH = 10  # used only if flow-through can be detected from catalogue
WEEK_DAYS_SUPPLY = 7

# If True: when security/lead/week/onhand are all 0, force Qty Needed to 1 (matches your Colab override idea)
FORCE_ONE_WHEN_ALL_ZERO = True


def _require_env(name: str, value: str | None) -> str:
    if value is None or str(value).strip() == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _safe_loc_filename(location: str) -> str:
    # Safe for filenames, keep spaces/dashes/underscores
    return "".join(ch if ch.isalnum() or ch in (" ", "-", "_") else "_" for ch in location).strip()


def _latest_file_by_predicate(folder: str, predicate) -> str:
    files = [f for f in os.listdir(folder) if predicate(f)]
    if not files:
        raise FileNotFoundError(f"No matching files found in: {folder}")
    latest = max(files, key=lambda f: os.path.getmtime(os.path.join(folder, f)))
    return os.path.join(folder, latest)


def _find_column(df: pd.DataFrame, candidates: list[str]) -> str:
    """
    Find a column in df matching any candidate, case-insensitive and trimmed.
    Returns the actual column name.
    """
    norm = {c.strip().lower(): c for c in df.columns}
    for cand in candidates:
        key = cand.strip().lower()
        if key in norm:
            return norm[key]
    # Try fuzzy contains (useful if OCS adds suffixes)
    for cand in candidates:
        key = cand.strip().lower()
        for nkey, original in norm.items():
            if key in nkey:
                return original
    raise KeyError(f"Could not find any of columns {candidates} in columns: {list(df.columns)}")


def _get_access_token() -> str:
    _require_env("COVA_CLIENT_ID", COVA_CLIENT_ID)
    _require_env("COVA_CLIENT_SECRET", COVA_CLIENT_SECRET)
    _require_env("COVA_USERNAME", COVA_USERNAME)
    _require_env("COVA_PASSWORD", COVA_PASSWORD)

    token_url = "https://accounts.iqmetrix.net/v1/oauth2/token"
    payload = {
        "grant_type": "password",
        "client_id": COVA_CLIENT_ID,
        "client_secret": COVA_CLIENT_SECRET,
        "username": COVA_USERNAME,
        "password": COVA_PASSWORD,
    }

    resp = requests.post(
        token_url,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20,
    )
    if resp.status_code != 200:
        # Log the server response text so you can see the actual reason
        logger.error(f"Token request failed: {resp.status_code} | {resp.text}")
        resp.raise_for_status()

    return resp.json()["access_token"]


def run():
    logger.info("Starting replenishment processing")

    # ---------------- CONFIG ----------------
    lead = [4, 4, 6, 5, 6, 0, 0]
    today_idx = datetime.today().weekday()

    location_ids = [
        "337565", "369139", "346750", "352270",
        "359751", "377354", "377353", "377352"
    ]

    location_mapping = {
        "369139": "Toronto West (Dundas)",
        "359751": "Milton",
        "346750": "Mississauga",
        "337565": "Hamilton",
        "352270": "Toronto North (Leaside)",
        "377353": "Oshawa",
        "377354": "Burlington",
        "377352": "Toronto East (Beaches)"
    }

    # ---------------- AUTH TOKEN ----------------
    access_token = _get_access_token()
    logger.info("COVA access token retrieved")

    # ---------------- INVENTORY DATA ----------------
    base_url = "https://api.covasoft.net/dataplatform"
    company_id = "287921"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    all_products: list[dict] = []
    page_size = 500

    for loc_id in location_ids:
        skip = 0
        while True:
            payload = {
                "LocationId": loc_id,
                "IncludeAvailability": True,
                "IncludePricing": True,
                "InStockOnly": True,
                "Skip": skip,
                "Top": page_size
            }

            resp = requests.post(
                f"{base_url}/v1/Companies/{company_id}/DetailedProductData",
                headers=headers,
                json=payload,
                timeout=60,
            )
            resp.raise_for_status()

            products = resp.json().get("Products", [])
            if not products:
                break

            for product in products:
                for av in product.get("Availability", []):
                    all_products.append({
                        "SKU": product.get("CatalogSku"),
                        "Location": location_mapping.get(str(av.get("LocationId"))),
                        "In Stock Qty": av.get("InStockQuantity", 0),
                        "Unit Cost": av.get("UnitCost"),
                        "Last Updated": av.get("UpdatedDateUtc"),
                    })

            skip += page_size

    inventory_df = (
        pd.DataFrame(all_products)
        .groupby(["Location", "SKU"], as_index=False)
        .agg({
            "In Stock Qty": "sum",
            "Unit Cost": "first",
            "Last Updated": "max"
        })
    )
    logger.info("Inventory data prepared")

    # ---------------- SALES DATA (last 28 days) ----------------
    now = datetime.today()
    start_date = (now - timedelta(days=DAYS_LOOKBACK_SALES)).replace(hour=5, minute=0, second=0, microsecond=0)
    end_date = now.replace(hour=5, minute=0, second=0, microsecond=0)

    sales_rows: list[dict] = []

    for loc_id in location_ids:
        url = (
            f"https://api.covasoft.net/posreports/v1/"
            f"Companies({company_id})/Entities({loc_id})/ByInvoiceWithProducts"
            f"?DateRange=Date ge datetime'{start_date}' and Date le datetime'{end_date}'"
        )

        resp = requests.get(url, headers=headers, timeout=60)
        resp.raise_for_status()

        for invoice in resp.json():
            location = invoice.get("SoldAtEntityName")
            for product in invoice.get("Products", []):
                sales_rows.append({
                    "SKU": product.get("Sku"),
                    "Location": location,
                    "Items Sold": product.get("Quantity", 0)
                })

    sales_df = (
        pd.DataFrame(sales_rows)
        .groupby(["Location", "SKU"], as_index=False)
        .agg({"Items Sold": "sum"})
    )
    logger.info("Sales data prepared")

    # ---------------- LOAD MASTER FILES ----------------
    assortment_path = os.path.join(MASTER_DIR, "STORE ITEM COMBINATION MASTER FILE (20).xlsx")
    if not os.path.exists(assortment_path):
        raise FileNotFoundError(f"Missing master file: {assortment_path}")
    assortment_df = pd.read_excel(assortment_path)

    delist_path = os.path.join(MASTER_DIR, "Overall_Delist.xlsx")
    if not os.path.exists(delist_path):
        raise FileNotFoundError(f"Missing delist file: {delist_path}")
    delist_df = pd.read_excel(delist_path)

    # ---------------- LOAD LATEST DOWNLOADED FILES ----------------
    # Catalogue: support "OCS_Catalogue" or "OCS_Catalog"
    latest_catalog_path = _latest_file_by_predicate(
        DOWNLOAD_DIR,
        lambda f: f.lower().endswith(".xlsx") and ("ocs_catalogue" in f.lower() or "ocs_catalog" in f.lower())
    )
    ocs_catalog_df = pd.read_excel(latest_catalog_path)

    # OrderExport template
    latest_export_path = _latest_file_by_predicate(
        DOWNLOAD_DIR,
        lambda f: f.lower().endswith(".xlsx") and "orderexport" in f.lower()
    )
    ocs_export_df = pd.read_excel(latest_export_path)

    logger.info(f"Loaded assortment: {assortment_path}")
    logger.info(f"Loaded delist: {delist_path}")
    logger.info(f"Loaded latest catalogue: {latest_catalog_path}")
    logger.info(f"Loaded latest order export: {latest_export_path}")

    # ---------------- CALCULATIONS (Location x SKU) ----------------
    merged = pd.merge(
        assortment_df[["SKU", "Location", "Supplier SKU"]],
        sales_df,
        on=["SKU", "Location"],
        how="left"
    ).fillna({"Items Sold": 0})

    merged["AVG Item sold /day"] = merged["Items Sold"] / DAYS_LOOKBACK_SALES

    # Default security stock first (may be overwritten later if flow-through detected)
    merged["Security Stock"] = np.ceil(merged["AVG Item sold /day"] * SECURITY_DAYS_NON_FLOWTHROUGH)
    merged["Leadtime"] = np.ceil(merged["AVG Item sold /day"] * lead[today_idx])
    merged["1 Week of Supply"] = np.ceil(merged["AVG Item sold /day"] * WEEK_DAYS_SUPPLY)

    merged = pd.merge(
        merged,
        inventory_df.rename(columns={"In Stock Qty": "Current On Hand Qty"}),
        on=["SKU", "Location"],
        how="left"
    ).fillna({"Current On Hand Qty": 0})

    merged["Qty Needed"] = (
        merged["Security Stock"]
        + merged["Leadtime"]
        + merged["1 Week of Supply"]
        - merged["Current On Hand Qty"]
    ).clip(lower=0)

    # Delist filter
    if "SKU" in delist_df.columns:
        merged = merged[~merged["SKU"].isin(delist_df["SKU"])]
    logger.info("Applied delist filter")

    # Optional override: force Qty Needed to 1 when everything is 0
    if FORCE_ONE_WHEN_ALL_ZERO:
        condition = (
            (merged["Security Stock"] == 0)
            & (merged["Leadtime"] == 0)
            & (merged["1 Week of Supply"] == 0)
            & (merged["Current On Hand Qty"] == 0)
        )
        merged.loc[condition, "Qty Needed"] = 1

    # Save the baseline merged output (useful for audit/debug)
    baseline_file = os.path.join(
        OUTPUT_DIR,
        f"final_replenishment_{datetime.today().strftime('%Y%m%d')}.xlsx"
    )
    merged.to_excel(baseline_file, index=False)
    logger.info(f"Baseline replenishment file saved: {baseline_file}")

    # ---------------- BUILD OCS EXPORTS (BY LOCATION + COMBINED WITH LOCATION) ----------------
    # Detect OCS catalogue columns robustly
    col_variant = _find_column(ocs_catalog_df, ["OCS Variant Number", "Variant Number", "OCS Variant"])
    col_pack = _find_column(ocs_catalog_df, ["Pack Size", "Pack", "Min Order Qty", "Minimum Order Qty"])
    col_unit_price = _find_column(ocs_catalog_df, ["Unit Price", "Price"])
    col_stock = _find_column(ocs_catalog_df, ["Stock Status", "Stock"])
    # Fulfilment method is sometimes absent; handle gracefully
    try:
        col_fm = _find_column(ocs_catalog_df, ["Fulfilment Method", "Fulfillment Method"])
    except KeyError:
        col_fm = None

    ocs_relevant = ocs_catalog_df[[col_variant, col_pack, col_unit_price, col_stock] + ([col_fm] if col_fm else [])].copy()
    ocs_relevant = ocs_relevant.rename(columns={
        col_variant: "Supplier SKU",
        col_pack: "Min Order Qty",
        col_unit_price: "Unit Price",
        col_stock: "Stock Status",
        **({col_fm: "Fulfilment Method"} if col_fm else {}),
    })

    # Normalize types
    ocs_relevant["Supplier SKU"] = ocs_relevant["Supplier SKU"].astype(str).str.strip().str.lower()
    ocs_relevant["Min Order Qty"] = pd.to_numeric(ocs_relevant["Min Order Qty"], errors="coerce")
    ocs_relevant["Unit Price"] = pd.to_numeric(ocs_relevant["Unit Price"], errors="coerce")

    out = merged.copy()
    out["Supplier SKU"] = out["Supplier SKU"].astype(str).str.strip().str.lower()

    out = pd.merge(
        out,
        ocs_relevant,
        on="Supplier SKU",
        how="left"
    )

    # If fulfilment method exists, optionally apply flow-through security stock logic
    if "Fulfilment Method" in out.columns:
        fm = out["Fulfilment Method"].fillna("").astype(str).str.upper()
        out["Security Stock"] = np.ceil(
            np.where(
                fm.eq("FLOW-THROUGH"),
                out["AVG Item sold /day"] * SECURITY_DAYS_FLOWTHROUGH,
                out["AVG Item sold /day"] * SECURITY_DAYS_NON_FLOWTHROUGH
            )
        )
        out["Qty Needed"] = (
            out["Security Stock"]
            + out["Leadtime"]
            + out["1 Week of Supply"]
            - out["Current On Hand Qty"]
        ).clip(lower=0)

        if FORCE_ONE_WHEN_ALL_ZERO:
            condition = (
                (out["Security Stock"] == 0)
                & (out["Leadtime"] == 0)
                & (out["1 Week of Supply"] == 0)
                & (out["Current On Hand Qty"] == 0)
            )
            out.loc[condition, "Qty Needed"] = 1

    # Stockable flag (only YES allowed)
    stockable = out["Stock Status"].fillna("").astype(str).str.upper().eq("YES")
    valid_pack = out["Min Order Qty"].notna() & (out["Min Order Qty"] > 0)

    out["Packs to Order"] = 0.0
    out.loc[stockable & valid_pack, "Packs to Order"] = np.ceil(
        out.loc[stockable & valid_pack, "Qty Needed"] / out.loc[stockable & valid_pack, "Min Order Qty"]
    )

    # Cost estimate
    valid_price = out["Unit Price"].notna() & (out["Unit Price"] >= 0)
    out["Order Cost"] = 0.0
    out.loc[stockable & valid_pack & valid_price, "Order Cost"] = (
        out.loc[stockable & valid_pack & valid_price, "Packs to Order"]
        * out.loc[stockable & valid_pack & valid_price, "Min Order Qty"]
        * out.loc[stockable & valid_pack & valid_price, "Unit Price"]
    )

    # Combined location-aware file (for server filtering/debugging)
    combined = out[[
        "Location", "SKU", "Supplier SKU", "Qty Needed", "Packs to Order", "Min Order Qty", "Unit Price", "Order Cost", "Stock Status"
    ]].copy()

    combined["Packs to Order"] = pd.to_numeric(combined["Packs to Order"], errors="coerce").fillna(0).astype(int)
    combined = combined[combined["Packs to Order"] > 0]

    combined_file = os.path.join(
        OUTPUT_DIR,
        f"final_replenishment_with_location_{datetime.today().strftime('%Y%m%d')}.xlsx"
    )
    combined.to_excel(combined_file, index=False)
    logger.info(f"Wrote combined location-aware output: {combined_file}")

    # Build per-location OrderExport template outputs
    template_cols = list(ocs_export_df.columns)
    if "SKU" not in template_cols:
        raise KeyError(f"OrderExport template missing 'SKU' column. Columns: {template_cols}")
    if "Quantity" not in template_cols:
        raise KeyError(f"OrderExport template missing 'Quantity' column. Columns: {template_cols}")

    per_location_paths: dict[str, str] = {}

    for loc in sorted(combined["Location"].unique()):
        loc_rows = combined[combined["Location"] == loc].copy()

        # Map template SKU -> quantity using Supplier SKU (OCS variant)
        loc_rows["SKU"] = loc_rows["Supplier SKU"].astype(str).str.strip().str.lower()
        qty_map = loc_rows.set_index("SKU")["Packs to Order"].to_dict()

        loc_export = ocs_export_df.copy()
        loc_export["SKU"] = loc_export["SKU"].astype(str).str.strip().str.lower()

        # Add missing SKUs not present in template
        missing = sorted(set(qty_map.keys()) - set(loc_export["SKU"]))
        if missing:
            add_df = pd.DataFrame({c: [np.nan] * len(missing) for c in template_cols})
            add_df["SKU"] = missing
            add_df["Quantity"] = 0
            loc_export = pd.concat([loc_export, add_df], ignore_index=True)

        loc_export["Quantity"] = loc_export["SKU"].map(qty_map).fillna(0).astype(int)
        loc_export = loc_export[loc_export["Quantity"] > 0]

        loc_file = os.path.join(
            OUTPUT_DIR,
            f"OrderExport_{_safe_loc_filename(loc)}_{datetime.today().strftime('%Y%m%d')}.xlsx"
        )
        loc_export.to_excel(loc_file, index=False)
        per_location_paths[loc] = loc_file
        logger.info(f"Wrote OrderExport for {loc}: {loc_file}")

    logger.info(f"Replenishment processing completed. Outputs: {len(per_location_paths)} locations")


if __name__ == "__main__":
    run()
