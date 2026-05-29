#!/usr/bin/env python3
"""Check what's in the sandbox Seatable base."""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent / "src" / "skills" / "parse_invoice"))

load_dotenv()

os.environ["SEATABLE_API_TOKEN"] = os.getenv("SEATABLE_API_TOKEN_SANDBOX")
os.environ["SEATABLE_BASE_URL"] = os.getenv("SEATABLE_BASE_URL_SANDBOX")

from seatable_writer import _base

base = _base()

print("Sandbox Base Contents")
print("=" * 80)
print()

# Check Suppliers
try:
    suppliers = base.list_rows("Suppliers")
    print(f"Suppliers ({len(suppliers)} rows):")
    for s in suppliers:
        print(f"  - {s.get('Name')} (ID: {s.get('_id')})")
    print()
except Exception as e:
    print(f"[ERROR] Suppliers: {e}")
    print()

# Check Supplier Products
try:
    sps = base.list_rows("Supplier Products")
    print(f"Supplier Products ({len(sps)} rows):")
    for sp in sps:
        print(f"  - {sp.get('Name')} | Code: {sp.get('Code')} | Price: {sp.get('Price per Pack')} | Unit: {sp.get('Unit/UoM')}")
    print()
except Exception as e:
    print(f"[ERROR] Supplier Products: {e}")
    print()

# Check Ingredients
try:
    ings = base.list_rows("Ingredients")
    print(f"Ingredients ({len(ings)} rows):")
    for ing in ings[:5]:
        print(f"  - {ing.get('Name')}")
    print()
except Exception as e:
    print(f"[ERROR] Ingredients: {e}")
    print()

# Check Invoices
try:
    invs = base.list_rows("Invoices")
    print(f"Invoices ({len(invs)} rows):")
    for inv in invs[:5]:
        print(f"  - {inv.get('Name')} | Number: {inv.get('Invoice Number')}")
    print()
except Exception as e:
    print(f"[ERROR] Invoices: {e}")
    print()

# Check Price History
try:
    hist = base.list_rows("Price History")
    print(f"Price History ({len(hist)} rows):")
    for h in hist[:5]:
        print(f"  - Old: {h.get('Old Price')}, New: {h.get('New Price')}, Flag: {h.get('Flagged By')}")
    print()
except Exception as e:
    print(f"[ERROR] Price History: {e}")
