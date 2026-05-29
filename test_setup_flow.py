#!/usr/bin/env python3
"""Test the setup flow with a synthetic payload."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src" / "skills" / "parse_invoice"))

import json

# Create a synthetic payload for Seng Kong Fishery with missing supplier
payload = {
    "invoice_number": "CINV-1256-0526",
    "supplier_name": "SENG KONG FISHERY SDN BHD",
    "supplier_row_id": None,
    "invoice_date": "08-May-2026",
    "supplier_matched": False,
    "supplier_candidates": [],
    "matched_items": [],
    "price_changes": [],
    "confirm_items": [],
    "unmatched_items": [
        {
            "product_name": "FROZEN SMOKED SALMON TROUT FILLET 1KG",
            "invoice_unit": "kg",
            "invoice_unit_price": 74.0,
            "candidates": []
        }
    ],
    "data_gaps": [],
    "unit_mismatches": [],
    "handwriting": None,
    "has_handwriting": False,
    "invoice_file_path": "data/invoices_inbox/2026-05-28/CINV-1256-0526.pdf"
}

print("Testing setup flow with synthetic payload:")
print(f"  Invoice: {payload['invoice_number']}")
print(f"  Supplier: {payload['supplier_name']}")
print(f"  Supplier matched: {payload['supplier_matched']}")
print(f"  Unmatched items: {len(payload['unmatched_items'])}")
print(f"  Item 1: {payload['unmatched_items'][0]['product_name']} @ RM{payload['unmatched_items'][0]['invoice_unit_price']}/{payload['unmatched_items'][0]['invoice_unit']}")
print()

from notifier import notify_invoice_comparison

print("[TEST] Calling notify_invoice_comparison with supplier_matched=False")
result = notify_invoice_comparison(payload)
print(f"[RESULT] notify_invoice_comparison returned: {result}")
print()

# Check if setup state was created
from setup_handler import load_setup_state
state = load_setup_state(payload["invoice_number"])
if state:
    print("[OK] Setup state created!")
    print(f"  Supplier: {state['supplier_name']}")
    print(f"  Items to setup: {len(state['items'])}")
    for i, item in enumerate(state['items'], 1):
        print(f"    Item {i}: {item['product_name']} @ RM{item['invoice_unit_price']}")
else:
    print("[ERROR] Setup state NOT created")

