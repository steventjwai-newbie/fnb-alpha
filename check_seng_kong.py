#!/usr/bin/env python3
"""Check what Seng Kong products are in production."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src" / "skills" / "parse_invoice"))

from seatable_writer import _base

base = _base()

# Find Seng Kong Fishery
suppliers = base.list_rows("Suppliers")
seng_kong = None
for s in suppliers:
    if "SENG KONG" in (s.get("Name") or "").upper():
        seng_kong = s
        print(f"[OK] Found Seng Kong Fishery")
        print(f"  Name: {s.get('Name')}")
        print(f"  ID: {s.get('_id')}")
        break

if not seng_kong:
    print("[ERROR] Seng Kong Fishery not found in Suppliers")
    sys.exit(1)

seng_kong_id = seng_kong.get("_id")
print()

# Find all SP products for Seng Kong
all_sps = base.list_rows("Supplier Products")
seng_kong_sps = [sp for sp in all_sps if (sp.get("Supplier") or [{}])[0].get("_id") == seng_kong_id]

print(f"[OK] Found {len(seng_kong_sps)} Supplier Products for Seng Kong Fishery:")
print()

for sp in seng_kong_sps:
    name = sp.get("Name", "?")
    code = sp.get("Code", "?")
    price = sp.get("Price per Pack", "?")
    uom = sp.get("Unit/UoM", "?")
    qty = sp.get("Unit Quantity", "?")
    print(f"  [{code}] {name}")
    print(f"        Price: {price} | Qty: {qty} {uom}")
    print()

# Check if any contain "salmon" or "trout"
print()
print("Searching for SALMON/TROUT variants:")
for sp in seng_kong_sps:
    name = sp.get("Name", "").upper()
    if "SALMON" in name or "TROUT" in name:
        print(f"  [MATCH] {sp.get('Name')}")

if not any("SALMON" in sp.get("Name", "").upper() or "TROUT" in sp.get("Name", "").upper() for sp in seng_kong_sps):
    print("  [NOT FOUND] No Salmon or Trout products in Seng Kong Fishery")
