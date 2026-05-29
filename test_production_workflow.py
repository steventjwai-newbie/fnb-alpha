#!/usr/bin/env python3
"""
Production workflow test for Seng Kong Fishery invoice.
Simulates the full intake_listener + approval_handler workflow.
"""
import json
import os
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent / "src" / "skills" / "invoice_intake"))
sys.path.insert(0, str(Path(__file__).parent / "src" / "skills" / "parse_invoice"))

from dotenv import load_dotenv
load_dotenv()

# Verify we're using PRODUCTION credentials
prod_token = os.getenv("SEATABLE_API_TOKEN")
if not prod_token or prod_token.startswith("e55fe4052"):
    print("[ERROR] Not using production token! Aborting.")
    sys.exit(1)

print("=" * 80)
print("PRODUCTION WORKFLOW TEST — Seng Kong Fishery Invoice CINV-1256-0526")
print("=" * 80)
print(f"Base URL: {os.getenv('SEATABLE_BASE_URL')}")
print(f"Token: {prod_token[:10]}... (PRODUCTION)")
print()

from step1_extract import extract_invoice
from step2_compare import build_comparison
from cross_check import check_invoice as cross_check
from seatable_writer import (
    _base,
    upsert_invoice_row,
    write_price_history,
    update_sp_price,
)

invoice_path = Path(__file__).parent / "data" / "invoices_inbox" / "2026-05-27" / "seng_kong_fishery_cinv_1266_0526.jpg"

# ─── STEP 1: Extract ──────────────────────────────────────────────────────────
print("[STEP 1] Extract invoice via Gemini")
print("-" * 80)

step1 = extract_invoice(str(invoice_path))
if step1.get("status") == "error":
    print(f"[ERROR] Extraction failed: {step1.get('error_message')}")
    sys.exit(1)

invoices = step1.get("invoices", [])
if not invoices:
    print(f"[ERROR] No invoices found")
    sys.exit(1)

invoice = invoices[0]
print(f"[OK] Supplier: {invoice.get('supplier_name')}")
print(f"[OK] Invoice: {invoice.get('invoice_number')}")
print(f"[OK] Date: {invoice.get('invoice_date')}")
print(f"[OK] Total: RM{invoice.get('invoice_total', 0):.2f}")
print(f"[OK] Line items: {len(invoice.get('line_items', []))}")
for item in invoice.get('line_items', []):
    desc = item.get('description', 'Unknown')
    qty = item.get('quantity', '?')
    unit = item.get('unit', '?')
    price = item.get('unit_price', '?')
    print(f"  - {desc} | {qty} {unit} @ RM{price}")
print()

# ─── STEP 2: Cross-check (fill missing totals) ─────────────────────────────
print("[STEP 2] Cross-check & verify")
print("-" * 80)

for inv in step1.get("invoices", []):
    warnings = cross_check(inv)
    if warnings:
        print(f"[WARNINGS] Found {len(warnings)} cross-check issues:")
        for w in warnings:
            print(f"  - {w.get('type')}: {w.get('message')}")
    else:
        print(f"[OK] All cross-checks passed")
print()

# ─── STEP 3: Build comparison ──────────────────────────────────────────────
print("[STEP 3] Compare against Supplier Products")
print("-" * 80)

payloads = build_comparison(step1)
print(f"[OK] Generated {len(payloads)} comparison payload(s)")
print()

if not payloads:
    print("[ERROR] No payloads generated (no matches found)")
    sys.exit(1)

payload = payloads[0]

# ─── STEP 4: Display what would be sent to Telegram ────────────────────────
print("[STEP 4] Telegram notification preview")
print("-" * 80)

supplier = payload['supplier_name']
invoice_num = payload['invoice_number']
invoice_date = payload['invoice_date']

print(f"Invoice: {invoice_num} | {supplier}")
print(f"Date: {invoice_date}")
print()

items = payload.get('price_changes', [])
confirms = payload.get('confirm_items', [])

if items:
    print(f"PRICE CHANGES ({len(items)}):")
    for i, item in enumerate(items, 1):
        old = item.get('current_price', 0)
        new = item.get('invoice_price', 0)
        pct = ((new - old) / old * 100) if old else 0
        tier = item.get('match_score_tier', '?')
        code = item.get('sp_code', '?')
        product_name = item.get('matched_product_name') or item.get('product_name') or '?'
        print(f"  [{i}] {code} | {product_name}")
        print(f"      RM{old:.2f} -> RM{new:.2f} ({pct:+.1f}%) [{tier}]")
        if item.get('magnitude_flag'):
            print(f"      ** MAGNITUDE FLAG: Change > 30%")
    print()

