#!/usr/bin/env python3
"""Check what supplier row ID was matched."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src" / "skills" / "parse_invoice"))

from seatable_writer import _base

base = _base()

# List all suppliers
all_suppliers = base.list_rows("Suppliers")
print(f"All suppliers ({len(all_suppliers)}):")
for s in all_suppliers:
    supplier_id = s.get('_id')
    name = s.get('Name')
    print(f"  [{supplier_id[:10]}...] {name}")

print()
print("Looking for match to SENG KONG FISHERY SDN BHD...")

# Check if emagV9InSPSwKD6F6JAZTw is in there
target_id = "emagV9InSPSwKD6F6JAZTw"
matches = [s for s in all_suppliers if s.get('_id') == target_id]

if matches:
    print(f"[OK] Found row {target_id}:")
    print(f"  Name: {matches[0].get('Name')}")
else:
    print(f"[NOT FOUND] Row {target_id} not in Suppliers table")

# Try fuzzy match
from rapidfuzz import fuzz

target_name = "SENG KONG FISHERY SDN BHD"
best_match = None
best_score = 0

for s in all_suppliers:
    name = s.get('Name') or ""
    score = fuzz.token_set_ratio(target_name.upper(), name.upper())
    if score > best_score:
        best_score = score
        best_match = s

if best_match and best_score >= 80:
    print(f"\n[FUZZY MATCH] Score: {best_score}")
    print(f"  Expected: {target_name}")
    print(f"  Found: {best_match.get('Name')}")
    print(f"  ID: {best_match.get('_id')}")
else:
    print(f"\n[NO FUZZY MATCH] Best score: {best_score} (need >= 80)")
