import json
import os
import re
import time
from datetime import date
from pathlib import Path
from typing import Optional, List, Dict, Any
from decimal import Decimal, InvalidOperation
from dotenv import load_dotenv
from google import genai
from google.genai import types

_TICK_ONLY = re.compile(r'^[\s✓✔√,./]+$')

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
_GEMINI_RPD = int(os.getenv("GEMINI_RPD", "20"))
_USAGE_PATH = Path(__file__).parent.parent.parent.parent / "data" / "gemini_daily_usage.json"


def _read_usage() -> dict:
    if _USAGE_PATH.exists():
        try:
            with open(_USAGE_PATH, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("date") == str(date.today()):
                return data
        except Exception:
            pass
    return {"date": str(date.today()), "count": 0}


def _increment_usage() -> int:
    usage = _read_usage()
    usage["count"] += 1
    _USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_USAGE_PATH, "w", encoding="utf-8") as f:
        json.dump(usage, f)
    return usage["count"]


def _check_daily_limit() -> tuple[bool, int]:
    """Returns (allowed, current_count)."""
    usage = _read_usage()
    count = usage["count"]
    if count >= _GEMINI_RPD:
        print(f"[LOG] Gemini daily limit reached: {count}/{_GEMINI_RPD} calls used today")
        return False, count
    if count >= _GEMINI_RPD * 0.8:
        print(f"[LOG] Gemini daily usage warning: {count}/{_GEMINI_RPD} calls used today")
    return True, count

