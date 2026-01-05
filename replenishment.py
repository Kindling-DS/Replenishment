import requests
import pandas as pd
from datetime import datetime, timedelta
import glob
import numpy as np
from google.colab import drive
import time
import os

n=1
drive.mount('/content/drive')

while n>0:
    lead = [4,4,6,5,6,0,0]
    today = datetime.today()
    today = today.weekday()
    token_url = "https://accounts.iqmetrix.net/v1/oauth2/token"
    location_ids = ["337565", "369139", "346750", "352270", "359751","377354","377353","377352"]

    auth_payload = {
        "grant_type": "password",
        "client_id": "Kindling.SelfIntegration",
        "client_secret": "58gKwEAG3qNRnTyetsSnefzu",
        "username": "SelfIntegration.COVA.APIUser.Kindling",
        "password": "Kindling2024!",
    }

    token_response = requests.post(token_url, data=auth_payload)
    if token_response.status_code == 200:
        token_data = token_response.json()
        access_token = token_data.get("access_token")
        print(f"Access Token Retrieved: {access_token}")
    else:
        print(f"Failed to retrieve access token. Status Code: {token_response.status_code}")
        print(f"Error: {token_response.text}")
        exit()

    base_url = "https://api.covasoft.net/dataplatform"
    company_id = "287921"
    api_url = f"{base_url}/v1/Companies/{company_id}/DetailedProductData"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    location_mapping = {
        "369139": "Toronto West (Dundas)",
        "359751": "Milton",
        "346750": "Mississauga",
        "337565": "Hamilton",
        "352270": "Toronto North (Leaside)",
        "377353": "Oshawa",
        "377354" : "Burlington",
        "377352": "Toronto East (Beaches)"
    }

    page_size = 500
    max_records = 100000000
    all_products = []

    for loc_id in location_ids:
        skip = 0
        while True:
            payload = {
                "LocationId": loc_id,
                "IncludeProductSkusAndUpcs": True,
                "IncludeProductSpecifications": False,
                "IncludeClassifications": False,
                "IncludeProductAssets": False,
                "IncludeAvailability": True,
                "IncludePackageDetails": False,
                "IncludePricing": True,
                "IncludeTaxes": True,
                "InStockOnly": True,
                "IncludeAllLifecycles": False,
                "SellingRoomOnly": False,
                "Skip": skip,
                "Top": page_size
            }

            print(f"Fetching records {skip} to {skip + page_size} for Location ID: {loc_id}...")
            response = requests.post(api_url, headers=headers, json=payload)

            if response.status_code == 200:
                data = response.json()
                products = data.get("Products", [])

                if not products:
                    print(f"No more products found for Location {loc_id}.")
                    break

                for product in products:
                    product_id = product.get("ProductId", "N/A")
                    master_product_name = product.get("MasterProductName", "N/A")
                    name = product.get("Name", "N/A")
                    sku = product.get("CatalogSku", "N/A")

                    company_price = product.get("CompanyLevelRegularPrice", {})
                    price = company_price.get("Price", "N/A") if company_price else "N/A"

                    short_description = product.get("ShortDescription", "N/A")

                    for av in product.get("Availability", []):
                        location_id = str(av.get("LocationId", "N/A"))
                        location_name = location_mapping.get(location_id, "Unknown Location")
                        all_products.append({
                            "Product ID": product_id,
                            "Master Product Name": master_product_name,
                            "Name": name,
                            "SKU": sku,
                            "Price": price,
                            "Short Description": short_description,
                            "Location": location_name,
                            "In Stock Qty": av.get("InStockQuantity", 0),
                            "Unit Cost": av.get("UnitCost", "N/A"),
                            "Last Updated": av.get("UpdatedDateUtc", "N/A"),
                        })

                if len(all_products) >= max_records:
                    print(f"Reached maximum limit of {max_records} records.")
                    all_products = all_products[:max_records]
                    break

                skip += page_size
            else:
                print(f"Failed to fetch data for Location {loc_id}. Status Code: {response.status_code}")
                print(f"Error: {response.text}")
                break

    if all_products:
        df = pd.DataFrame(all_products)
        inventory_df = df.groupby(['Location', 'SKU'], as_index=False).agg({
        'Product ID': 'first',
        'Master Product Name': 'first',
        'Name': 'first',
        'Price': 'first',
        'Short Description': 'first',
        'In Stock Qty': 'sum',
        'Unit Cost': lambda x: x.dropna().iloc[0] if not x.dropna().empty else "N/A",
        'Last Updated': lambda x: x.max()
        })
    else:
        print("No product data retrieved.")

    base_url = "https://api.covasoft.net/posreports/v1"
    company_id = "287921"
    location_id = ["337565", "346750", "352270", "359751","369139", "377353", "377354","377352"]

    token_url = "https://accounts.iqmetrix.net/v1/oauth2/token"
    auth_payload = {
        "grant_type": "password",
        "client_id": "Kindling.SelfIntegration",
        "client_secret": "58gKwEAG3qNRnTyetsSnefzu",
        "username": "SelfIntegration.COVA.APIUser.Kindling",
        "password": "Kindling2024!",
    }

    token_response = requests.post(token_url, data=auth_payload)
    if token_response.status_code == 200:
        token_data = token_response.json()
        access_token = token_data.get("access_token")
        print(f"Access Token Retrieved: {access_token}")
    else:
        print(f"Failed to retrieve access token. Status Code: {token_response.status_code}")
        print(f"Error: {token_response.text}")
        exit()

    now = datetime.today()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_date = midnight - timedelta(days=28) + timedelta(hours=5)
    end_date = midnight + timedelta(hours=5) - timedelta(seconds=1)

    columns = ["Product", "SKU", "Location", "Classification", "Items Sold", "Supplier SKUs"]

    all_data = []

    for loc_id in location_id:
        api_url = f"{base_url}/Companies({company_id})/Entities({loc_id})/ByInvoiceWithProducts"
        date_filter = f"DateRange=Date ge datetime'{start_date}' and Date le datetime'{end_date}'"
        full_url = f"{api_url}?{date_filter}"

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        response = requests.get(full_url, headers=headers)

        if response.status_code == 200:
            result = response.json()
            print(f"API response received successfully for Location {loc_id}")

            for invoice in result:
                location = invoice.get("SoldAtEntityName", "N/A")
                for product in invoice.get("Products", []):
                    all_data.append({
                        "Product": product.get("ProductName", "N/A"),
                        "SKU": product.get("Sku", "N/A"),
                        "Location": location,
                        "Classification": product.get("Classification", "N/A"),
                        "Items Sold": product.get("Quantity", 0),
                    })
        else:
            print(f"Failed to retrieve data for Location {loc_id}. Status Code: {response.status_code}")
            print(f"Error: {response.text}")

    df = pd.DataFrame(all_data)
    print(df.sample(50))
    supplier_skus_path = "/content/drive/My Drive/replenishment/update_always.xlsx"
    supplier_skus_df = pd.read_excel(supplier_skus_path, sheet_name="Sheet1")

    supplier_skus_df.rename(columns={"SKU": "Product SKU"}, inplace=True)

    merged_df = pd.merge(df, supplier_skus_df[['Product SKU', 'Supplier SKU']],
                         left_on="SKU", right_on="Product SKU", how="left")

    final_df = merged_df.rename(columns={"Supplier SKU": "Supplier SKUs"})[columns]
    sales_df = final_df.groupby(['Location', 'SKU'], as_index=False).agg({
        'Product': 'first',
        'Classification': 'first',
        'Items Sold': 'sum',
        'Supplier SKUs': lambda x: x.dropna().iloc[0] if not x.dropna().empty else "N/A"
    })

    leadtime = lead[today]
    week = 7
    security = 6

    sales_data = sales_df
    inventory_data = inventory_df
    assortment_data = pd.read_excel("/content/drive/My Drive/replenishment/STORE ITEM COMBINATION MASTER FILE (20).xlsx")
    ocs_catalog_data = pd.read_excel("/content/drive/My Drive/replenishment/OCS_Catalogue_05_Jan_2026_844AM.xlsx")
    delist_data = pd.read_excel("/content/drive/My Drive/replenishment/Overall_Delist.xlsx")
    ocs_export = pd.read_excel("/content/drive/My Drive/replenishment/OrderExport_05_Jan_2026_845AM_Packs.xlsx")
    products_add = pd.read_excel("/content/drive/My Drive/replenishment/add.xlsx")
    products_data = products_add
    # Mark Flow-Through items
    ocs_catalog_data['Stock Status'] = np.where(
        ocs_catalog_data['Fulfilment Method'] == "FLOW-THROUGH",
        "YES",
        ocs_catalog_data.get('Stock Status', "")  # keep existing value if not Flow-Through
    )

    # Select relevant columns
    assortment_relevant = (assortment_data[["Product", "SKU", "Location", "Supplier SKU"]].drop_duplicates(subset=["Supplier SKU", "Location"]))

    print(assortment_relevant.sample(50))
    sales_relevant = sales_data[["Location","SKU", "Items Sold"]]

    # Merge data
    merged_data = pd.merge(
        assortment_relevant,
        sales_relevant,
        on=["SKU", "Location"],
        how="left"
    )
    merged_data["Items Sold"] = merged_data["Items Sold"].fillna(0)

    ocs_relevant = ocs_catalog_data[["OCS Variant Number", "Pack Size", "Unit Price","Stock Status", "Fulfilment Method"]].rename(
        columns={"OCS Variant Number": "Supplier SKU", "Pack Size": "Min Order Qty", "Unit Price": "Order Cost","Fulfilment Method":"Fulfilment Method"}
    )

    print(merged_data.sample(50))
    print(ocs_relevant.sample(50))

    # Merge Fulfilment Method for conditional calculation
    merged_data = pd.merge(
        merged_data,
        ocs_relevant[['Supplier SKU', 'Fulfilment Method']],
        on='Supplier SKU',
        how='left'
    )

    # Average items sold per day
    merged_data["AVG Item sold /day"] = np.round(merged_data["Items Sold"] / 28, 2)

    # Conditional Security Stock: 10 days for Flow-Through, 3 days otherwise
    merged_data["Security Stock"] = np.ceil(
        np.where(
            merged_data["Fulfilment Method"] == "FLOW-THROUGH",
            merged_data["AVG Item sold /day"] * 10,
            merged_data["AVG Item sold /day"] * 6
        )
    )

    merged_data["Leadtime"] = np.ceil(merged_data["AVG Item sold /day"] * leadtime)
    merged_data["1 Week of Supply"] = np.ceil(merged_data["AVG Item sold /day"] * week)

    inventory_relevant = inventory_data[["SKU", "Location", "In Stock Qty"]].rename(columns={"In Stock Qty": "Current On Hand Qty"})
    merged_data = pd.merge(merged_data, inventory_relevant, on=["SKU", "Location"], how="left")
    merged_data["Current On Hand Qty"].fillna(0, inplace=True)

    merged_data["Qty Needed"] = (
        merged_data["Security Stock"] + merged_data["Leadtime"] + merged_data["1 Week of Supply"] - merged_data["Current On Hand Qty"]
    ).apply(lambda x: max(0, x))

    # Step 2: Apply override condition
    condition = (
        (merged_data["Security Stock"] == 0) &
        (merged_data["Leadtime"] == 0) &
        (merged_data["1 Week of Supply"] == 0) &
        (merged_data["Current On Hand Qty"] == 0)
    )

    merged_data.loc[condition, "Qty Needed"] = 1


    ocs_relevant["Supplier SKU"] = ocs_relevant["Supplier SKU"].astype(str)  # Ensure SKU is string
    merged_data["Supplier SKU"] = merged_data["Supplier SKU"].astype(str)  # Ensure SKU is string for merge


    ocs_relevant["Supplier SKU"] = ocs_relevant["Supplier SKU"].str.lower()
    merged_data["Supplier SKU"] = merged_data["Supplier SKU"].str.lower()

    merged_data = pd.merge(merged_data, ocs_relevant, on="Supplier SKU", how="left")

    merged_data["Packs to Order"] = np.ceil(merged_data["Stock Status"].eq('YES').mul(1)*merged_data["Qty Needed"] / merged_data["Min Order Qty"])

    merged_data["Order Cost"] = merged_data["Packs to Order"] * merged_data["Min Order Qty"] * merged_data["Order Cost"]*merged_data["Stock Status"].eq('YES').mul(1)

    merged_data.to_excel("/content/drive/My Drive/replenishment/merged_data.xlsx", index=False)

    location_mapping = {
        "369139": "Toronto West (Dundas)",
        "359751": "Milton",
        "346750": "Mississauga",
        "337565": "Hamilton",
        "352270": "Toronto North (Leaside)",
        "377353": "Oshawa",
        "377354" : "Burlington",
        "377352": "Toronto East (Beaches)"
    }

    drop = delist_data['SKU']

    merged_data = merged_data[~merged_data['SKU'].isin(drop)]

    # Step 1: Clean up merged_data to create final_data
    final_data = merged_data[
        ["SKU", "Product", "Location", "Supplier SKU", "Packs to Order", "Order Cost"]
    ]

    final_data["Order Cost"] = pd.to_numeric(final_data["Order Cost"], errors='coerce')
    final_data = final_data[final_data["Order Cost"].notna() & (final_data["Order Cost"] != 0)]
    # Step 2: Prepare products_data to match final_data columns
    required_columns = final_data.columns.tolist()

    # Add any missing columns to products_data with default value (e.g., NaN or 0)
    for col in required_columns:
        if col not in products_data.columns:
            products_data[col] = None  # or 0 for numeric fields
    # Select only the required columns from products_data
    products_data_filtered = products_data[required_columns]

    def remove_existing_inventory(products_data_filtered, inventory_data):
        # Ensure both DataFrames have stripped column names
        products_data_filtered.columns = products_data_filtered.columns.str.strip()
        inventory_data.columns = inventory_data.columns.str.strip()

        # Merge to find matches (inner join on both SKU and Location)
        merged = products_data_filtered.merge(
            inventory_data[['SKU', 'Location']],
            on=['SKU', 'Location'],
            how='left',
            indicator=True
        )

        # Keep only those NOT in inventory_data
        filtered = merged[merged['_merge'] == 'left_only']

        # Drop the merge indicator column
        filtered = filtered.drop(columns=['_merge'])

        return filtered
    filtered_products = remove_existing_inventory(products_data_filtered, inventory_data)

    # Step 3: Combine
    final_data = pd.concat([final_data, filtered_products], ignore_index=True)
    final_data.to_excel("/content/drive/My Drive/replenishment/final_data.xlsx", index=False)
    # Optional check
    base_path1 = "/content/drive/My Drive/replenishment/dundas/"
    base_path2 = "/content/drive/My Drive/replenishment/pine/"
    base_path3 = "/content/drive/My Drive/replenishment/lakeshore/"
    base_path4 = "/content/drive/My Drive/replenishment/leaside/"
    base_path5 = "/content/drive/My Drive/replenishment/mary/"
    base_path6 = "/content/drive/My Drive/replenishment/simcoe/"
    base_path7 = "/content/drive/My Drive/replenishment/mountainside/"
    base_path8 = "/content/drive/My Drive/replenishment/beaches/"

    todays = datetime.today()
    weeks = todays.isocalendar()[1]

    locations = {
        "Hamilton": base_path2 + "Replenishment Management Pine Week {0}.xlsx".format(weeks),
        "Mississauga": base_path3 + "Replenishment Management Lakeshore Week {0}.xlsx".format(weeks),
        "Toronto West (Dundas)": base_path1 + "Replenishment Management Dundas Week {0}.xlsx".format(weeks),
        "Milton": base_path5 + "Replenishment Management Mary Week {0}.xlsx".format(weeks),
        "Toronto North (Leaside)": base_path4 + "Replenishment Management Eglinton Week {0}.xlsx".format(weeks),
        "Oshawa": base_path6 + "Replenishment Management Simcoe Week {0}.xlsx".format(weeks),
        "Burlington": base_path7 + "Replenishment Management Mountainside Week {0}.xlsx".format(weeks),
        "Toronto East (Beaches)": base_path8 + "Replenishment Management Beaches Week {0}.xlsx".format(weeks)
    }

    for location, path in locations.items():
      final_data.sort_values(['Order Cost'], inplace=False)
      location_data = final_data[final_data["Location"] == location].drop_duplicates(subset=["SKU"])

      replenishment = location_data.rename(columns={"SKU": "COVA_SKU", "Supplier SKU": "SKU", "Packs to Order": "Quantity"})
      ocs_export_local = ocs_export.copy()
      replenishment_dict = replenishment.set_index("SKU")["Quantity"].to_dict()
      matching_skus = set(ocs_export_local["SKU"]).intersection(set(replenishment_dict.keys()))
      missing_skus = set(replenishment_dict.keys()) - set(ocs_export_local["SKU"])
      if missing_skus:
          missing_skus_df = pd.DataFrame({"SKU": list(missing_skus), "Quantity": 0})
          ocs_export_local = pd.concat([ocs_export_local, missing_skus_df], ignore_index=True)
      ocs_export_local["Quantity"] = ocs_export_local["SKU"].map(replenishment_dict).fillna(0) + ocs_export_local["Quantity"].fillna(0)
      ocs_export_local = ocs_export_local[ocs_export_local["Quantity"] > 0]
      ocs_export_local.to_excel(path, index=False)

