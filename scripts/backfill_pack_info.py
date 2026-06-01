"""
Backfill Pack Size / Unit Quantity / Unit of Measure for existing Supplier Products.

Scans rows where any of those three fields is empty AND the product name looks like
it contains embedded pack info. Calls LLM to suggest values, then sends Telegram
approval prompts.

Usage:
    python scripts/backfill_pack_info.py [--dry-run] [--supplier NAME] [--limit N]

    --dry-run     Print proposed changes as CSV, do NOT send Telegram messages.
    --supplier    Filter to a single supplier name (case-insensitive substring).
    --limit       Max number of Telegram prompts to send per run (default 10).
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Add parse_invoice to path so we can import helpers
_SKILL_DIR = Path(__file__).parent.parent / "src" / "skills" / "parse_invoice"
sys.path.insert(0, str(_SKILL_DIR))

BACKFILL_DIR = Path(__file__).parent.parent / "data" / "backfill_pending"
BACKFILL_DIR.mkdir(parents=True, exist_ok=True)

# Regex patterns that indicate pack info is embedded in the product name
# Imported from step2_compare to avoid duplication
from step2_compare import PACK_SIZE_PATTERNS

_PACK_RE = re.compile(
    "|".join(PACK_SIZE_PATTERNS) + r"|(\d+\s*[xX×*]\s*[\d.]+\s*[a-zA-Z]+)",
    re.IGNORECASE,
)


def _has_pack_info_in_name(name: str) -> bool:
    return bool(_PACK_RE.search(name or ""))


def _needs_backfill(row: dict) -> bool:
    missing = (
        not row.get("Pack Size")
        or row.get("Unit Quantity") is None
        or not row.get("Unit of Measure")
    )
    return missing and _has_pack_info_in_name(row.get("Supplier Product Name", ""))


def _load_all_sps(base, supplier_filter: str = None) -> list:
    rows = []
    start, page = 0, 1000
    while True:
        batch = base.list_rows("Supplier Products", start=start, limit=page)
        if not batch:
            break
        for row in batch:
            if supplier_filter:
                supplier_links = row.get("Supplier") or []
                names = []
                for sl in supplier_links:
                    if isinstance(sl, dict):
                        names.append(sl.get("display_value") or sl.get("row_id", ""))
                    else:
                        names.append(str(sl))
                if not any(supplier_filter.lower() in n.lower() for n in names):
                    continue
            rows.append(row)
        if len(batch) < page:
            break
        start += page
    return rows


def _classify_sp(sp_name: str, price_per_pack=None) -> dict:
    """Call LLM to extract pack fields. No invoice context — name only."""
    from setup_handler import _llm_classify_sp

    # Pass price as a weak hint (no invoice unit context available for backfill)
    invoice_unit_price = float(price_per_pack) if price_per_pack else None
    return _llm_classify_sp(
        sp_name,
        candidates=[],  # no ingredient matching needed for backfill
        invoice_unit=None,
        invoice_unit_price=invoice_unit_price,
    )


def _send_backfill_prompt(sp_row_id: str, sp_name: str, supplier_display: str, result: dict) -> bool:
    """Send a single Telegram approval prompt for a backfill item."""
    import requests

    token = os.getenv("SEATABLE_UPDATE_BOT_TOKEN")
    chat_id = os.getenv("SEATABLE_BOT_CHAT_ID", "-5150446443")

    pack_lines = []
    if result.get("pack_size"):
        pack_lines.append(f"  Pack Size: {result['pack_size']}")
    if result.get("unit_quantity") is not None:
        pack_lines.append(f"  Unit Qty: {result['unit_quantity']}")
    if result.get("unit_of_measure"):
        pack_lines.append(f"  UoM: {result['unit_of_measure']}")
    if result.get("whole_piece"):
        pack_lines.append("  (whole-piece item)")

    proposed = "\n".join(pack_lines) if pack_lines else "  (nothing to fill)"
    reasoning = result.get("pack_reasoning", "")

    text = (
        f"*Pack Info Backfill*\n"
        f"Supplier: {supplier_display}\n"
        f"Product: `{sp_name}`\n\n"
        f"*Proposed:*\n{proposed}\n"
        + (f"_Reason: {reasoning}_\n" if reasoning else "")
        + f"\nApply or skip?"
    )

    # callback_data must fit in 64 bytes:  "backfill_pack:{sp_row_id}" (~13 + ~30 = 43 bytes)
    keyboard = {"inline_keyboard": [[
        {"text": "✓ Apply", "callback_data": f"backfill_pack:{sp_row_id}"},
        {"text": "⏭ Skip", "callback_data": f"backfill_skip:{sp_row_id}"},
    ]]}

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "reply_markup": keyboard,
    })
    if not resp.ok:
        print(f"[WARNING] Telegram send failed: {resp.status_code} {resp.text}")
    return resp.ok


def main():
    parser = argparse.ArgumentParser(description="Backfill SP pack info via Telegram")
    parser.add_argument("--dry-run", action="store_true", help="Print CSV, no Telegram")
    parser.add_argument("--supplier", default=None, help="Filter by supplier name substring")
    parser.add_argument("--limit", type=int, default=10, help="Max Telegram prompts per run")
    args = parser.parse_args()

    from seatable_api import Base

    token = os.getenv("SEATABLE_API_TOKEN")
    url = os.getenv("SEATABLE_BASE_URL")
    if not token or not url:
        print("[ERROR] SEATABLE_API_TOKEN or SEATABLE_BASE_URL missing from .env")
        sys.exit(1)

    base = Base(token, url)
    base.auth()

    print("[INFO] Loading Supplier Products...")
    all_sps = _load_all_sps(base, supplier_filter=args.supplier)
    candidates = [r for r in all_sps if _needs_backfill(r)]

    print(f"[INFO] Found {len(all_sps)} SPs total, {len(candidates)} need backfill.")

    if not candidates:
        print("[INFO] Nothing to do.")
        return

    if args.dry_run:
        print("\n--- DRY RUN: proposed changes (no writes) ---")
        print("SP Name,Pack Size,Unit Qty,UoM,Reasoning")
        for row in candidates:
            name = row.get("Supplier Product Name", "")
            r = _classify_sp(name, row.get("Price per Pack"))
            print(
                f'"{name}",'
                f'"{r.get("pack_size","")}",'
                f'"{r.get("unit_quantity","")}",'
                f'"{r.get("unit_of_measure","")}",'
                f'"{r.get("pack_reasoning","")}"'
            )
            time.sleep(1)  # avoid Gemini rate limit
        return

    # Live run — confirm before sending
    print(f"\nAbout to send up to {min(args.limit, len(candidates))} Telegram prompts.")
    confirm = input("Type 'yes' to proceed: ").strip().lower()
    if confirm != "yes":
        print("[ABORT] Cancelled.")
        return

    sent = 0
    for row in candidates:
        if sent >= args.limit:
            print(f"[INFO] Limit of {args.limit} reached. Run again to continue.")
            break

        sp_row_id = row["_id"]
        sp_name = row.get("Supplier Product Name", "")

        # Get supplier display name from link cell
        supplier_links = row.get("Supplier") or []
        supplier_display = "Unknown"
        if supplier_links:
            first = supplier_links[0]
            supplier_display = first.get("display_value", "") if isinstance(first, dict) else str(first)

        print(f"[INFO] Classifying: {sp_name}")
        result = _classify_sp(sp_name, row.get("Price per Pack"))
        print(f"  → Pack: {result.get('pack_size')} | Qty: {result.get('unit_quantity')} | UoM: {result.get('unit_of_measure')}")

        # Save proposed values so approval_handler can apply them
        pending_path = BACKFILL_DIR / f"{sp_row_id}.json"
        uom = result.get("unit_of_measure")
        with open(pending_path, "w", encoding="utf-8") as f:
            json.dump({
                "sp_row_id": sp_row_id,
                "sp_name": sp_name,
                "pack_size": result.get("pack_size"),
                "unit_quantity": result.get("unit_quantity"),
                "unit_of_measure": uom.upper() if uom else None,
                "whole_piece": result.get("whole_piece", False),
            }, f, ensure_ascii=False, indent=2)

        ok = _send_backfill_prompt(sp_row_id, sp_name, supplier_display, result)
        if ok:
            sent += 1

        time.sleep(1)  # avoid Gemini + Telegram rate limits

    print(f"[INFO] Sent {sent} prompts. Approve them in Telegram (approval_handler must be running).")


if __name__ == "__main__":
    main()
