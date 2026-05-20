import json
import sys
import os
import time
from pathlib import Path
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential

load_dotenv()

AZURE_DI_ENDPOINT = os.getenv("AZURE_DI_ENDPOINT")
AZURE_DI_KEY = os.getenv("AZURE_DI_KEY")


def extract_invoice(file_path: str) -> Dict[str, Any]:
    """Extract invoice data from PDF using Azure Document Intelligence."""
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


def _process_invoices(result) -> List[Dict[str, Any]]:
    """Group documents by invoice number (primary) or DO number (fallback)."""
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

    for key, documents in invoice_groups.items():
        for doc in documents:
            invoice_obj = _build_invoice_object(doc)
            invoices.append(invoice_obj)

    return invoices


def _build_invoice_object(document) -> Dict[str, Any]:
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

    return {
        "supplier_name": supplier_name,
        "invoice_number": invoice_number,
        "do_number": do_number,
        "invoice_date": invoice_date,
        "flags": flags,
        "line_items": line_items,
    }


def _extract_line_items(document) -> List[Dict[str, Any]]:
    """Extract line items from invoice document."""
    line_items: List[Dict[str, Any]] = []

    if not hasattr(document, "fields"):
        return line_items

    items_field = document.fields.get("Items")
    if not items_field or not hasattr(items_field, "value") or not items_field.value:
        return line_items

    for item in items_field.value:
        if not hasattr(item, "value"):
            continue

        item_dict = item.value
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

        line_items.append(
            {
                "product_name": product_name,
                "quantity": quantity,
                "unit_price": unit_price,
                "total_price": total_price,
                "flags": item_flags,
            }
        )

    return line_items


def _get_field_value(document, field_name: str) -> Optional[str]:
    """Extract a field value from Azure DI document."""
    if not hasattr(document, "fields"):
        return None

    field = document.fields.get(field_name)
    if not field or not hasattr(field, "value"):
        return None

    value = field.value
    return str(value).strip() if value else None


def _get_nested_field_value(
    item_dict: Dict[str, Any], field_name: str
) -> Optional[str]:
    """Extract a nested field value from item dictionary."""
    if field_name not in item_dict:
        return None

    field = item_dict[field_name]
    if not field or not hasattr(field, "value"):
        return None

    value = field.value
    return str(value).strip() if value else None


def _parse_number(value: Optional[str]) -> Optional[float]:
    """Parse a string to float, return None if invalid."""
    if not value:
        return None

    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def main():
    """CLI entry point. Usage: python step1_extract.py <invoice_pdf_path>"""
    if len(sys.argv) < 2:
        print("Usage: python step1_extract.py <invoice_pdf_path>")
        sys.exit(1)

    file_path = sys.argv[1]
    result = extract_invoice(file_path)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
