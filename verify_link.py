#!/usr/bin/env python3
"""Verify the link was added."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src" / "skills" / "parse_invoice"))

from seatable_writer import _base

base = _base()

# Re-authenticate to get fresh data
base.auth()

sp_id = "I3B4s9GdSfOjziy0pcmxWA"
seng_kong_id = "emagV9InSPSwKD6F6JAZTw"

# Get the row
row = base.get_row("Supplier Products", sp_id)

print("Current state of salmon trout product:")
print()

name = row.get("Name") or row.get("Supplier Product Name") or "?"
print(f"Name: {name}")
print(f"ID: {sp_id}")
print()

suppliers = row.get("Supplier") or []
print(f"Suppliers field (raw): {suppliers}")
print()
print(f"Suppliers ({len(suppliers)}):")
for sup in suppliers:
    if isinstance(sup, dict):
        print(f"  - {sup.get('display_value', 'Unknown')}")
        print(f"    _id: {sup.get('_id', '?')}")
        print(f"    row_id: {sup.get('row_id', '?')}")
    else:
        print(f"  - {sup}")

print()

# Check if Seng Kong is there
seng_kong_found = any(
    sup.get("_id") == seng_kong_id or sup.get("row_id") == seng_kong_id
    for sup in suppliers
    if isinstance(sup, dict)
)

if seng_kong_found:
    print("[OK] Seng Kong Fishery IS linked")
else:
    print("[ERROR] Seng Kong Fishery NOT linked")
