#!/usr/bin/env python3
"""Diagnose where an invoice gets stuck in the pipeline."""
import json
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent / "src" / "skills" / "parse_invoice"))
sys.path.insert(0, str(Path(__file__).parent / "src" / "skills" / "invoice_intake"))

from dotenv import load_dotenv
load_dotenv()

print("INVOICE PIPELINE DIAGNOSTIC")
print("=" * 80)
print()

# Check if files were downloaded
inbox_dir = Path(__file__).parent / "data" / "invoices_inbox"
print(f"[1] Checking inbox directory: {inbox_dir}")
print()

if inbox_dir.exists():
    # Find most recent files
    recent_files = sorted(
        inbox_dir.rglob("*.*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )[:5]

    if recent_files:
        print(f"Most recent files ({len(recent_files)}):")
        for f in recent_files:
            age = datetime.now() - datetime.fromtimestamp(f.stat().st_mtime)
            age_str = f"{age.total_seconds():.0f}s ago"
            print(f"  - {f.name} ({age_str})")
        print()

        # Try to process the most recent
        print(f"[2] Processing most recent file: {recent_files[0].name}")
        print()

        from step1_extract import extract_invoice
        try:
            step1 = extract_invoice(str(recent_files[0]))
            if step1.get("status") == "success":
                invoices = step1.get("invoices", [])
                print(f"[OK] Extraction succeeded. Found {len(invoices)} invoice(s)")
                for inv in invoices:
                    print(f"  - {inv.get('invoice_number')} from {inv.get('supplier_name')}")
                    print(f"    Total: RM{inv.get('invoice_total', '?')}")
                    print(f"    Items: {len(inv.get('line_items', []))}")
                print()

                # Try comparison
                print(f"[3] Running comparison...")
                from step2_compare import build_comparison
                payloads = build_comparison(step1)
                print(f"[OK] Comparison generated {len(payloads)} payload(s)")
                for payload in payloads:
                    print(f"  - Invoice: {payload.get('invoice_number')}")
                    print(f"    Supplier: {payload.get('supplier_name')}")
                    print(f"    Supplier matched: {payload.get('supplier_matched')}")
                    print(f"    Price changes: {len(payload.get('price_changes', []))}")
                    print(f"    Confirm items: {len(payload.get('confirm_items', []))}")
                    print(f"    Unmatched items: {len(payload.get('unmatched_items', []))}")
                print()

                # Try notifier
                print(f"[4] Testing notifier...")
                from notifier import notify_invoice_comparison
                if payloads:
                    payload = payloads[0]
                    try:
                        result = notify_invoice_comparison(payload)
                        print(f"[OK] Notifier returned: {result}")
                    except Exception as e:
                        print(f"[ERROR] Notifier failed: {e}")
                        import traceback
                        traceback.print_exc()
                else:
                    print(f"[WARNING] No payloads to notify")

            else:
                print(f"[ERROR] Extraction failed: {step1.get('error_message')}")

        except Exception as e:
            print(f"[ERROR] Processing failed: {e}")
            import traceback
            traceback.print_exc()

    else:
        print(f"[WARNING] No files found in {inbox_dir}")
else:
    print(f"[ERROR] Inbox directory not found: {inbox_dir}")

print()
print("=" * 80)
print("DIAGNOSTIC COMPLETE")
