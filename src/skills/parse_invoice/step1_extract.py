import json
import sys
import os
import time
from pathlib import Path
from typing import Optional, List, Dict, Any
from decimal import Decimal, InvalidOperation
from dotenv import load_dotenv
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential

load_dotenv()

AZURE_DI_ENDPOINT = os.getenv("AZURE_DI_ENDPOINT")
AZURE_DI_KEY = os.getenv("AZURE_DI_KEY")


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder that handles Decimal values."""

    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def extract_invoice(file_path: str) -> Dict[str, Any]:
    """Extract invoice data from PDF or image. Currently routes to Gemini only."""
    import sys as _sys
    _here = str(Path(__file__).parent)
    if _here not in _sys.path:
        _sys.path.insert(0, _here)
    from gemini_extractor import extract_invoice_gemini
    from notifier import notify_handwriting_detected

    result = extract_invoice_gemini(file_path)

    for invoice in result.get("invoices", []):
        if invoice.get("has_handwriting"):
            notify_handwriting_detected(invoice, file_path)

    return result


def _extract_invoice_azure(file_path: str) -> Dict[str, Any]:
    """Azure DI extraction — kept for reference, not active."""
    start_time = time.time()

    file_path_obj = Path(file_path)
    if not file_path_obj.exists():
        error_msg = f"File not found: {file_path}"
        print(f"[LOG] Error: {error_msg}")
        return {
            "status": "error",
            "file_path": file_path,
            "error_message": error_msg,
            "invoices": [],
            "api_call_summary": {
                "success": False,
                "pages_processed": 0,
                "invoices_extracted": 0,
                "duration_ms": 0,
            },
        }

    suffix = file_path_obj.suffix.lower()
    supported_types = [
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
        ".bmp",
        ".tiff",
        ".gif",
        ".webp",
    ]
    if suffix not in supported_types:
        error_msg = f"Unsupported file type: {suffix}. Supported: {', '.join(supported_types)}"
        print(f"[LOG] Error: {error_msg}")
        return {
            "status": "error",
            "file_path": file_path,
            "error_message": error_msg,
            "invoices": [],
            "api_call_summary": {
                "success": False,
                "pages_processed": 0,
                "invoices_extracted": 0,
                "duration_ms": 0,
            },
        }

    try:
        client = DocumentIntelligenceClient(
            endpoint=AZURE_DI_ENDPOINT, credential=AzureKeyCredential(AZURE_DI_KEY)
        )

        with open(file_path, "rb") as f:
            file_data = f.read()

        print(f"[LOG] Calling Azure Document Intelligence for: {file_path}")

        poller = client.begin_analyze_document("prebuilt-invoice", file_data)
        result = poller.result()

        duration_ms = int((time.time() - start_time) * 1000)
        pages_count = len(result.pages) if result.pages else 0
        print(
            f"[LOG] Azure DI call successful. Duration: {duration_ms}ms, Pages: {pages_count}"
        )

        invoices = _process_invoices(result)

        # Gemini fallback for any invoice where all line items are missing prices
        fallback_needed = any(_needs_gemini_fallback(inv) for inv in invoices)
        if fallback_needed:
            import sys as _sys
            _here = str(Path(__file__).parent)
            if _here not in _sys.path:
                _sys.path.insert(0, _here)
            from gemini_extractor import extract_invoice_gemini
            gemini_result = extract_invoice_gemini(file_path)
            gemini_invoices = gemini_result.get("invoices", [])

            # Index Gemini invoices by invoice_number for matching
            gemini_by_num: Dict[str, Any] = {
                g["invoice_number"]: g
                for g in gemini_invoices
                if g.get("invoice_number")
            }

            for i, invoice in enumerate(invoices):
                if _needs_gemini_fallback(invoice):
                    azure_key = invoice.get("invoice_number") or invoice.get("do_number")
                    gemini_match = (
                        gemini_by_num.get(azure_key)
                        if azure_key and azure_key in gemini_by_num
                        else (gemini_invoices[i] if i < len(gemini_invoices) else None)
                    )
                    if gemini_match:
                        invoices[i] = _merge_with_gemini(invoice, gemini_match)
                        print(f"[LOG] Invoice {invoice.get('invoice_number', 'unknown')}: gemini_fallback")
                    else:
                        print(f"[LOG] Invoice {invoice.get('invoice_number', 'unknown')}: azure_di (no gemini match)")
                else:
                    print(f"[LOG] Invoice {invoice.get('invoice_number', 'unknown')}: azure_di")
        else:
            for invoice in invoices:
                print(f"[LOG] Invoice {invoice.get('invoice_number', 'unknown')}: azure_di")

        print(f"[LOG] Invoices extracted: {len(invoices)}")

        return {
            "status": "success",
            "file_path": str(file_path),
            "error_message": None,
            "invoices": invoices,
            "api_call_summary": {
                "success": True,
                "pages_processed": pages_count,
                "invoices_extracted": len(invoices),
                "duration_ms": duration_ms,
            },
        }

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        error_msg = str(e)
        print(f"[LOG] Azure DI call failed. Error: {error_msg}")

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


def _compute_confidence(document) -> float:
    """Compute mean confidence across all fields in document."""
    if not hasattr(document, "fields"):
        return 0.0

    confidences = []
    for field in document.fields.values():
        if field and hasattr(field, "confidence") and field.confidence is not None:
            confidences.append(field.confidence)

    return sum(confidences) / len(confidences) if confidences else 0.0


def _process_invoices(result) -> List[Dict[str, Any]]:
    """Group documents by invoice number (primary) or DO number (fallback). Merge same invoices."""
    invoices = []

    if not result.documents:
        return invoices

    invoice_groups: Dict[str, list] = {}

    for doc in result.documents:
        invoice_num = _get_field_value(doc, "InvoiceId")
        do_num = _get_field_value(doc, "DeliveryOrderNumber")

        key = invoice_num if invoice_num else do_num

        if not key:
            key = f"unknown_{len(invoice_groups)}"

        if key not in invoice_groups:
            invoice_groups[key] = []

        invoice_groups[key].append(doc)

    raw_text = result.content if hasattr(result, "content") else None

    for key, documents in invoice_groups.items():
        merged_invoice = _merge_invoice_documents(documents, raw_text=raw_text)
        invoices.append(merged_invoice)

    return invoices


def _merge_invoice_documents(documents: List, raw_text=None) -> Dict[str, Any]:
    """Merge multiple documents with same invoice number into one invoice object."""
    if not documents:
        return {}

    first_doc = documents[0]
    invoice_obj = _build_invoice_object(first_doc, skip_confidence=True, raw_text=raw_text)

    # Merge line items from remaining documents
    for doc in documents[1:]:
        doc_items = _extract_line_items(doc)
        invoice_obj["line_items"].extend(doc_items)

    # Recompute confidence from all documents
    all_confidences = [_compute_confidence(doc) for doc in documents]
    invoice_obj["confidence"] = (
        sum(all_confidences) / len(all_confidences) if all_confidences else 0.0
    )

    return invoice_obj


def _build_invoice_object(document, skip_confidence: bool = False, raw_text=None) -> Dict[str, Any]:
    """Build a single invoice object from Azure DI document."""
    flags: List[str] = []

    supplier_name = _get_field_value(document, "VendorName")
    invoice_number = _get_field_value(document, "InvoiceId")
    do_number = _get_field_value(document, "DeliveryOrderNumber")
    invoice_date = _get_field_value(document, "InvoiceDate")

    if not supplier_name:
        flags.append("missing_supplier_name")
    if not invoice_number:
        flags.append("missing_invoice_number")
    if not invoice_date:
        flags.append("missing_invoice_date")

    line_items = _extract_line_items(document)
    if not line_items:
        flags.append("no_line_items_found")

    confidence = _compute_confidence(document) if not skip_confidence else 0.0

    result = {
        "supplier_name": supplier_name,
        "invoice_number": invoice_number,
        "do_number": do_number,
        "invoice_date": invoice_date,
        "confidence": confidence,
        "extraction_method": "azure_di",
        "raw_text": raw_text,
        "flags": flags,
        "line_items": line_items,
    }

    result["flags"].extend(_verify_totals(result))
    return result


def _extract_line_items(document) -> List[Dict[str, Any]]:
    """Extract line items from invoice document."""
    line_items: List[Dict[str, Any]] = []

    if not hasattr(document, "fields"):
        return line_items

    items_field = document.fields.get("Items")
    if not items_field:
        return line_items

    # Items are in value_array for array fields
    items_array = None
    if hasattr(items_field, "value_array") and items_field.value_array:
        items_array = items_field.value_array
    elif hasattr(items_field, "value") and items_field.value:
        items_array = items_field.value

    if not items_array:
        return line_items

    for item in items_array:
        # Each item is a DocumentField with value_object
        item_dict = None
        if hasattr(item, "value_object"):
            item_dict = item.value_object
        elif hasattr(item, "value"):
            item_dict = item.value

        if not item_dict:
            continue

        item_flags: List[str] = []

        product_name = _get_nested_field_value(item_dict, "Description")
        quantity_raw = _get_nested_field_value(item_dict, "Quantity")
        unit_price_raw = _get_nested_field_value(item_dict, "UnitPrice")
        total_price_raw = _get_nested_field_value(item_dict, "Amount")

        quantity = _parse_number(quantity_raw)
        if quantity_raw and quantity is None:
            item_flags.append("invalid_quantity_format")
        elif not quantity_raw:
            item_flags.append("missing_quantity")

        unit_price = _parse_number(unit_price_raw)
        if unit_price_raw and unit_price is None:
            item_flags.append("invalid_unit_price_format")
        elif not unit_price_raw:
            item_flags.append("missing_unit_price")

        total_price = _parse_number(total_price_raw)
        if total_price_raw and total_price is None:
            item_flags.append("invalid_total_price_format")
        elif not total_price_raw:
            item_flags.append("missing_total_price")

        if not product_name:
            item_flags.append("missing_product_name")

        # Compute per-item confidence from Quantity, UnitPrice, Amount fields
        item_confidences = []
        for field_name in ["Quantity", "UnitPrice", "Amount"]:
            if field_name in item_dict:
                field = item_dict[field_name]
                if hasattr(field, "confidence") and field.confidence is not None:
                    item_confidences.append(field.confidence)
        item_confidence = (
            sum(item_confidences) / len(item_confidences)
            if item_confidences
            else 0.0
        )

        line_items.append(
            {
                "product_name": product_name,
                "quantity": quantity,
                "unit": None,
                "unit_price": unit_price,
                "total_price": total_price,
                "confidence": item_confidence,
                "flags": item_flags,
            }
        )

    return line_items


def _get_field_value(document, field_name: str) -> Optional[str]:
    """Extract a field value from Azure DI document."""
    if not hasattr(document, "fields"):
        return None

    field = document.fields.get(field_name)
    if not field:
        return None

    # Handle different field value types
    if hasattr(field, "value_string") and field.value_string is not None:
        return str(field.value_string).strip()
    elif hasattr(field, "value_date") and field.value_date is not None:
        return str(field.value_date)
    elif hasattr(field, "value_currency") and field.value_currency is not None:
        if hasattr(field.value_currency, "amount"):
            return str(field.value_currency.amount)
        return str(field.value_currency)
    elif hasattr(field, "value") and field.value is not None:
        val = field.value
        return str(val).strip() if isinstance(val, str) else str(val)

    return None


def _get_nested_field_value(
    item_dict: Dict[str, Any], field_name: str
) -> Optional[str]:
    """Extract a nested field value from item dictionary."""
    if field_name not in item_dict:
        return None

    field = item_dict[field_name]
    if not field:
        return None

    # Handle different field value types
    if hasattr(field, "value_string"):
        return str(field.value_string).strip() if field.value_string else None
    elif hasattr(field, "value_number"):
        return str(field.value_number) if field.value_number else None
    elif hasattr(field, "value_date"):
        return str(field.value_date).strip() if field.value_date else None
    elif hasattr(field, "value_currency"):
        if hasattr(field.value_currency, "amount"):
            return str(field.value_currency.amount)
        return str(field.value_currency)
    elif hasattr(field, "value"):
        value = field.value
        return str(value).strip() if isinstance(value, str) else str(value) if value else None

    return None


def _parse_number(value: Optional[str]) -> Optional[Decimal]:
    """Parse a string to Decimal, return None if invalid."""
    if not value:
        return None

    try:
        return Decimal(value)
    except (InvalidOperation, ValueError, TypeError):
        return None


def _verify_totals(invoice: Dict[str, Any]) -> list:
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


def _needs_gemini_fallback(invoice: Dict[str, Any]) -> bool:
    """True when every line item is missing all three numeric fields."""
    items = invoice.get("line_items", [])
    if not items:
        return True
    return all(
        item.get("quantity") is None
        and item.get("unit_price") is None
        and item.get("total_price") is None
        for item in items
    )


def _merge_with_gemini(azure_invoice: Dict[str, Any], gemini_invoice: Dict[str, Any]) -> Dict[str, Any]:
    """Keep Azure header fields where present; replace line items with Gemini's."""
    merged = {
        "supplier_name": azure_invoice.get("supplier_name") or gemini_invoice.get("supplier_name"),
        "invoice_number": azure_invoice.get("invoice_number") or gemini_invoice.get("invoice_number"),
        "do_number": azure_invoice.get("do_number") or gemini_invoice.get("do_number"),
        "invoice_date": azure_invoice.get("invoice_date") or gemini_invoice.get("invoice_date"),
        "confidence": azure_invoice.get("confidence"),
        "extraction_method": "gemini_fallback",
        "line_items": gemini_invoice.get("line_items", []),
    }

    flags: List[str] = []
    if not merged["supplier_name"]:
        flags.append("missing_supplier_name")
    if not merged["invoice_number"]:
        flags.append("missing_invoice_number")
    if not merged["invoice_date"]:
        flags.append("missing_invoice_date")
    if not merged["line_items"]:
        flags.append("no_line_items_found")
    merged["flags"] = flags

    return merged


