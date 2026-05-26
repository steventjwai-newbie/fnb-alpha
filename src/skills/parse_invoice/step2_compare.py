"""
Step 2: Compare extracted invoice items against Seatable Supplier Products.

Categories:
- matched_items:     score ≥95%, price unchanged
- price_changes:     score ≥95%, ≤30% diff, measurement units → auto-tier (still needs /yes)
- confirm_items:     score 60-94%, OR diff >30%, OR container unit assumed → /yes required
- unmatched_items:   score <60% (L2/LLM target)
- data_gaps:         high match but Seatable row missing fields
- unit_mismatches:   high match but units truly incompatible (mass vs volume)
"""

import os
import re
import sys
import unicodedata
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from rapidfuzz import fuzz, process
from seatable_api import Base

load_dotenv()

_here = str(Path(__file__).parent)
if _here not in sys.path:
    sys.path.insert(0, _here)

from unit_normalizer import normalize_invoice_price, _clean_uom

SEATABLE_API_TOKEN = os.getenv("SEATABLE_API_TOKEN")
SEATABLE_BASE_URL = os.getenv("SEATABLE_BASE_URL")

# Match tiers
AUTO_ACCEPT = 95
CONFIRM = 80
SHOW_CANDIDATES = 60
SUPPLIER_THRESHOLD = 70

# Magnitude safeguard
PRICE_CHANGE_SANITY_CAP_PCT = 30.0

# Column name where you stored your SP-00000 auto-number
# CHANGE THIS to match your actual Seatable column label
SP_CODE_COLUMN = "SP Code"
ING_CODE_COLUMN = "Ingredient Code"

# Container unit words on invoices — treated as "supplier pack" by assumption
CONTAINER_UNITS = {
    "pack", "pkt", "pkg", "packet",
    "ctn", "carton", "case", "box",
    "btl", "bottle",
    "pcs", "pc", "piece", "unit", "ea", "each",
    "doz", "dozen",
    "bag", "sack",
    "tin", "can",
    "tray", "punnet", "roll",
    "biji",  # Bahasa for "piece"
}

PACK_SIZE_PATTERNS = [
    r"\(\s*\d+\s*[x×*]\s*[\d.]+\s*[a-zA-Z]+\s*\)",
    r"\(\s*\d+\s*[a-zA-Z]+\s*/\s*[a-zA-Z]+\s*\)",
    r"\b\d+\s*(?:kg|ml|g|l|gr|gram|liter|litre|kilo)\b",
]


