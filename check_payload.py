import sys
from pathlib import Path
sys.path.insert(0, str(Path("src/skills/parse_invoice")))

import json

# Load the parsed step1 result
parsed_file = Path("data/parsed_results/2026-05-29/CINV-1256-0526_20260529_000757.json")
with open(parsed_file) as f:
    step1 = json.load(f)

print(f"Step1 status: {step1.get('status')}")
invoices = step1.get('invoices', [])
print(f"Invoices found: {len(invoices)}")
if invoices:
    inv = invoices[0]
    print(f"  Invoice: {inv.get('invoice_number')}")
    print(f"  Supplier: {inv.get('supplier_name')}")
    print(f"  Items: {len(inv.get('line_items', []))}")
    if inv.get('line_items'):
        item = inv['line_items'][0]
        print(f"    Item 1: {item.get('product_name')}")

# Now simulate step2 to see what payload would be created
print("\nNow let's trace through what step2_compare would do...")
print("(This requires Seatable auth)")

