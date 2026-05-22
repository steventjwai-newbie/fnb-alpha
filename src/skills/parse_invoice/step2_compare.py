import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from rapidfuzz import fuzz, process
from seatable_api import Base

load_dotenv()

_here = str(Path(__file__).parent)
if _here not in sys.path:
    sys.path.insert(0, _here)

from unit_normalizer import normalize_invoice_price

SEATABLE_API_TOKEN = os.getenv("SEATABLE_API_TOKEN")
SEATABLE_BASE_URL = os.getenv("SEATABLE_BASE_URL")


def _norm(s: str) -> str:
    return (s or "").strip().lower()

_suppliers_cache: Optional[List[Dict]] = None
_products_cache: Optional[List[Dict]] = None


def _seatable_base() -> Base:
    base = Base(SEATABLE_API_TOKEN, SEATABLE_BASE_URL)
    base.auth()
    return base


def _load_suppliers(base: Base) -> List[Dict]:
    global _suppliers_cache
    if _suppliers_cache is None:
        _suppliers_cache = base.list_rows("Suppliers")
        print(f"[LOG] Loaded {len(_suppliers_cache)} suppliers from Seatable")
    return _suppliers_cache


def _load_products(base: Base) -> List[Dict]:
    global _products_cache
    if _products_cache is None:
        _products_cache = base.list_rows("Supplier Products")
        print(f"[LOG] Loaded {len(_products_cache)} products from Seatable")
    return _products_cache


def _match_supplier(supplier_name: str, suppliers: List[Dict]) -> Optional[Dict]:
    names = [r.get("Supplier Name", "") for r in suppliers]
    norm_names = [_norm(n) for n in names]
    norm_supplier_name = _norm(supplier_name)
    results = process.extract(norm_supplier_name, norm_names, scorer=fuzz.token_sort_ratio, limit=1)
    if not results or results[0][1] < 70:
        return None
    _, score, idx = results[0]
    return suppliers[idx]


def _get_linked_ids(cell_value) -> List[str]:
    """Extract row IDs from a Seatable link cell (handles row_id and _id key variants)."""
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


