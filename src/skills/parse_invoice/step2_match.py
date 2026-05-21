"""
Step 2: Match extracted line items against Seatable Supplier Products.

4-tier cascade:
  Tier 1 — exact match (confidence 1.0)
  Tier 2 — rapidfuzz token_sort_ratio >= 85 (confidence = score/100)
  Tier 3 — Gemini LLM from top-5 fuzzy candidates (confidence >= 0.5)
  Tier 4 — manual review; stored in pending_matches + Telegram alert

CLI: python step2_match.py <invoice_pdf_or_image_path>
"""
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from rapidfuzz import fuzz, process
from seatable_api import Base
from google import genai
from google.genai import types

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent))

SEATABLE_API_TOKEN = os.getenv("SEATABLE_API_TOKEN")
SEATABLE_BASE_URL = os.getenv("SEATABLE_BASE_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-2.5-flash"

FUZZY_THRESHOLD = 85
LLM_CONFIDENCE_THRESHOLD = 0.5
TOP_CANDIDATES = 5

_PRODUCT_CACHE: Optional[List[Dict[str, Any]]] = None


def _load_seatable_products() -> List[Dict[str, Any]]:
    global _PRODUCT_CACHE
    if _PRODUCT_CACHE is not None:
        return _PRODUCT_CACHE

    base = Base(SEATABLE_API_TOKEN, SEATABLE_BASE_URL)
    base.auth()
    rows = base.list_rows("Supplier Products")

    products = []
    for row in rows:
        name = row.get("Supplier Product Name") or ""
        if name:
            products.append({"name": name, "id": row.get("_id", "")})

    _PRODUCT_CACHE = products
    print(f"[LOG] Loaded {len(products)} products from Seatable")
    return products


def _normalize(s: str) -> str:
    return s.lower().strip()


def _llm_match(
    product_name: str,
    candidates: List[str],
    products: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        user_prompt = (
            f'Invoice item: "{product_name}". '
            f"Candidates: {json.dumps(candidates)}. "
            f"Pick the best match or return null if none fit. "
            f'JSON: {{"matched": "<name or null>", "confidence": <0-1>, "reason": "<one line>"}}'
        )
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                "You are a product matcher for a Malaysian cafe. Return ONLY valid JSON, no markdown.",
                user_prompt,
            ],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        data = json.loads(response.text.strip())
        matched_name = data.get("matched")
        confidence = float(data.get("confidence", 0))
        reason = data.get("reason", "")

        if not matched_name:
            return None
        matched_product = next((p for p in products if p["name"] == matched_name), None)
        if not matched_product:
            return None

        return {
            "match_tier": 3,
            "match_confidence": round(confidence, 4),
            "matched_product_name": matched_product["name"],
            "matched_product_id": matched_product["id"],
            "match_reason": f"LLM: {reason}",
            "_candidates": [],
        }
    except Exception as e:
        print(f"[LOG] LLM match failed for '{product_name}': {e}")
        return None


def _match_item(product_name: str, products: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Returns match dict including internal `_candidates` key (stripped before output)."""
    if not product_name:
        return {
            "match_tier": 4,
            "match_confidence": 0.0,
            "matched_product_name": None,
            "matched_product_id": None,
            "match_reason": "manual review needed: empty product name",
            "_candidates": [],
        }

    norm_input = _normalize(product_name)

    # Tier 1: exact
    for p in products:
        if _normalize(p["name"]) == norm_input:
            return {
                "match_tier": 1,
                "match_confidence": 1.0,
                "matched_product_name": p["name"],
                "matched_product_id": p["id"],
                "match_reason": "exact match",
                "_candidates": [],
            }

    # Score all with rapidfuzz
    names = [p["name"] for p in products]
    scored = process.extract(product_name, names, scorer=fuzz.token_sort_ratio, limit=TOP_CANDIDATES)

    if not scored:
        return {
            "match_tier": 4,
            "match_confidence": 0.0,
            "matched_product_name": None,
            "matched_product_id": None,
            "match_reason": "manual review needed: no candidates",
            "_candidates": [],
        }

    best_name, best_score, best_idx = scored[0]
    best_product = products[best_idx]
    candidates = [
        {"name": name, "id": products[idx]["id"], "score": score}
        for name, score, idx in scored
    ]

    # Tier 2: fuzzy >= threshold
    if best_score >= FUZZY_THRESHOLD:
        return {
            "match_tier": 2,
            "match_confidence": round(best_score / 100, 4),
            "matched_product_name": best_product["name"],
            "matched_product_id": best_product["id"],
            "match_reason": f"fuzzy match (score={best_score})",
            "_candidates": [],
        }

    # Tier 3: LLM
    candidate_names = [n for n, _, _ in scored]
    llm_result = _llm_match(product_name, candidate_names, products)
    if llm_result and llm_result["match_confidence"] >= LLM_CONFIDENCE_THRESHOLD:
        return llm_result

    # Tier 4: manual
    return {
        "match_tier": 4,
        "match_confidence": round(best_score / 100, 4),
        "matched_product_name": None,
        "matched_product_id": None,
        "match_reason": f"manual review needed: best fuzzy score={best_score}",
        "_candidates": candidates,
    }


def match_step1_result(
    step1_result: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Returns (enhanced_result, tier4_groups) where tier4_groups is a list of
    {invoice, file_path, items: [{id, product_name, quantity, unit, unit_price, candidates}]}
    ready for storage and notification.
    """
    products = _load_seatable_products()
    result = dict(step1_result)
    enhanced_invoices = []
    tier4_groups: List[Dict[str, Any]] = []

    for invoice in result.get("invoices", []):
        invoice = dict(invoice)
        tier_counts = {1: 0, 2: 0, 3: 0, 4: 0}
        enhanced_items = []
        tier4_items: List[Dict[str, Any]] = []

        for item in invoice.get("line_items", []):
            item = dict(item)
            match = _match_item(item.get("product_name") or "", products)
            candidates = match.pop("_candidates")
            item.update(match)
            tier_counts[match["match_tier"]] += 1
            enhanced_items.append(item)

            if match["match_tier"] == 4:
                tier4_items.append({
                    "product_name": item.get("product_name") or "",
                    "quantity": float(item["quantity"]) if item.get("quantity") is not None else None,
                    "unit": item.get("unit"),
                    "unit_price": float(item["unit_price"]) if item.get("unit_price") is not None else None,
                    "candidates": candidates,
                })

        invoice["line_items"] = enhanced_items
        invoice["match_summary"] = {
            "tier1_count": tier_counts[1],
            "tier2_count": tier_counts[2],
            "tier3_count": tier_counts[3],
            "tier4_count": tier_counts[4],
            "total_items": len(enhanced_items),
        }
        enhanced_invoices.append(invoice)

        if tier4_items:
            tier4_groups.append({
                "invoice": invoice,
                "file_path": result.get("file_path", ""),
                "items": tier4_items,
            })

    result["invoices"] = enhanced_invoices
    return result, tier4_groups


class _DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def main():
    if len(sys.argv) < 2:
        print("Usage: python step2_match.py <invoice_pdf_or_image_path>")
        sys.exit(1)

    file_path = sys.argv[1]

    from step1_extract import extract_invoice
    from pending_matches_store import add_pending_item
    from notifier import notify_tier4_items

    print(f"[LOG] Running step1 extraction on: {file_path}")
    step1_result = extract_invoice(file_path)

    if step1_result.get("status") == "error":
        print(f"[LOG] Step1 failed: {step1_result.get('error_message')}")
        print(json.dumps(step1_result, cls=_DecimalEncoder, indent=2))
        sys.exit(1)

    print("[LOG] Running step2 matching...")
    enhanced, tier4_groups = match_step1_result(step1_result)

    # Store and notify Tier 4 items
    for group in tier4_groups:
        inv = group["invoice"]
        stored_records = []
        for t4 in group["items"]:
            record_id = add_pending_item(
                invoice_number=inv.get("invoice_number") or "",
                supplier_name=inv.get("supplier_name") or "",
                file_path=group["file_path"],
                product_name=t4["product_name"],
                quantity=t4["quantity"],
                unit=t4["unit"],
                unit_price=t4["unit_price"],
                candidates=t4["candidates"],
            )
            stored_records.append({"id": record_id, **t4})
            safe_name = t4['product_name'].encode('ascii', 'replace').decode('ascii')
            print(f"[LOG] Tier 4 stored: {safe_name} id={record_id}")
        notify_tier4_items(inv, stored_records, group["file_path"])

    print(json.dumps(enhanced, cls=_DecimalEncoder, indent=2))


if __name__ == "__main__":
    main()