SYSTEM_PROMPT = (
    "You are an invoice data extraction assistant. "
    "Extract all invoice data from the document and return ONLY a valid JSON object — "
    "no markdown, no explanation, no code fences. "
    "If the document contains multiple invoices with different invoice numbers, "
    "extract each as a separate entry in the invoices array.\n\n"
    "Required JSON structure:\n"
    '{\n'
    '  "invoices": [\n'
    '    {\n'
    '      "supplier_name": "string or null",\n'
    '      "supplier_brn": "string or null",\n'
    '      "supplier_tax_id": "string or null",\n'
    '      "invoice_number": "string or null",\n'
    '      "do_number": "string or null",\n'
    '      "invoice_date": "YYYY-MM-DD or null",\n'
    '      "invoice_total": number or null,\n'
    '      "has_handwriting": true or false,\n'
    '      "handwriting_content": "verbatim transcription of meaningful handwritten annotations, or null if none",\n'
    '      "line_items": [\n'
    '        {\n'
    '          "product_name": "string or null",\n'
    '          "quantity": number or null,\n'
    '          "unit": "kg/pcs/btl/ctn/etc or null",\n'
    '          "unit_price": number or null,\n'
    '          "total_price": number or null,\n'
    '          "crossed_out": true or false,\n'
    '          "is_promotional": true or false,\n'
    '          "promotion_note": "verbatim discount text or null"\n'
    '        }\n'
    '      ]\n'
    '    }\n'
    '  ]\n'
    '}\n\n'
    "Rules:\n"
    "- Line items may span multiple visual lines — treat them as one item.\n"
    "- For dates: output YYYY-MM-DD. Do NOT assume a fixed format — infer the correct "
    "interpretation from context: invoice number date codes (e.g. 'IV2606' = June 2026), "
    "month names printed elsewhere, and surrounding text. When two readings are plausible "
    "(e.g. 03/06 could be March 6 or June 3), pick the one consistent with other context "
    "on the document such as the invoice number.\n"
    "- For numbers use numeric values, not strings.\n"
    "- If a field is missing or unreadable use null.\n"
    "- supplier_brn: extract the supplier's Business Registration Number (BRN) or Company Registration "
    "Number if visible. Look for labels: 'BRN:', 'Reg No:', 'SSM:', 'Co. No:', 'Company No:'. "
    "Return null if not found.\n"
    "- supplier_tax_id: extract the supplier's SST or Tax ID if visible. "
    "Look for labels: 'SST No:', 'GST No:', 'Tax ID:', 'GST ID:', 'SST Reg No:'. "
    "Return null if not found.\n"
    "- has_handwriting: set true ONLY for meaningful handwriting — words (English, Chinese, Malay), "
    "X marks, cross-outs, or handwritten numbers. "
    "Tick marks and checkmarks (✓ ✔) alone are NOT meaningful — set has_handwriting: false.\n"
    "- handwriting_content: transcribe meaningful handwritten annotations verbatim. "
    "Exclude signatures, company names, registration numbers, rubber stamps, and tick/checkmarks.\n"
    "- crossed_out: set true ONLY if the item description or price is physically struck through with a line. "
    "A tick, slash, or checkmark through the row NUMBER (e.g. ✓1, /2) is a delivery confirmation — do NOT set crossed_out. "
    "Only the item text or price itself being struck through counts.\n"
    "- Retail receipt single-line items: when a description line ends with a count token followed by a "
    "container word (e.g. 'CAMERON STRAWBERRY 2 PKT', 'ORANGE JUICE 3 BTL'), treat the trailing "
    "'<n> <unit>' as quantity and unit. Set product_name to the description without the count token. "
    "Example: 'CAMERON STRAWBERRY 2 PKT' → product_name='CAMERON STRAWBERRY', quantity=2, unit='PKT'. "
    "When only total_price is shown (no separate unit_price column), set unit_price = total_price / quantity.\n"
    "- Multi-pack bundle vs purchase quantity: if the count token describes the product's own bundle format "
    "(e.g. 'MILK 6X200ML' = a six-pack carton), keep the bundle description in product_name and set "
    "quantity to the actual number of units purchased (default 1 if no separate quantity column shown).\n"
    "- Seafood and meat size grade codes: patterns like 'X/Y' or 'U<n>' in product names are industry "
    "grading codes for pieces-per-kg, NOT quantities, fractions, or pack sizes. Common prawn grades: "
    "8/12, 13/15, 21/25, 31/40, 41/50, 61/70, and U10 (under 10 per kg, jumbo). Always keep the full "
    "grade code (digits, slash, and any 'U' prefix) inside product_name verbatim. Never split X/Y into "
    "quantity, unit_price, or any other numeric field. The pattern applies to other size-graded seafood "
    "and meat too. Example: 'TIGER PRAWN 13/15 1 BLOCK' → product_name='TIGER PRAWN 13/15', quantity=1, "
    "unit='block'. Example: 'WHITE PRAWN U10 2 BLOCK' → product_name='WHITE PRAWN U10', quantity=2, "
    "unit='block'.\n"
    "- 'Block' container: treat 'block' as a container unit (like pack, carton, bottle). When the invoice "
    "shows '<n> block(s)', set quantity=<n> and unit='block'. Do not convert to weight even if a per-kg "
    "price appears elsewhere — keep the pack-level price and unit intact.\n"
    "- is_promotional / promotion_note: set is_promotional=true when the line shows discount language "
    "('Promo', 'Disc', '% off', 'Off', 'Buy N Free M', 'N for RM X', 'Free', 'FOC') OR when the "
    "receipt has a separate discount/rebate line for this item. Set promotion_note to the verbatim "
    "discount text. Keep total_price as the actual amount paid (after discount).\n"
    "- Return ONLY the JSON object."
)