def build_comparison(step1_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Compare invoice line items against Seatable Supplier Products.
    Returns one notification payload per invoice. No writes, no LLM calls.
    """
    if step1_result.get("status") != "success":
        return []

    base = _seatable_base()
    suppliers = _load_suppliers(base)
    all_products = _load_products(base)

    payloads = []

    for invoice in step1_result.get("invoices", []):
        supplier_name = invoice.get("supplier_name") or ""
        invoice_number = invoice.get("invoice_number") or ""
        invoice_date = invoice.get("invoice_date") or ""

        matched_supplier = _match_supplier(supplier_name, suppliers)
        supplier_matched = matched_supplier is not None

        if supplier_matched:
            supplier_id = matched_supplier.get("_id", "")
            supplier_products = _filter_products_by_supplier(all_products, supplier_id)
            product_names = [p.get("Supplier Product Name", "") for p in supplier_products]
            supplier_candidates = []
        else:
            supplier_products = []
            product_names = []
            supplier_names = [s.get("Supplier Name", "") for s in suppliers]
            norm_supplier_names = [_norm(n) for n in supplier_names]
            norm_supplier_name = _norm(supplier_name)
            top3_sup = process.extract(norm_supplier_name, norm_supplier_names, scorer=fuzz.token_sort_ratio, limit=3) if norm_supplier_names else []
            supplier_candidates = _make_candidates(top3_sup)

        all_product_names = [p.get("Supplier Product Name", "") for p in all_products]
        norm_all_product_names = [_norm(n) for n in all_product_names]
        price_changes = []
        matched_items = []
        unmatched_items = []

        for item in invoice.get("line_items", []):
            product_name = item.get("product_name") or ""
            invoice_unit = item.get("unit") or ""
            invoice_unit_price = item.get("unit_price")

            if not supplier_matched:
                if product_name:
                    norm_product_name = _norm(product_name)
                    top3 = process.extract(norm_product_name, norm_all_product_names, scorer=fuzz.token_sort_ratio, limit=3) if norm_all_product_names else []
                    unmatched_items.append({"product_name": product_name, "candidates": _make_candidates(top3)})
                continue

            if not product_names:
                if product_name:
                    unmatched_items.append({"product_name": product_name, "candidates": []})
                continue

            norm_product_names = [_norm(n) for n in product_names]
            norm_product_name = _norm(product_name)
            results = process.extract(norm_product_name, norm_product_names, scorer=fuzz.token_sort_ratio, limit=3)
            if not results or results[0][1] < 75:
                if product_name:
                    unmatched_items.append({"product_name": product_name, "candidates": _make_candidates(results)})
                continue

            _, match_score, idx = results[0]
            best_name = product_names[idx]
            matched_product = supplier_products[idx]

            old_price = matched_product.get("Price per Pack")
            supplier_unit_qty = matched_product.get("Unit Quantity")
            supplier_uom = matched_product.get("Unit of Measure") or ""

            if old_price is None or invoice_unit_price is None or not supplier_unit_qty:
                if product_name:
                    unmatched_items.append({"product_name": product_name, "candidates": _make_candidates(results)})
                continue

            new_price_per_pack = normalize_invoice_price(
                invoice_unit_price,
                invoice_unit,
                supplier_unit_qty,
                supplier_uom,
            )

            if new_price_per_pack is None:
                if product_name:
                    unmatched_items.append({"product_name": product_name, "candidates": _make_candidates(results)})
                continue

            new_price = float(new_price_per_pack)
            old_price_f = float(old_price)

            if abs(new_price - old_price_f) > 0.001:
                price_changes.append({
                    "invoice_product": product_name,
                    "seatable_product": best_name,
                    "old_price": old_price_f,
                    "new_price": new_price,
                    "unit": invoice_unit,
                    "match_score": round(match_score, 2),
                })
            else:
                matched_items.append({
                    "product_name": product_name,
                    "seatable_product": best_name,
                    "price": old_price_f,
                    "unit": invoice_unit,
                })

        payloads.append({
            "invoice_number": invoice_number,
            "supplier_name": supplier_name,
            "invoice_date": invoice_date,
            "supplier_matched": supplier_matched,
            "supplier_candidates": supplier_candidates,
            "price_changes": price_changes,
            "matched_items": matched_items,
            "unmatched_items": unmatched_items,
            "handwriting": invoice.get("handwriting_content"),
            "has_handwriting": bool(invoice.get("has_handwriting")),
        })

    return payloads


def format_telegram_message(payload: Dict[str, Any]) -> str:
    lines = [
        f"📋 {payload['invoice_number']} | {payload['supplier_name']} | {payload['invoice_date']}"
    ]

    if payload.get("matched_items"):
        lines.append(f"\n✅ *Matched ({len(payload['matched_items'])}):*")
        for m in payload["matched_items"]:
            lines.append(f"  • {m['product_name']} · RM{m['price']:.2f}/{m['unit']}")

    if payload.get("price_changes"):
        lines.append("\n💰 *Price update:*")
        for ch in payload["price_changes"]:
            unit = ch["unit"]
            lines.append(f"  {ch['invoice_product']}")
            lines.append(f"  RM{ch['old_price']:.2f}/{unit} → RM{ch['new_price']:.2f}/{unit}")

    if payload.get("unmatched_items"):
        lines.append(f"\n⚠️ *Unmatched ({len(payload['unmatched_items'])}):*")
        for entry in payload["unmatched_items"]:
            lines.append(f"  • {entry['product_name']}")
            for c in entry.get("candidates", []):
                lines.append(f"    → {c['name']} ({c['score']:.0f}%)")

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