def _norm(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s).lower().strip()
    for pat in PACK_SIZE_PATTERNS:
        s = re.sub(pat, "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _is_container_unit(unit_str: str) -> bool:
    if not unit_str:
        return False
    return _clean_uom(unit_str) in CONTAINER_UNITS


_suppliers_cache: Optional[List[Dict]] = None
_products_cache: Optional[List[Dict]] = None


def _seatable_base() -> Base:
    base = Base(SEATABLE_API_TOKEN, SEATABLE_BASE_URL)
    base.auth()
    return base


def _list_rows_paginated(base: Base, table: str, page_size: int = 1000) -> List[Dict]:
    rows = []
    start = 0
    while True:
        batch = base.list_rows(table, start=start, limit=page_size)
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
    return rows


def _load_suppliers(base: Base) -> List[Dict]:
    global _suppliers_cache
    if _suppliers_cache is None:
        _suppliers_cache = _list_rows_paginated(base, "Suppliers")
        print(f"[LOG] Loaded {len(_suppliers_cache)} suppliers")
    return _suppliers_cache


def _load_products(base: Base) -> List[Dict]:
    global _products_cache
    if _products_cache is None:
        _products_cache = _list_rows_paginated(base, "Supplier Products")
        print(f"[LOG] Loaded {len(_products_cache)} products")
    return _products_cache


def _match_supplier(supplier_name: str, suppliers: List[Dict]) -> Optional[Dict]:
    names = [r.get("Supplier Name", "") for r in suppliers]
    norm_names = [_norm(n) for n in names]
    target = _norm(supplier_name)
    if not target or not norm_names:
        return None
    results = process.extract(target, norm_names, scorer=fuzz.token_set_ratio, limit=1)
    if not results or results[0][1] < SUPPLIER_THRESHOLD:
        return None
    _, _, idx = results[0]
    return suppliers[idx]


def _get_linked_ids(cell_value) -> List[str]:
    if not cell_value or not isinstance(cell_value, list):
        return []
    ids = []
    for item in cell_value:
        if isinstance(item, dict):
            row_id = item.get("row_id") or item.get("_id") or ""
            if row_id:
                ids.append(row_id)
    return ids


def _filter_products_by_supplier(products: List[Dict], supplier_id: str) -> List[Dict]:
    return [p for p in products if supplier_id in _get_linked_ids(p.get("Supplier"))]


def _make_candidates(results: list) -> List[Dict]:
    return [{"name": name, "score": round(score, 1)} for name, score, _ in results[:3]]


def _sp_ref(matched_product: Dict) -> str:
    """Stable reference: prefer auto-number SP Code, fall back to truncated _id."""
    return matched_product.get(SP_CODE_COLUMN) or matched_product.get("_id", "")[:8]


def build_comparison(step1_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    if step1_result.get("status") != "success":
        return []

    base = _seatable_base()
    suppliers = _load_suppliers(base)
    all_products = _load_products(base)
    all_product_names = [p.get("Supplier Product Name", "") for p in all_products]
    norm_all_product_names = [_norm(n) for n in all_product_names]

    payloads = []

    for invoice in step1_result.get("invoices", []):
        supplier_name = invoice.get("supplier_name") or ""
        invoice_number = invoice.get("invoice_number") or ""
        invoice_date = invoice.get("invoice_date") or ""

        matched_supplier = _match_supplier(supplier_name, suppliers)
        supplier_matched = matched_supplier is not None
        supplier_row_id = matched_supplier.get("_id", "") if supplier_matched else ""

        if supplier_matched:
            supplier_products = _filter_products_by_supplier(all_products, supplier_row_id)
            product_names = [p.get("Supplier Product Name", "") for p in supplier_products]
            norm_product_names = [_norm(n) for n in product_names]
            supplier_candidates = []
            print(f"[LOG] Supplier '{supplier_name}' -> matched. {len(supplier_products)} products in scope.")
        else:
            supplier_products = []
            product_names = []
            norm_product_names = []
            supplier_names = [s.get("Supplier Name", "") for s in suppliers]
            norm_supplier_names = [_norm(n) for n in supplier_names]
            target = _norm(supplier_name)
            top3 = process.extract(target, norm_supplier_names, scorer=fuzz.token_set_ratio, limit=3) if norm_supplier_names else []
            supplier_candidates = _make_candidates(top3)

        matched_items = []
        price_changes = []
        confirm_items = []
        unmatched_items = []
        data_gaps = []
        unit_mismatches = []

        for item in invoice.get("line_items", []):
            product_name = item.get("product_name") or ""
            invoice_unit = item.get("unit") or ""
            invoice_unit_price = item.get("unit_price")

            if not product_name:
                continue

            if not supplier_matched:
                target = _norm(product_name)
                top3 = process.extract(target, norm_all_product_names, scorer=fuzz.token_set_ratio, limit=3) if norm_all_product_names else []
                unmatched_items.append({"product_name": product_name, "candidates": _make_candidates(top3)})
                continue

            if not product_names:
                unmatched_items.append({"product_name": product_name, "candidates": []})
                continue

            target = _norm(product_name)
            results = process.extract(target, norm_product_names, scorer=fuzz.token_set_ratio, limit=3)

            if not results:
                unmatched_items.append({"product_name": product_name, "candidates": []})
                continue

            top_score = results[0][1]

            if top_score < SHOW_CANDIDATES:
                unmatched_items.append({"product_name": product_name, "candidates": _make_candidates(results)})
                continue

            _, match_score, idx = results[0]
            best_name = product_names[idx]
            matched_product = supplier_products[idx]
            sp_code = _sp_ref(matched_product)
            sp_row_id = matched_product.get("_id", "")

            old_price = matched_product.get("Price per Pack")
            supplier_unit_qty = matched_product.get("Unit Quantity")
            supplier_uom = matched_product.get("Unit of Measure") or ""

            # Data gap check
            missing = []
            if old_price is None:
                missing.append("Price per Pack")
            if not supplier_unit_qty:
                missing.append("Unit Quantity")
            if not supplier_uom:
                missing.append("Unit of Measure")
            if invoice_unit_price is None:
                missing.append("invoice price (parsing)")
            if missing:
                data_gaps.append({
                    "product_name": product_name,
                    "seatable_product": best_name,
                    "sp_code": sp_code,
                    "sp_row_id": sp_row_id,
                    "match_score": round(match_score, 1),
                    "missing": missing,
                })
                continue

            # Unit handling: two paths
            container_assumed = False
            if _is_container_unit(invoice_unit):
                # Container unit on invoice → assume 1 invoice unit = 1 supplier pack
                # Compare directly to Price per Pack
                new_price_per_pack = Decimal(str(invoice_unit_price))
                container_assumed = True
            else:
                # Measurement unit → real conversion
                new_price_per_pack = normalize_invoice_price(
                    invoice_unit_price, invoice_unit,
                    supplier_unit_qty, supplier_uom,
                )

            if new_price_per_pack is None:
                unit_mismatches.append({
                    "product_name": product_name,
                    "seatable_product": best_name,
                    "sp_code": sp_code,
                    "sp_row_id": sp_row_id,
                    "match_score": round(match_score, 1),
                    "invoice_unit": invoice_unit,
                    "supplier_uom": supplier_uom,
                })
                continue

            new_price = float(new_price_per_pack)
            old_price_f = float(old_price)
            diff_pct = abs(new_price - old_price_f) / old_price_f * 100 if old_price_f else 0
            price_changed = abs(new_price - old_price_f) > 0.001

            item_payload = {
                "product_name": product_name,
                "seatable_product": best_name,
                "sp_code": sp_code,
                "sp_row_id": sp_row_id,
                "match_score": round(match_score, 1),
                "old_price": old_price_f,
                "new_price": new_price,
                "unit": invoice_unit,
                "diff_pct": round(diff_pct, 1),
            }

            if not price_changed:
                matched_items.append({
                    "product_name": product_name,
                    "seatable_product": best_name,
                    "sp_code": sp_code,
                    "sp_row_id": sp_row_id,
                    "price": old_price_f,
                    "unit": invoice_unit,
                })
            elif container_assumed:
                # Container unit assumed = pack. Always /yes required.
                item_payload["container_assumed"] = True
                item_payload["candidates"] = _make_candidates(results)
                confirm_items.append(item_payload)
            elif match_score < AUTO_ACCEPT or diff_pct > PRICE_CHANGE_SANITY_CAP_PCT:
                item_payload["candidates"] = _make_candidates(results)
                if diff_pct > PRICE_CHANGE_SANITY_CAP_PCT:
                    item_payload["magnitude_flag"] = True
                confirm_items.append(item_payload)
            else:
                price_changes.append(item_payload)

        payloads.append({
            "invoice_number": invoice_number,
            "supplier_name": supplier_name,
            "supplier_row_id": supplier_row_id,
            "invoice_date": invoice_date,
            "supplier_matched": supplier_matched,
            "supplier_candidates": supplier_candidates,
            "matched_items": matched_items,
            "price_changes": price_changes,
            "confirm_items": confirm_items,
            "unmatched_items": unmatched_items,
            "data_gaps": data_gaps,
            "unit_mismatches": unit_mismatches,
            "handwriting": invoice.get("handwriting_content"),
            "has_handwriting": bool(invoice.get("has_handwriting")),
        })

    return payloads


def format_telegram_message(payload: Dict[str, Any]) -> str:
    lines = [f"📋 {payload['invoice_number']} | {payload['supplier_name']} | {payload['invoice_date']}"]

    if payload.get("matched_items"):
        lines.append(f"\n✅ *Auto-matched ({len(payload['matched_items'])}):*")
        for m in payload["matched_items"]:
            lines.append(f"  • {m['product_name']} · RM{m['price']:.2f}/{m['unit']}")

    item_idx = 0

    if payload.get("price_changes"):
        lines.append(f"\n💰 *Price changes ({len(payload['price_changes'])}):*")
        for ch in payload["price_changes"]:
            item_idx += 1
            arrow = "↑" if ch["new_price"] > ch["old_price"] else "↓"
            lines.append(f"  {item_idx}. {ch['product_name']} [{ch['sp_code']}] ({ch['match_score']}%)")
            lines.append(f"     RM{ch['old_price']:.2f} → RM{ch['new_price']:.2f} {arrow} ({ch['diff_pct']}%)")

    if payload.get("confirm_items"):
        lines.append(f"\n🤔 *Confirm match ({len(payload['confirm_items'])}):*")
        for c in payload["confirm_items"]:
            item_idx += 1
            flags = []
            if c.get("container_assumed"):
                flags.append("📦 container=pack assumed")
            if c.get("magnitude_flag"):
                flags.append("⚠️ large change")
            flag_str = " " + " ".join(flags) if flags else ""
            lines.append(f"  {item_idx}. {c['product_name']} → {c['seatable_product']} ({c['match_score']}%){flag_str}")
            lines.append(f"     RM{c['old_price']:.2f} → RM{c['new_price']:.2f}/{c['unit']} ({c['diff_pct']}%)")

    if payload.get("unmatched_items"):
        lines.append(f"\n⚠️ *Unmatched ({len(payload['unmatched_items'])}):*")
        for entry in payload["unmatched_items"]:
            lines.append(f"  • {entry['product_name']}")
            for c in entry.get("candidates", []):
                lines.append(f"    → {c['name']} ({c['score']:.0f}%)")

    if payload.get("data_gaps"):
        lines.append(f"\n🔧 *Seatable data gap ({len(payload['data_gaps'])}):*")
        for d in payload["data_gaps"]:
            lines.append(f"  • {d['product_name']} → {d['seatable_product']} [{d['sp_code']}] ({d['match_score']}%)")
            lines.append(f"    missing: {', '.join(d['missing'])}")

    if payload.get("unit_mismatches"):
        lines.append(f"\n📐 *Unit can't convert ({len(payload['unit_mismatches'])}):*")
        for u in payload["unit_mismatches"]:
            lines.append(f"  • {u['product_name']} → {u['seatable_product']} [{u['sp_code']}] ({u['match_score']}%)")
            lines.append(f"    invoice='{u['invoice_unit']}' vs supplier='{u['supplier_uom']}'")

    if not payload.get("supplier_matched"):
        lines.append(f"\n🏭 *Unknown supplier:* {payload['supplier_name']}")
        for c in payload.get("supplier_candidates", []):
            lines.append(f"    → {c['name']} ({c['score']:.0f}%)")

    if payload.get("has_handwriting") and payload.get("handwriting"):
        lines.append(f"\n✍️ _{payload['handwriting']}_")

    return "\n".join(lines)


def main():
    sys.stdout.reconfigure(encoding="utf-8")

    if len(sys.argv) < 2:
        print("Usage: python step2_compare.py <invoice_pdf_or_image>")
        sys.exit(1)

    file_path = sys.argv[1]

    from step1_extract import extract_invoice
    print(f"[LOG] Running step1 extraction on: {file_path}")
    step1_result = extract_invoice(file_path)

    if step1_result.get("status") != "success":
        print(f"[LOG] Step1 failed: {step1_result.get('error_message')}")
        sys.exit(1)

    payloads = build_comparison(step1_result)

    from notifier import notify_invoice_comparison
    for payload in payloads:
        print("\n" + "=" * 60)
        print(format_telegram_message(payload))
        notify_invoice_comparison(payload)


if __name__ == "__main__":
    main()