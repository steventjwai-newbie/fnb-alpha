#!/usr/bin/env python3
"""
Sandbox test: Process Seng Kong Fishery invoice end-to-end.
Uses SANDBOX tokens to isolate from production.
"""
import json
import os
import sys
from pathlib import Path
from datetime import datetime

# Setup paths
sys.path.insert(0, str(Path(__file__).parent / "src" / "skills" / "parse_invoice"))

from dotenv import load_dotenv
load_dotenv()

# Override with sandbox credentials
os.environ["SEATABLE_API_TOKEN"] = os.getenv("SEATABLE_API_TOKEN_SANDBOX")
os.environ["SEATABLE_BASE_URL"] = os.getenv("SEATABLE_BASE_URL_SANDBOX")

from step1_extract import extract_invoice
from step2_compare import build_comparison
from seatable_writer import (
    _base,
    upsert_invoice_row,
    add_row_link,
    write_price_history,
    update_sp_price,
)

print("=" * 80)
print("SANDBOX WORKFLOW TEST — Seng Kong Fishery Invoice CINV-1266-0526")
print("=" * 80)
print(f"Base URL: {os.environ['SEATABLE_BASE_URL']}")
print(f"Token: {os.environ['SEATABLE_API_TOKEN'][:10]}...")
print()

# ─── Step 1: Extract invoice from image ───────────────────────────────────────
invoice_path = Path(__file__).parent / "data" / "invoices_inbox" / "2026-05-27" / "seng_kong_fishery_cinv_1266_0526.jpg"

print(f"[1] Extracting from {invoice_path.name}")
print("-" * 80)

step1 = extract_invoice(str(invoice_path))
if step1.get("status") == "error":
    print(f"ERROR: Extraction failed: {step1.get('error_message')}")
    sys.exit(1)

invoices = step1.get("invoices", [])
if not invoices:
    print(f"ERROR: No invoices found")
    sys.exit(1)

invoice = invoices[0]
print(f"[OK] Supplier: {invoice.get('supplier_name')}")
print(f"[OK] Invoice #: {invoice.get('invoice_number')}")
print(f"[OK] Date: {invoice.get('invoice_date')}")
print(f"[OK] Items found: {len(invoice.get('line_items', []))}")
for item in invoice.get('line_items', []):
    print(f"  - {item.get('description', 'Unknown')}: {item.get('quantity')} {item.get('unit')}")
print()

# ─── Step 2: Build comparison (match to Supplier Products) ───────────────────
print(f"[2] Building comparison (matching to Supplier Products)")
print("-" * 80)

payloads = build_comparison(step1)
print(f"[OK] Generated {len(payloads)} payload(s)")
print()

for payload in payloads:
    print(f"Payload for invoice {payload['invoice_number']}:")
    print(f"  Supplier: {payload['supplier_name']} (ID: {payload.get('supplier_row_id', 'NOT_FOUND')})")
    print(f"  Price changes: {len(payload.get('price_changes', []))}")
    print(f"  Confirm items: {len(payload.get('confirm_items', []))}")

    for i, item in enumerate(payload.get('price_changes', []), 1):
        print(f"    [{i}] {item.get('sp_code')} | {item['description']}")
        print(f"        Current: RM{item['current_price']:.2f} -> Invoice: RM{item['invoice_price']:.2f}")
        print(f"        Change: {item.get('price_change_pct', 0):.1f}%")
        print(f"        Status: {item.get('match_score_tier', 'unknown')}")

    for i, item in enumerate(payload.get('confirm_items', []), 1):
        print(f"    [{i}] CONFIRM: {item.get('description')}")
        print(f"        {item.get('reason', 'No reason given')}")

    print()

if not payloads:
    print("[WARNING] No actionable items found")
    sys.exit(1)

# ─── Step 3: Simulate Telegram notification ────────────────────────────────
print(f"[3] What would be sent to Telegram")
print("-" * 80)
payload = payloads[0]  # Use first payload for demo

# Build message like notifier would
msg_lines = [
    f"{payload['invoice_number']} — {payload['supplier_name']}",
    f"Date: {payload['invoice_date']}",
    "",
]

