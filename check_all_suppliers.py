#!/usr/bin/env python3
"""List all suppliers and find Seng Kong."""
import sys
from pathlib import Path
from rapidfuzz import fuzz, process

sys.path.insert(0, str(Path(__file__).parent / "src" / "skills" / "parse_invoice"))

from seatable_writer import _base

base = _base()

suppliers = base.list_rows("Suppliers")

print(f"All {len(suppliers)} suppliers:\n")

target = "SENG KONG FISHERY SDN BHD"
best_match = None
best_score = 0

for s in suppliers:
    name = s.get("Supplier Name") or ""
    row_id = s.get("_id")

    print(f"  [{row_id[:10]}...] {name}")

    # Check if this is Seng Kong
    if "SENG KONG" in name.upper():
        print(f"    ^^^ FOUND SENG KONG")

    # Fuzzy match
    score = fuzz.token_set_ratio(target.upper(), name.upper())
    if score > best_score:
        best_score = score
        best_match = (name, row_id, score)

print()
print(f"Best fuzzy match for '{target}':")
if best_match and best_score >= 70:
    name, row_id, score = best_match
    print(f"  Score: {best_score}")
    print(f"  Name: {name}")
    print(f"  ID: {row_id}")
else:
    print(f"  No match found (best score: {best_score})")
