#!/usr/bin/env python3
"""
Link the Salmon Trout product to Seng Kong Fishery via Seatable API.
This fixes the data gap so the invoice test can proceed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src" / "skills" / "parse_invoice"))

from seatable_writer import _base

base = _base()

# IDs we need
seng_kong_id = "emagV9InSPSwKD6F6JAZTw"

# Find the salmon trout product (from Pok Brothers currently)
all_products = base.list_rows("Supplier Products")

salmon_trout_sp = None
for sp in all_products:
    name = sp.get("Name") or sp.get("Supplier Product Name") or ""
    if "PB LONGYANG FRESH FROZEN SMOKED SALMON TROUT FILLET" in name.upper():
        salmon_trout_sp = sp
        break

if not salmon_trout_sp:
    print("[ERROR] Could not find salmon trout product")
    sys.exit(1)

sp_id = salmon_trout_sp.get("_id")
sp_code = salmon_trout_sp.get("Code") or "?"
sp_name = salmon_trout_sp.get("Name") or "?"

print(f"Found product to update:")
print(f"  Code: {sp_code}")
print(f"  Name: {sp_name}")
print(f"  ID: {sp_id}")
print()

# Get current suppliers
current_suppliers = salmon_trout_sp.get("Supplier") or []
print(f"Current suppliers ({len(current_suppliers)}):")
for sup in current_suppliers:
    if isinstance(sup, dict):
        print(f"  - {sup.get('display_value', 'Unknown')} ({sup.get('row_id', sup.get('_id', '?'))})")
print()

# Check if Seng Kong is already linked
seng_kong_already_linked = any(
    sup.get("_id") == seng_kong_id or sup.get("row_id") == seng_kong_id
    for sup in current_suppliers
    if isinstance(sup, dict)
)

if seng_kong_already_linked:
    print("[OK] Seng Kong Fishery is already linked to this product")
    sys.exit(0)

# Add Seng Kong to the suppliers list
new_suppliers = list(current_suppliers) if isinstance(current_suppliers, list) else []

# Append Seng Kong
new_suppliers.append({
    "_id": seng_kong_id,
    "row_id": seng_kong_id,
    "display_value": "SENG KONG FISHERY SDN BHD"
})

print(f"Updating Supplier Product to add Seng Kong Fishery...")
print()

# Update the row
try:
    base.update_row("Supplier Products", sp_id, {
        "Supplier": new_suppliers
    })
    print("[OK] Successfully linked Seng Kong Fishery to salmon trout product")
    print()
    print("Verification:")

    # Read back to confirm
    updated = base.get_row("Supplier Products", sp_id)
    suppliers_after = updated.get("Supplier") or []
    print(f"  Product now has {len(suppliers_after)} supplier(s):")
    for sup in suppliers_after:
        if isinstance(sup, dict):
            print(f"    - {sup.get('display_value', 'Unknown')}")

except Exception as e:
    print(f"[ERROR] Failed to update: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