items = payload.get('price_changes', []) + payload.get('confirm_items', [])
if items:
    for i, item in enumerate(items, 1):
        if '_category' not in item or item['_category'] == 'price_changes':
            old_p = item.get('current_price', 0)
            new_p = item.get('invoice_price', 0)
            pct = ((new_p - old_p) / old_p * 100) if old_p else 0
            msg_lines.append(f"[{i}] {item.get('sp_code', '?')} {item.get('description', '?')}")
            msg_lines.append(f"    RM{old_p:.2f} -> RM{new_p:.2f} ({pct:+.1f}%)")
        else:
            msg_lines.append(f"[{i}] CONFIRM: {item.get('description', '?')}")
            msg_lines.append(f"    {item.get('reason', 'confirm')}")
    msg_lines.append("")
    msg_lines.append("Use buttons to approve, reject, or skip")

print("\n".join(msg_lines))
print()

# ─── Step 4: Simulate approval ────────────────────────────────────────────
print(f"[4] Simulating approval (auto-yes on first price change)")
print("-" * 80)

# Prepare approval data
payload["invoice_file_path"] = str(invoice_path)

# Find price_changes items
price_changes = payload.get('price_changes', [])
if price_changes:
    item = price_changes[0]
    print(f"Approving: {item['sp_code']} | {item['description']}")
    print(f"  Old price: RM{item['current_price']:.2f}")
    print(f"  New price: RM{item['invoice_price']:.2f}")
    print()

    # ─── Write to Seatable ────────────────────────────────────────────────
    print(f"[5] Writing to Seatable (Sandbox)")
    print("-" * 80)

    base = _base()

    # Step 5a: Upsert Invoice row
    print("  [5a] Creating Invoices row...")
    invoice_row_id = upsert_invoice_row(
        base=base,
        invoice_number=payload['invoice_number'],
        supplier_name=payload['supplier_name'],
        supplier_row_id=payload.get('supplier_row_id', ''),
        invoice_date=payload.get('invoice_date', ''),
    )

    if invoice_row_id:
        print(f"  [OK] Invoice row created: {invoice_row_id}")
    else:
        print(f"  [ERROR] Failed to create invoice row")
        sys.exit(1)

    # Step 5b: Write Price History
    print("  [5b] Writing Price History row...")
    ok = write_price_history(
        base=base,
        sp_row_id=item['sp_row_id'],
        old_price=item['current_price'],
        new_price=item['invoice_price'],
        invoice_row_id=invoice_row_id,
        flagged_by="Auto:sandbox_test",
    )

    if ok:
        print(f"  [OK] Price History row created")
    else:
        print(f"  [ERROR] Failed to write Price History")
        sys.exit(1)

    # Step 5c: Update Supplier Product price
    print("  [5c] Updating Supplier Product price...")
    ok = update_sp_price(
        base=base,
        sp_row_id=item['sp_row_id'],
        new_price=item['invoice_price'],
    )

    if ok:
        print(f"  [OK] SP price updated to RM{item['invoice_price']:.2f}")
    else:
        print(f"  [WARNING] SP price update had issues (but Price History written)")

    print()

# ─── Verification ─────────────────────────────────────────────────────────
print(f"[6] Verification in Sandbox")
print("-" * 80)

try:
    # Read back the invoice row
    all_invoices = base.list_rows("Invoices")
    matching = [r for r in all_invoices if (r.get("Invoice Number") or "").strip() == payload['invoice_number'].strip()]

    if matching:
        inv = matching[0]
        print(f"[OK] Found Invoices row: {inv.get('Name')}")
        print(f"  - Invoice Number: {inv.get('Invoice Number')}")
        print(f"  - Processed: {inv.get('Processed')}")
    else:
        print(f"[WARNING] Could not find Invoices row (check Seatable)")

    # Read back the price history row
    all_history = base.list_rows("Price History")
    recent = [r for r in all_history if r.get("Flagged By") == "Auto:sandbox_test"]

    if recent:
        hist = recent[-1]  # Latest
        print(f"[OK] Found Price History row:")
        print(f"  - Old Price: RM{hist.get('Old Price', 0):.2f}")
        print(f"  - New Price: RM{hist.get('New Price', 0):.2f}")
        print(f"  - Change %: {hist.get('Change %', 0):.1f}%")
        print(f"  - Flagged By: {hist.get('Flagged By')}")
    else:
        print(f"[WARNING] Could not find Price History row (check Seatable)")

except Exception as e:
    print(f"[WARNING] Verification error: {e}")

print()
print("=" * 80)
print("SANDBOX TEST COMPLETE")
print("=" * 80)
print()
print("Next steps:")
print("1. Check Seatable sandbox base for new Invoices row")
print("2. Check Price History for new row with RM amounts")
print("3. Check Supplier Product for updated price")
print("4. Run full intake_listener.py + approval_handler.py in production if ready")