def debug_azure_fields(file_path: str):
    """Debug: dump all fields returned by Azure DI."""
    try:
        client = DocumentIntelligenceClient(
            endpoint=AZURE_DI_ENDPOINT, credential=AzureKeyCredential(AZURE_DI_KEY)
        )

        with open(file_path, "rb") as f:
            file_data = f.read()

        poller = client.begin_analyze_document("prebuilt-invoice", file_data)
        result = poller.result()

        print("[DEBUG] Full Azure Response:")
        print(f"  Documents count: {len(result.documents) if result.documents else 0}")
        print(f"  Pages count: {len(result.pages) if result.pages else 0}")

        if result.documents:
            doc = result.documents[0]
            print("\n[DEBUG] Fields:")
            if hasattr(doc, "fields"):
                for field_name, field_obj in doc.fields.items():
                    if hasattr(field_obj, "value"):
                        print(f"  {field_name}: {field_obj.value}")
                    else:
                        print(f"  {field_name}: {field_obj}")
                print("\n[DEBUG] Items field details:")
                items = doc.fields.get("Items")
                if items and hasattr(items, "value"):
                    print(f"  Items type: {type(items.value)}")
                    if items.value:
                        print(f"  Items count: {len(items.value)}")
                        for idx, item in enumerate(items.value):
                            print(f"    Item {idx}: {item}")
                    else:
                        print("  Items: empty/None")
            else:
                print("  No fields attribute")

    except Exception as e:
        import traceback
        print(f"[DEBUG] Error: {e}")
        traceback.print_exc()


def main():
    """CLI entry point. Usage: python step1_extract.py <invoice_pdf_path> [--debug]"""
    if len(sys.argv) < 2:
        print("Usage: python step1_extract.py <invoice_pdf_path> [--debug]")
        sys.exit(1)

    file_path = sys.argv[1]

    if "--debug" in sys.argv:
        debug_azure_fields(file_path)
    else:
        result = extract_invoice(file_path)
        print(json.dumps(result, indent=2, cls=DecimalEncoder))


if __name__ == "__main__":
    main()