MIME_MAP = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def extract_invoice_gemini(file_path: str) -> Dict[str, Any]:
    """Extract invoice data from PDF or image using Gemini as fallback."""
    start_time = time.time()

    file_path_obj = Path(file_path)
    if not file_path_obj.exists():
        error_msg = f"File not found: {file_path}"
        print(f"[LOG] Error: {error_msg}")
        return _error_response(file_path, error_msg, 0)

    mime_type = MIME_MAP.get(file_path_obj.suffix.lower())
    if not mime_type:
        error_msg = f"Unsupported file type: {file_path_obj.suffix}"
        print(f"[LOG] Error: {error_msg}")
        return _error_response(file_path, error_msg, 0)

    allowed, used = _check_daily_limit()
    if not allowed:
        error_msg = f"Gemini daily limit reached ({used}/{_GEMINI_RPD}). Try again tomorrow."
        return _error_response(file_path, error_msg, 0)

    client = genai.Client(api_key=GEMINI_API_KEY)

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    _RETRY_DELAYS = [15, 30, 60]  # seconds; covers 5 RPM window

    for attempt, delay in enumerate([0] + _RETRY_DELAYS):
        if delay:
            print(f"[LOG] Gemini rate-limited, retrying in {delay}s (attempt {attempt + 1}/4)...")
            time.sleep(delay)

        try:
            print(f"[LOG] Calling Gemini for: {file_path}")
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[
                    types.Part.from_bytes(data=file_bytes, mime_type=mime_type),
                    SYSTEM_PROMPT,
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                ),
            )

            duration_ms = int((time.time() - start_time) * 1000)
            raw = response.text.strip()
            gemini_data = json.loads(raw)
            invoices = _parse_invoices(gemini_data.get("invoices", []), raw_text=raw)

            daily_count = _increment_usage()
            print(
                f"[LOG] Gemini extraction successful. Duration: {duration_ms}ms, "
                f"Invoices: {len(invoices)}, Daily usage: {daily_count}/{_GEMINI_RPD}"
            )

            return {
                "status": "success",
                "file_path": str(file_path),
                "error_message": None,
                "invoices": invoices,
                "api_call_summary": {
                    "success": True,
                    "pages_processed": 1,
                    "invoices_extracted": len(invoices),
                    "duration_ms": duration_ms,
                },
            }

        except Exception as e:
            err = str(e)
            is_rate_limit = ("429" in err or "503" in err or "quota" in err.lower()
                             or "resource_exhausted" in err.lower() or "unavailable" in err.lower())
            if is_rate_limit and attempt < len(_RETRY_DELAYS):
                continue
            duration_ms = int((time.time() - start_time) * 1000)
            print(f"[LOG] Gemini failed. Error: {err}")
            return _error_response(file_path, err, duration_ms)


def _parse_invoices(raw_invoices: list, raw_text: str = None) -> List[Dict[str, Any]]:
    invoices = []
    for inv in raw_invoices:
        flags: List[str] = []

        supplier_name = inv.get("supplier_name") or None
        supplier_brn = inv.get("supplier_brn") or None
        supplier_tax_id = inv.get("supplier_tax_id") or None
        invoice_number = inv.get("invoice_number") or None
        do_number = inv.get("do_number") or None
        invoice_date = inv.get("invoice_date") or None
        invoice_total = _to_decimal(inv.get("invoice_total"))
        has_handwriting = bool(inv.get("has_handwriting", False))
        handwriting_content = inv.get("handwriting_content") or None

        # Ticks/checkmarks alone are not actionable — suppress
        if has_handwriting and handwriting_content and _TICK_ONLY.match(handwriting_content):
            has_handwriting = False
            handwriting_content = None

        if not supplier_name:
            flags.append("missing_supplier_name")
        if not invoice_number:
            flags.append("missing_invoice_number")
        if not invoice_date:
            flags.append("missing_invoice_date")
        elif invoice_date:
            try:
                from datetime import timedelta
                parsed_date = date.fromisoformat(invoice_date)
                if parsed_date > date.today() + timedelta(days=45):
                    flags.append(f"date_possibly_wrong: {invoice_date} is in the future")
            except ValueError:
                flags.append(f"date_parse_error: {invoice_date}")
        if has_handwriting:
            flags.append("handwriting_detected")

        line_items = _parse_line_items(inv.get("line_items", []))
        if not line_items:
            flags.append("no_line_items_found")

        invoice_obj = {
            "supplier_name": supplier_name,
            "supplier_brn": supplier_brn,
            "supplier_tax_id": supplier_tax_id,
            "invoice_number": invoice_number,
            "do_number": do_number,
            "invoice_date": invoice_date,
            "invoice_total": invoice_total,
            "has_handwriting": has_handwriting,
            "handwriting_content": handwriting_content,
            "confidence": None,
            "extraction_method": "gemini_fallback",
            "raw_text": raw_text,
            "flags": flags,
            "line_items": line_items,
        }
        invoice_obj["flags"].extend(_verify_totals(invoice_obj))
        invoices.append(invoice_obj)
    return invoices


