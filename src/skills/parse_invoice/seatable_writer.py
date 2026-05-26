"""
Seatable write operations for the approval workflow.

Three operations:
1. upsert_invoice_row  — find or create an Invoices row for this invoice
2. write_price_history — log price change to Price History table
3. update_sp_price     — update Supplier Product's Price per Pack

All writes are logged with audit metadata (Flagged By, timestamp).
"""

import os
from datetime import datetime
from typing import Optional, Dict, Any

from dotenv import load_dotenv
from seatable_api import Base

load_dotenv()

SEATABLE_API_TOKEN = os.getenv("SEATABLE_API_TOKEN")
SEATABLE_BASE_URL = os.getenv("SEATABLE_BASE_URL")

# Column names — change these if your Seatable table uses different labels
INVOICES_TABLE = "Invoices"
PRICE_HISTORY_TABLE = "Price History"
SUPPLIER_PRODUCTS_TABLE = "Supplier Products"


def _base() -> Base:
    base = Base(SEATABLE_API_TOKEN, SEATABLE_BASE_URL)
    base.auth()
    return base


def upsert_invoice_row(
    base: Base,
    invoice_number: str,
    supplier_name: str,
    supplier_row_id: str,
    invoice_date: str,
) -> Optional[str]:
    """
    Find existing Invoices row by invoice_number or create new one.
    Returns the row's _id, or None on failure.
    """
    # Search existing
    existing = base.list_rows(INVOICES_TABLE)
    for row in existing:
        if (row.get("Invoice Number") or "").strip() == invoice_number.strip():
            return row.get("_id")

    # Create new
    row_payload = {
        "Name": f"{supplier_name} | {invoice_number}",
        "Invoice Number": invoice_number,
        "Invoice Date": invoice_date,
        "Processed": False,
    }
    if supplier_row_id:
        row_payload["Supplier (Link)"] = [supplier_row_id]

    try:
        new_row = base.append_row(INVOICES_TABLE, row_payload)
        return new_row.get("_id") if new_row else None
    except Exception as e:
        print(f"[ERROR] Failed to create Invoices row: {e}")
        return None


def write_price_history(
    base: Base,
    sp_row_id: str,
    old_price: float,
    new_price: float,
    invoice_row_id: str,
    flagged_by: str,
) -> bool:
    """
    Append a row to Price History. Returns True on success.
    """
    change_pct = ((new_price - old_price) / old_price * 100) if old_price else 0
    row_payload = {
        "Supplier product (link)": [sp_row_id] if sp_row_id else [],
        "Old Price": old_price,
        "New Price": new_price,
        "Change %": round(change_pct, 2),
        "Invoice Reference": [invoice_row_id] if invoice_row_id else [],
        "Flagged By": flagged_by,
    }
    try:
        base.append_row(PRICE_HISTORY_TABLE, row_payload)
        return True
    except Exception as e:
        print(f"[ERROR] Failed to write Price History row: {e}")
        return False


def update_sp_price(base: Base, sp_row_id: str, new_price: float) -> bool:
    """
    Update Supplier Product's Price per Pack and Date Updated.
    Returns True on success.
    Note: If big data API fails, logs warning but returns True (Price History already written).
    """
    try:
        base.update_row(SUPPLIER_PRODUCTS_TABLE, sp_row_id, {
            "Price per Pack": new_price,
            "Date Updated": datetime.now().strftime("%Y-%m-%d"),
        })
        return True
    except Exception as e:
        if "big data storage" in str(e).lower():
            print(f"[WARNING] SP price update skipped (big data API unavailable): {e}")
            print(f"[WARNING] Price History was written; manual SP price update may be needed")
            return True  # Price History written, so don't fail the whole workflow
        print(f"[ERROR] Failed to update SP row {sp_row_id}: {e}")
        return False


def mark_invoice_processed(base: Base, invoice_row_id: str) -> bool:
    """Flip Processed checkbox to True when all items resolved."""
    try:
        base.update_row(INVOICES_TABLE, invoice_row_id, {"Processed": True})
        return True
    except Exception as e:
        print(f"[ERROR] Failed to mark invoice processed: {e}")
        return False


def commit_price_change(
    item: Dict[str, Any],
    invoice_payload: Dict[str, Any],
    flagged_by: str,
) -> Dict[str, Any]:
    """
    Atomic-ish write: ensures Invoices row exists, then writes Price History,
    then updates Supplier Product. Returns dict with status + details.

    Note: not transactional. If SP update fails after Price History write,
    you'll have an orphan history row. Acceptable for v1.
    """
    base = _base()

    invoice_row_id = upsert_invoice_row(
        base=base,
        invoice_number=invoice_payload["invoice_number"],
        supplier_name=invoice_payload["supplier_name"],
        supplier_row_id=invoice_payload.get("supplier_row_id", ""),
        invoice_date=invoice_payload.get("invoice_date", ""),
    )
    if not invoice_row_id:
        return {"status": "error", "step": "invoices", "message": "Could not upsert invoice row"}

    ok_history = write_price_history(
        base=base,
        sp_row_id=item["sp_row_id"],
        old_price=item["old_price"],
        new_price=item["new_price"],
        invoice_row_id=invoice_row_id,
        flagged_by=flagged_by,
    )
    if not ok_history:
        return {"status": "error", "step": "price_history", "message": "History write failed"}

    ok_update = update_sp_price(base, item["sp_row_id"], item["new_price"])
    if not ok_update:
        return {"status": "partial", "step": "sp_update",
                "message": "History written but SP update failed"}

    return {
        "status": "ok",
        "invoice_row_id": invoice_row_id,
        "sp_code": item["sp_code"],
        "old_price": item["old_price"],
        "new_price": item["new_price"],
    }