#    if today == 0:
#      os.remove(base_path2 + "Replenishment Management Pine Week {0}.xlsx".format(weeks))
#      os.remove(base_path3 + "Replenishment Management Lakeshore Week {0}.xlsx".format(weeks))
#      os.remove(base_path4 + "Replenishment Management Eglinton Week {0}.xlsx".format(weeks))

#    elif today == 1:
#      os.remove(base_path2 + "Replenishment Management Pine Week {0}.xlsx".format(weeks))
#      os.remove(base_path3 + "Replenishment Management Lakeshore Week {0}.xlsx".format(weeks))
#      os.remove(base_path5 + "Replenishment Management Mary Week {0}.xlsx".format(weeks))
#      os.remove(base_path1 + "Replenishment Management Dundas Week {0}.xlsx".format(weeks))

#   elif today == 2:
#      os.remove(base_path3 + "Replenishment Management Lakeshore Week {0}.xlsx".format(weeks))
#      os.remove(base_path4 + "Replenishment Management Eglinton Week {0}.xlsx".format(weeks))
#      os.remove(base_path5 + "Replenishment Management Mary Week {0}.xlsx".format(weeks))
#      os.remove(base_path1 + "Replenishment Management Dundas Week {0}.xlsx".format(weeks))

#    elif today == 3:
#      os.remove(base_path2 + "Replenishment Management Pine Week {0}.xlsx".format(weeks))
#      os.remove(base_path4 + "Replenishment Management Eglinton Week {0}.xlsx".format(weeks))
#      os.remove(base_path5 + "Replenishment Management Mary Week {0}.xlsx".format(weeks))
#      os.remove(base_path1 + "Replenishment Management Dundas Week {0}.xlsx".format(weeks))