if confirms:
    print(f"CONFIRM ITEMS ({len(confirms)}):")
    for i, item in enumerate(items.__len__() + 1, 1):
        print(f"  [{i}] {item['description']}")
        print(f"      {item.get('reason', 'needs confirmation')}")
    print()

print("Buttons: [Approve] [Reject] [Skip] for each item")
print()

# ─── STEP 5: Auto-approve first price change ──────────────────────────────
if not items:
    print("[WARNING] No price changes to approve. Skipping Seatable write.")
    sys.exit(0)

item = items[0]
product_name = item.get('matched_product_name') or item.get('product_name') or '?'
print(f"[STEP 5] Auto-approving first item: {item['sp_code']} | {product_name}")
print("-" * 80)

base = _base()
payload["invoice_file_path"] = str(invoice_path)

# Create invoice row
print("  [5a] Creating Invoices row...")
invoice_row_id = upsert_invoice_row(
    base=base,
    invoice_number=payload['invoice_number'],
    supplier_name=payload['supplier_name'],
    supplier_row_id=payload.get('supplier_row_id', ''),
    invoice_date=payload.get('invoice_date', ''),
)

if not invoice_row_id:
    print(f"  [ERROR] Failed to create invoice row")
    sys.exit(1)

print(f"  [OK] Invoice row: {invoice_row_id}")

# Write price history
print("  [5b] Writing Price History...")
old_price = item.get('current_price') or item.get('Price per Pack') or 0
new_price = item.get('invoice_price', 0)
ok = write_price_history(
    base=base,
    sp_row_id=item.get('sp_row_id'),
    old_price=old_price,
    new_price=new_price,
    invoice_row_id=invoice_row_id,
    flagged_by="Test:manual",
)

if not ok:
    print(f"  [ERROR] Failed to write Price History")
    sys.exit(1)

print(f"  [OK] Price History row created")

# Update SP price
print("  [5c] Updating Supplier Product price...")
ok = update_sp_price(
    base=base,
    sp_row_id=item.get('sp_row_id'),
    new_price=item.get('invoice_price', 0),
)

if not ok:
    print(f"  [WARNING] SP price update had issues (check logs)")
else:
    new_price = item.get('invoice_price', 0)
    print(f"  [OK] SP price updated to RM{new_price:.2f}")

print()

# ─── STEP 6: Verify in Production ──────────────────────────────────────────
print("[STEP 6] Verification in Production")
print("-" * 80)

try:
    # Invoice row
    all_invoices = base.list_rows("Invoices")
    matching_inv = [r for r in all_invoices if (r.get("Invoice Number") or "").strip() == payload['invoice_number'].strip()]

    if matching_inv:
        inv = matching_inv[-1]
        print(f"[OK] Found Invoices row")
        print(f"  - Name: {inv.get('Name')}")
        print(f"  - Invoice Number: {inv.get('Invoice Number')}")
        print(f"  - Processed: {inv.get('Processed')}")
    else:
        print(f"[WARNING] Invoices row not found")

    # Price history
    all_history = base.list_rows("Price History")
    matching_hist = [r for r in all_history if r.get("Flagged By") == "Test:manual"]

    if matching_hist:
        hist = matching_hist[-1]
        print(f"[OK] Found Price History row")
        old = float(hist.get('Old Price') or 0)
        new = float(hist.get('New Price') or 0)
        chg = hist.get('Change %', '0')
        print(f"  - Old Price: RM{old:.2f}")
        print(f"  - New Price: RM{new:.2f}")
        print(f"  - Change %: {chg}%")
        print(f"  - Flagged By: {hist.get('Flagged By')}")
    else:
        print(f"[WARNING] Price History row not found")

    # Supplier Product
    all_sps = base.list_rows("Supplier Products")
    sp_row_id = item.get('sp_row_id')
    matching_sp = [r for r in all_sps if r.get("_id") == sp_row_id] if sp_row_id else []

    if matching_sp:
        sp = matching_sp[0]
        print(f"[OK] Found Supplier Product row")
        print(f"  - Name: {sp.get('Name')}")
        print(f"  - Code: {sp.get('Code')}")
        print(f"  - Price per Pack: RM{sp.get('Price per Pack', 0):.2f}")
        print(f"  - Date Updated: {sp.get('Date Updated')}")
    else:
        print(f"[WARNING] Supplier Product row not found")

except Exception as e:
    print(f"[ERROR] Verification failed: {e}")
    import traceback
    traceback.print_exc()

print()
print("=" * 80)
print("PRODUCTION TEST COMPLETE")
print("=" * 80)
