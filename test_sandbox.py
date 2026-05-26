"""
Test sandbox approval workflow end-to-end.

Uses the cached Mooi Tian parsed invoice to test the full pipeline (step2 → notifier)
against the sandbox Seatable base WITHOUT burning Gemini quota on re-extraction.

Run this AFTER:
1. setup_sandbox.py has seeded data
2. approval_handler.py is running in a separate terminal
3. SEATABLE_UPDATE_BOT_TOKEN is set in .env

Usage:
    python test_sandbox.py [path-to-parsed-json]

Default: data/parsed_results/2026-05-25/INV027448_20260525_073001.json (Mooi Tian)
"""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ============================================================
# Override env vars to sandbox BEFORE importing step2_compare
# (it reads SEATABLE_API_TOKEN at module load time as a global)
# ============================================================

SANDBOX_TOKEN = os.getenv("SEATABLE_API_TOKEN_SANDBOX")
SANDBOX_URL = os.getenv("SEATABLE_BASE_URL_SANDBOX")

if not SANDBOX_TOKEN:
    print("[ERROR] SEATABLE_API_TOKEN_SANDBOX not in .env")
    sys.exit(1)

os.environ["SEATABLE_API_TOKEN"] = SANDBOX_TOKEN
os.environ["SEATABLE_BASE_URL"] = SANDBOX_URL

print(f"[TEST] Using sandbox base: {SANDBOX_URL}")

# NOW import step2_compare (it will pick up the overridden env vars)
sys.path.insert(0, str(Path(__file__).parent / "src" / "skills" / "parse_invoice"))
from step2_compare import build_comparison
from notifier import notify_invoice_comparison


def main():
    # Load parsed invoice JSON (skip step1 re-extraction to save Gemini quota)
    if len(sys.argv) > 1:
        parsed_path = sys.argv[1]
    else:
        parsed_path = "data/parsed_results/2026-05-25/INV027448_20260525_073001.json"

    if not Path(parsed_path).exists():
        print(f"[ERROR] File not found: {parsed_path}")
        sys.exit(1)

    print(f"[TEST] Loading: {parsed_path}")
    with open(parsed_path, "r", encoding="utf-8") as f:
        step1_result = json.load(f)

    if step1_result.get("status") != "success":
        print(f"[ERROR] Parsed JSON status is not success: {step1_result.get('error_message')}")
        sys.exit(1)

    # Run step2: build_comparison
    print(f"\n[TEST] Running step2_compare against sandbox...")
    payloads = build_comparison(step1_result)

    print(f"[TEST] Generated {len(payloads)} payload(s)")

    # Send each payload to Telegram (notifier now includes buttons)
    for i, payload in enumerate(payloads, 1):
        print(f"\n[TEST] Payload {i}: {payload['invoice_number']} from {payload['supplier_name']}")

        # Count items by tier
        auto = len(payload.get("matched_items", []))
        changes = len(payload.get("price_changes", []))
        confirm = len(payload.get("confirm_items", []))
        unmatched = len(payload.get("unmatched_items", []))

        print(f"       ✅ auto-matched: {auto}")
        print(f"       💰 price_changes: {changes}")
        print(f"       🤔 confirm_items: {confirm}")
        print(f"       ⚠️ unmatched: {unmatched}")

        # This call now includes save_pending + reply_markup (buttons)
        result = notify_invoice_comparison(payload)
        if result:
            print(f"       ✓ Telegram message sent with {changes + confirm} action button(s)")
        else:
            print(f"       ✗ Failed to send Telegram message")

    print(f"\n[TEST] Done. Check Telegram for {len(payloads)} message(s) with buttons.")
    print(f"[TEST] To approve an item, click the ✓ button. Check sandbox Seatable for Price History row.")


if __name__ == "__main__":
    main()
