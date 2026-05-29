#!/usr/bin/env python3
"""Check Seng Kong Fishery's supplier products."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src" / "skills" / "parse_invoice"))

from seatable_writer import _base

base = _base()

seng_kong_id = "emagV9InSPSwKD6F6JAZTw"

# Get all products
all_products = base.list_rows("Supplier Products")

print(f"Total Supplier Products: {len(all_products)}\n")

# Filter for Seng Kong
seng_kong_products = []

for sp in all_products:
    supplier_field = sp.get("Supplier") or []

    # Check if this product is linked to Seng Kong
    is_seng_kong = False
    if isinstance(supplier_field, list):
        for item in supplier_field:
            if isinstance(item, dict):
                if item.get("_id") == seng_kong_id or item.get("row_id") == seng_kong_id:
                    is_seng_kong = True
                    break

    if is_seng_kong:
        seng_kong_products.append(sp)

print(f"Seng Kong Fishery Products ({len(seng_kong_products)}):\n")

for sp in seng_kong_products:
    code = sp.get("Code") or sp.get("SP Code") or "?"
    name = sp.get("Name") or sp.get("Supplier Product Name") or "?"
    price = sp.get("Price per Pack") or "?"
    qty = sp.get("Unit Quantity") or "?"
    uom = sp.get("Unit of Measure") or sp.get("Unit/UoM") or "?"

    print(f"  [{code}] {name}")
    print(f"        Price: {price} | Qty: {qty} {uom}")
    print()

# Also check if "SALMON" or "TROUT" exists anywhere
print()
print("Searching all Supplier Products for SALMON/TROUT:")
print()

for sp in all_products:
    name = sp.get("Name") or sp.get("Supplier Product Name") or ""
    if "SALMON" in name.upper() or "TROUT" in name.upper():
        code = sp.get("Code") or sp.get("SP Code") or "?"
        supplier_field = sp.get("Supplier") or []
        supplier_names = [s.get("display_value", "?") for s in supplier_field if isinstance(s, dict)] if isinstance(supplier_field, list) else []
        print(f"  [{code}] {name}")
        print(f"        Supplier: {', '.join(supplier_names)}")
        print()

if not any("SALMON" in sp.get("Name", "").upper() or "TROUT" in sp.get("Name", "").upper() or "SALMON" in sp.get("Supplier Product Name", "").upper() or "TROUT" in sp.get("Supplier Product Name", "").upper() for sp in all_products):
    print("  [NOT FOUND] No SALMON or TROUT products in entire Supplier Products table")
