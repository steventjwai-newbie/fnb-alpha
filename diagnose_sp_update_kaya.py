"""
Diagnostic: test update_sp_price on the production base against a known-safe SP.

Target: 'Kaya puff' from Yin's Sourdough Bakery (SP-00224)
  - Currently has Price per Pack = 0
  - Not used in any recipe
  - Safe to write a test value, then reset

This script:
  1. Reads current price (should be 0/None)
  2. Tries SDK update_row with Price per Pack = 9.99
  3. If SDK fails, tries direct HTTP PUT
  4. Reads back to verify which (if any) succeeded
  5. Resets to 0 if write succeeded (cleanup)

Run with PRODUCTION .env loaded. NOT sandbox.
"""

import os
import sys

import requests
from dotenv import load_dotenv
from seatable_api import Base

load_dotenv()

TOKEN = os.getenv("SEATABLE_API_TOKEN")
BASE_URL = os.getenv("SEATABLE_BASE_URL") or os.getenv("SEATABLE_SERVER_URL")

if not TOKEN or not BASE_URL:
    sys.exit("[ERROR] Missing SEATABLE_API_TOKEN or SEATABLE_BASE_URL in .env")

SP_CODE_TARGET = "SP-00224"          # Kaya puff
TEST_PRICE = 9.99                     # arbitrary test value
RESET_PRICE = 0                       # restore after


def find_kaya_puff(base: Base):
    """Find the Kaya puff SP row by SP Code."""
    # Paginated to be safe
    rows = []
    start = 0
    while True:
        batch = base.list_rows("Supplier Products", start=start, limit=1000)
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < 1000:
            break
        start += 1000

    for row in rows:
        if row.get("Supplier Product Code") == SP_CODE_TARGET:
            return row
    return None


def try_sdk_update(base: Base, row_id: str, new_price: float) -> dict:
    """Path 1: standard SDK call (currently failing on sandbox)."""
    try:
        base.update_row("Supplier Products", row_id, {
            "Price per Pack": new_price,
        })
        return {"success": True, "method": "SDK update_row"}
    except Exception as e:
        return {"success": False, "method": "SDK update_row", "error": str(e)}


def try_direct_http(base: Base, row_id: str, new_price: float) -> dict:
    """
    Path 2: bypass SDK routing, PUT directly to dtable-server endpoint.
    Tries common URL patterns; reports which (if any) returns 200.
    """
    # Inspect what the Base object exposes
    candidate_urls = []
    for attr in ("dtable_server_url", "server_url", "_dtable_server_url"):
        val = getattr(base, attr, None)
        if val:
            candidate_urls.append(val)

    # Token sources
    candidate_tokens = []
    for attr in ("jwt_token", "access_token", "_access_token", "dtable_access_token"):
        val = getattr(base, attr, None)
        if val:
            candidate_tokens.append((attr, val))

    print(f"   Detected server URLs: {candidate_urls}")
    print(f"   Detected token attrs: {[t[0] for t in candidate_tokens]}")
    print(f"   Detected base uuid:   {getattr(base, 'dtable_uuid', None)}")

    if not candidate_urls or not candidate_tokens:
        return {"success": False, "method": "direct HTTP",
                "error": "Couldn't find server URL or token on Base object"}

    server_url = candidate_urls[0]
    token_name, token_val = candidate_tokens[0]
    base_uuid = getattr(base, "dtable_uuid", None)

    # Try the standard dtable-server REST endpoint
    url = f"{server_url}/api/v1/dtables/{base_uuid}/rows/"
    headers = {
        "Authorization": f"Token {token_val}",
        "Content-Type": "application/json",
    }
    payload = {
        "table_name": "Supplier Products",
        "row_id": row_id,
        "row": {"Price per Pack": new_price},
    }
    try:
        r = requests.put(url, headers=headers, json=payload, timeout=10)
        return {
            "success": r.status_code < 400,
            "method": f"direct HTTP PUT to {url}",
            "status": r.status_code,
            "response": r.text[:500],
        }
    except Exception as e:
        return {"success": False, "method": "direct HTTP", "error": str(e)}


def read_current_price(base: Base, row_id: str):
    """Re-read the row to confirm what's stored now."""
    row = base.get_row("Supplier Products", row_id)
    return row.get("Price per Pack") if row else None


def main():
    print(f"[diag] Connecting to production base…")
    print(f"[diag] URL: {BASE_URL}")

    base = Base(TOKEN, BASE_URL)
    base.auth()

    print(f"\n[diag] Locating {SP_CODE_TARGET}…")
    kaya = find_kaya_puff(base)
    if not kaya:
        sys.exit(f"[ERROR] SP {SP_CODE_TARGET} not found. Make sure you're on production .env")

    row_id = kaya["_id"]
    initial_price = kaya.get("Price per Pack")
    print(f"[diag] Found: {kaya.get('Supplier Product Name')!r} | row_id={row_id}")
    print(f"[diag] Current Price per Pack: {initial_price!r}")
    if initial_price not in (None, 0, 0.0):
        print(f"[WARN] Expected price 0 but got {initial_price}. "
              f"Pick a different test SP or verify this is safe.")
        confirm = input("Continue anyway? (y/N): ")
        if confirm.strip().lower() != "y":
            sys.exit("[diag] Aborted by user.")

    print(f"\n[diag] === Attempt 1: SDK update_row ===")
    r1 = try_sdk_update(base, row_id, TEST_PRICE)
    print(f"   {r1}")

    if r1["success"]:
        readback = read_current_price(base, row_id)
        print(f"   Readback: Price per Pack = {readback}")
        if readback == TEST_PRICE:
            print("\n[diag] [OK] SDK update_row WORKS on production.")
            print("[diag]   Conclusion: sandbox-only issue. Recreate sandbox base.")
            # cleanup
            base.update_row("Supplier Products", row_id, {"Price per Pack": RESET_PRICE})
            print(f"[diag] Reset price back to {RESET_PRICE}.")
            return

    print(f"\n[diag] === Attempt 2: direct HTTP PUT ===")
    r2 = try_direct_http(base, row_id, TEST_PRICE)
    print(f"   {r2}")

    if r2["success"]:
        readback = read_current_price(base, row_id)
        print(f"   Readback: Price per Pack = {readback}")
        if readback == TEST_PRICE:
            print("\n[diag] [OK] Direct HTTP PUT WORKS, SDK does NOT.")
            print("[diag]   Conclusion: SDK auto-routes through big data. "
                  "Patch seatable_writer.update_sp_price to use direct HTTP.")
            # cleanup
            try:
                base.update_row("Supplier Products", row_id, {"Price per Pack": RESET_PRICE})
                print(f"[diag] Reset price back to {RESET_PRICE}.")
            except Exception:
                print(f"[diag] (Couldn't auto-reset via SDK — set manually back to 0)")
            return

    print("\n[diag] ✗ Both methods failed on production.")
    print("[diag]   Conclusion: this is a Seatable plan limitation, not a code issue.")
    print("[diag]   Options:")
    print("[diag]     1. Upgrade to Seatable Pro (~$15-19/mo) for big-data API access")
    print("[diag]     2. Workaround B: redesign so SP price derives from Price History via Formula column")
    print("[diag]     3. Manual Seatable UI updates after each price change")


if __name__ == "__main__":
    main()