def _parse_line_items(raw_items: list) -> List[Dict[str, Any]]:
    line_items = []
    for item in raw_items:
        item_flags: List[str] = []

        product_name = item.get("product_name") or None
        quantity = _to_decimal(item.get("quantity"))
        unit = item.get("unit") or None
        unit_price = _to_decimal(item.get("unit_price"))
        total_price = _to_decimal(item.get("total_price"))
        crossed_out = bool(item.get("crossed_out", False))
        is_promotional = bool(item.get("is_promotional", False))
        promotion_note = item.get("promotion_note") or None

        if not product_name:
            item_flags.append("missing_product_name")
        if quantity is None:
            item_flags.append("missing_quantity")
        if unit_price is None:
            item_flags.append("missing_unit_price")
        if total_price is None:
            item_flags.append("missing_total_price")
        if crossed_out:
            item_flags.append("crossed_out")
        if is_promotional:
            item_flags.append("promotional")

        line_items.append(
            {
                "product_name": product_name,
                "quantity": quantity,
                "unit": unit,
                "unit_price": unit_price,
                "total_price": total_price,
                "crossed_out": crossed_out,
                "is_promotional": is_promotional,
                "promotion_note": promotion_note,
                "confidence": None,
                "flags": item_flags,
            }
        )
    return line_items


def _verify_totals(invoice: Dict[str, Any]) -> List[str]:
    flags = []
    for i, item in enumerate(invoice.get("line_items", []), 1):
        q = item.get("quantity")
        up = item.get("unit_price")
        tp = item.get("total_price")
        if q is not None and up is not None and tp is not None:
            try:
                expected = Decimal(str(q)) * Decimal(str(up))
                actual = Decimal(str(tp))
                if expected > 0:
                    diff_pct = abs((actual - expected) / expected) * 100
                    if diff_pct > 1:
                        flags.append(
                            f"line_{i}_total_mismatch: {q}x{up}={expected} vs invoice={actual}"
                        )
            except Exception:
                pass

    line_sum = Decimal("0")
    for item in invoice.get("line_items", []):
        tp = item.get("total_price")
        if tp is not None:
            try:
                line_sum += Decimal(str(tp))
            except Exception:
                pass

    invoice_total = invoice.get("invoice_total") or invoice.get("total") or invoice.get("subtotal")
    if invoice_total and line_sum > 0:
        try:
            inv_total_dec = Decimal(str(invoice_total))
            diff_pct = abs((line_sum - inv_total_dec) / inv_total_dec) * 100
            if diff_pct > 1:
                flags.append(
                    f"invoice_total_mismatch: lines_sum={line_sum} vs invoice_total={inv_total_dec}"
                )
        except Exception:
            pass

    return flags


def _to_decimal(value) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _error_response(file_path: str, error_msg: str, duration_ms: int) -> Dict[str, Any]:
    return {
        "status": "error",
        "file_path": str(file_path),
        "error_message": error_msg,
        "invoices": [],
        "api_call_summary": {
            "success": False,
            "pages_processed": 0,
            "invoices_extracted": 0,
            "duration_ms": duration_ms,
        },
    }
