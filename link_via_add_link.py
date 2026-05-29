#!/usr/bin/env python3
"""
Use the add_link() API to properly link salmon trout to Seng Kong Fishery.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src" / "skills" / "parse_invoice"))

from seatable_writer import _base, add_row_link

base = _base()

# IDs
seng_kong_id = "emagV9InSPSwKD6F6JAZTw"
salmon_trout_sp_id = "I3B4s9GdSfOjziy0pcmxWA"

print("Linking Salmon Trout product to Seng Kong Fishery via add_link()")
print(f"  SP Row ID: {salmon_trout_sp_id}")
print(f"  Seng Kong ID: {seng_kong_id}")
print()

# Use add_link to create the link
result = add_row_link(
    base=base,
    link_column_table="Supplier Products",
    link_column_name="Supplier",
    link_column_row_id=salmon_trout_sp_id,
    target_table="Suppliers",
    target_row_id=seng_kong_id,
)

if result:
    print("[OK] Link created successfully")
    print()

    # Verify
    print("Verifying...")
    row = base.get_row("Supplier Products", salmon_trout_sp_id)
    suppliers = row.get("Supplier") or []

    print(f"Product now has {len(suppliers)} supplier(s):")
    for sup in suppliers:
        if isinstance(sup, dict):
            print(f"  - {sup.get('display_value', 'Unknown')}")
else:
    print("[ERROR] Failed to create link")
    sys.exit(1)
