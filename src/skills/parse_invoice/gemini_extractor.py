import json
import os
import time
from pathlib import Path
from typing import Optional, List, Dict, Any
from decimal import Decimal, InvalidOperation
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-2.5-flash"

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
    '      "invoice_number": "string or null",\n'
    '      "do_number": "string or null",\n'
    '      "invoice_date": "YYYY-MM-DD or null",\n'
    '      "has_handwriting": true or false,\n'
    '      "handwriting_content": "verbatim transcription of all handwritten text, or null if none",\n'
    '      "line_items": [\n'
    '        {\n'
    '          "product_name": "string or null",\n'
    '          "quantity": number or null,\n'
    '          "unit": "kg/pcs/btl/ctn/etc or null",\n'
    '          "unit_price": number or null,\n'
    '          "total_price": number or null\n'
    '        }\n'
    '      ]\n'
    '    }\n'
    '  ]\n'
    '}\n\n'
    "Rules:\n"
    "- Line items may span multiple visual lines — treat them as one item.\n"
    "- For dates use YYYY-MM-DD.\n"
    "- For numbers use numeric values, not strings.\n"
    "- If a field is missing or unreadable use null.\n"
    "- has_handwriting must be true if ANY handwritten text appears anywhere on the document.\n"
    "- handwriting_content must transcribe handwritten notes and annotations only — exclude signatures, company names, company registration numbers, and rubber stamps.\n"
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

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)

        with open(file_path, "rb") as f:
            file_bytes = f.read()

        print(f"[LOG] Calling Gemini fallback for: {file_path}")

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
        invoices = _parse_invoices(gemini_data.get("invoices", []))

        print(
            f"[LOG] Gemini extraction successful. Duration: {duration_ms}ms, "
            f"Invoices: {len(invoices)}"
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
        duration_ms = int((time.time() - start_time) * 1000)
        error_msg = str(e)
        print(f"[LOG] Gemini fallback failed. Error: {error_msg}")
        return _error_response(file_path, error_msg, duration_ms)


def _parse_invoices(raw_invoices: list) -> List[Dict[str, Any]]:
    invoices = []
    for inv in raw_invoices:
        flags: List[str] = []

        supplier_name = inv.get("supplier_name") or None
        invoice_number = inv.get("invoice_number") or None
        do_number = inv.get("do_number") or None
        invoice_date = inv.get("invoice_date") or None
        has_handwriting = bool(inv.get("has_handwriting", False))
        handwriting_content = inv.get("handwriting_content") or None

        if not supplier_name:
            flags.append("missing_supplier_name")
        if not invoice_number:
            flags.append("missing_invoice_number")
        if not invoice_date:
            flags.append("missing_invoice_date")
        if has_handwriting:
            flags.append("handwriting_detected")

        line_items = _parse_line_items(inv.get("line_items", []))
        if not line_items:
            flags.append("no_line_items_found")

        invoices.append(
            {
                "supplier_name": supplier_name,
                "invoice_number": invoice_number,
                "do_number": do_number,
                "invoice_date": invoice_date,
                "has_handwriting": has_handwriting,
                "handwriting_content": handwriting_content,
                "confidence": None,
                "extraction_method": "gemini_fallback",
                "flags": flags,
                "line_items": line_items,
            }
        )
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

        if not product_name:
            item_flags.append("missing_product_name")
        if quantity is None:
            item_flags.append("missing_quantity")
        if unit_price is None:
            item_flags.append("missing_unit_price")
        if total_price is None:
            item_flags.append("missing_total_price")

        line_items.append(
            {
                "product_name": product_name,
                "quantity": quantity,
                "unit": unit,
                "unit_price": unit_price,
                "total_price": total_price,
                "confidence": None,
                "flags": item_flags,
            }
        )
    return line_items


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
