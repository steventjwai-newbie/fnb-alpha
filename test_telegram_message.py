#!/usr/bin/env python3
"""Check what Telegram message is being sent."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src" / "skills" / "parse_invoice"))

payload = {
    "invoice_number": "CINV-1256-0526",
    "supplier_name": "SENG KONG FISHERY SDN BHD",
    "supplier_matched": False,
    "unmatched_items": [
        {
            "product_name": "FROZEN SMOKED SALMON TROUT FILLET 1KG",
            "invoice_unit": "kg",
            "invoice_unit_price": 74.0,
            "candidates": []
        }
    ]
}

invoice_num = payload.get("invoice_number")
supplier_name = payload.get("supplier_name")
unmatched = payload.get("unmatched_items", [])

items_state = [{
    "product_name": u["product_name"],
    "invoice_unit": u.get("invoice_unit"),
    "invoice_unit_price": u.get("invoice_unit_price"),
    "product_row_id": None,
    "product_added": False,
    "ingredient_row_id": None,
    "ingredient_linked": False,
    "status": "pending",
} for u in unmatched]

text = (
    f"*Setup Required* — {invoice_num}\n"
    f"Supplier: `{supplier_name}`\n"
    f"Not found in Seatable.\n\n"
    f"{len(items_state)} product(s) to set up after.\n\n"
    f"Create this supplier?"
)

print("Telegram message that would be sent:")
print("=" * 60)
print(text)
print("=" * 60)
print()
print("Buttons:")
print("  [Add Supplier] -> add_supplier:CINV-1256-0526")
print("  [Skip] -> skip_supplier:CINV-1256-0526")

