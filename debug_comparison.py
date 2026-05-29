#!/usr/bin/env python3
"""Debug: Check what build_comparison produces."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src" / "skills" / "parse_invoice"))

from step1_extract import extract_invoice
from step2_compare import build_comparison

invoice_path = Path(__file__).parent / "data" / "invoices_inbox" / "2026-05-27" / "seng_kong_fishery_cinv_1266_0526.jpg"

step1 = extract_invoice(str(invoice_path))
payloads = build_comparison(step1)

print("=" * 80)
print("BUILD_COMPARISON OUTPUT")
print("=" * 80)
print(json.dumps(payloads, indent=2, ensure_ascii=False, default=str))