#    elif today == 4:
#      os.remove(base_path2 + "Replenishment Management Pine Week {0}.xlsx".format(weeks))
#      os.remove(base_path4 + "Replenishment Management Eglinton Week {0}.xlsx".format(weeks))
#      os.remove(base_path5 + "Replenishment Management Mary Week {0}.xlsx".format(weeks))
#      os.remove(base_path1 + "Replenishment Management Dundas Week {0}.xlsx".format(weeks))
#      os.remove(base_path3 + "Replenishment Management Lakeshore Week {0}.xlsx".format(weeks))

#    elif today == 5:
#      os.remove(base_path2 + "Replenishment Management Pine Week {0}.xlsx".format(weeks))
#      os.remove(base_path4 + "Replenishment Management Eglinton Week {0}.xlsx".format(weeks))
#      os.remove(base_path5 + "Replenishment Management Mary Week {0}.xlsx".format(weeks))
#      os.remove(base_path1 + "Replenishment Management Dundas Week {0}.xlsx".format(weeks))
#      os.remove(base_path3 + "Replenishment Management Lakeshore Week {0}.xlsx".format(weeks))

#    elif today == 6:
#      os.remove(base_path2 + "Replenishment Management Pine Week {0}.xlsx".format(weeks))
#      os.remove(base_path4 + "Replenishment Management Eglinton Week {0}.xlsx".format(weeks))
#      os.remove(base_path5 + "Replenishment Management Mary Week {0}.xlsx".format(weeks))
#      os.remove(base_path1 + "Replenishment Management Dundas Week {0}.xlsx".format(weeks))
#      os.remove(base_path3 + "Replenishment Management Lakeshore Week {0}.xlsx".format(weeks))

    time.sleep(86400)
