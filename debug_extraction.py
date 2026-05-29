#!/usr/bin/env python3
"""Debug: Check what Gemini extracted from the invoice."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src" / "skills" / "parse_invoice"))

from step1_extract import extract_invoice

invoice_path = Path(__file__).parent / "data" / "invoices_inbox" / "2026-05-27" / "seng_kong_fishery_cinv_1266_0526.jpg"

step1 = extract_invoice(str(invoice_path))

print("=" * 80)
print("RAW GEMINI EXTRACTION")
print("=" * 80)
print(json.dumps(step1, indent=2, ensure_ascii=False, default=str))
