import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import os
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from config.settings import (
    BASE_DIR,
    DOWNLOAD_DIR,
    MASTER_DIR,
    OUTPUT_DIR,
    logger
)
from config.secrets import (
    COVA_CLIENT_ID,
    COVA_CLIENT_SECRET,
    COVA_USERNAME,
    COVA_PASSWORD
)


def run():
    logger.info("Starting replenishment processing")

    # ---------------- CONFIG ----------------
    lead = [4, 4, 6, 5, 6, 0, 0]
    today = datetime.today().weekday()

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
    token_url = "https://accounts.iqmetrix.net/v1/oauth2/token"
    auth_payload = {
        "grant_type": "password",
        "client_id": COVA_CLIENT_ID,
        "client_secret": COVA_CLIENT_SECRET,
        "username": COVA_USERNAME,
        "password": COVA_PASSWORD,
    }

    token_response = requests.post(token_url, data=auth_payload)
    token_response.raise_for_status()
    access_token = token_response.json()["access_token"]
    logger.info("COVA access token retrieved")

    # ---------------- INVENTORY DATA ----------------
    base_url = "https://api.covasoft.net/dataplatform"
    company_id = "287921"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    all_products = []
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

            response = requests.post(
                f"{base_url}/v1/Companies/{company_id}/DetailedProductData",
                headers=headers,
                json=payload
            )
            response.raise_for_status()

            products = response.json().get("Products", [])
            if not products:
                break

            for product in products:
                for av in product.get("Availability", []):
                    all_products.append({
                        "SKU": product.get("CatalogSku"),
                        "Location": location_mapping.get(str(av.get("LocationId"))),
                        "In Stock Qty": av.get("InStockQuantity", 0),
                        "Unit Cost": av.get("UnitCost"),
                        "Last Updated": av.get("UpdatedDateUtc")
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

    # ---------------- SALES DATA ----------------
    now = datetime.today()
    start_date = (now - timedelta(days=28)).replace(hour=5, minute=0, second=0)
    end_date = now.replace(hour=5, minute=0, second=0)

    sales_rows = []

    for loc_id in location_ids:
        url = (
            f"https://api.covasoft.net/posreports/v1/"
            f"Companies({company_id})/Entities({loc_id})/ByInvoiceWithProducts"
            f"?DateRange=Date ge datetime'{start_date}' and Date le datetime'{end_date}'"
        )

        response = requests.get(url, headers=headers)
        response.raise_for_status()

        for invoice in response.json():
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
    assortment_df = pd.read_excel(
        os.path.join(MASTER_DIR, "STORE ITEM COMBINATION MASTER FILE (20).xlsx")
    )

    ocs_catalog_df = pd.read_excel(
        sorted(f for f in os.listdir(DOWNLOAD_DIR) if "OCS_Catalogue" in f)[-1]
    )

    delist_df = pd.read_excel(os.path.join(MASTER_DIR, "Overall_Delist.xlsx"))
    ocs_export_df = pd.read_excel(
        sorted(f for f in os.listdir(DOWNLOAD_DIR) if "OrderExport" in f)[-1]
    )

    # ---------------- CALCULATIONS ----------------
    merged = pd.merge(
        assortment_df[["SKU", "Location", "Supplier SKU"]],
        sales_df,
        on=["SKU", "Location"],
        how="left"
    ).fillna({"Items Sold": 0})

    merged["AVG Item sold /day"] = merged["Items Sold"] / 28
    merged["Security Stock"] = np.ceil(merged["AVG Item sold /day"] * 6)
    merged["Leadtime"] = np.ceil(merged["AVG Item sold /day"] * lead[today])
    merged["1 Week of Supply"] = np.ceil(merged["AVG Item sold /day"] * 7)

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

    merged = merged[~merged["SKU"].isin(delist_df["SKU"])]

    # ---------------- OUTPUT ----------------
    output_file = os.path.join(
        OUTPUT_DIR,
        f"final_replenishment_{datetime.today().strftime('%Y%m%d')}.xlsx"
    )

    merged.to_excel(output_file, index=False)
    logger.info(f"Replenishment file saved: {output_file}")

    logger.info("Replenishment processing completed")
