from decimal import Decimal
from typing import Any, Dict, List

_TOLERANCE = Decimal("0.01")


def check_invoice(invoice: dict) -> List[Dict[str, Any]]:
    """
    Returns a list of mismatch dicts. Two checks:
      1. qty * unit_price ≈ total_price per non-crossed line (1% tolerance)
      2. sum(line total_prices) ≈ invoice_total (1% tolerance)
    Crossed-out items are excluded from both checks.
    """
    warnings = []
    active_items = [i for i in invoice.get("line_items", []) if not i.get("crossed_out")]
    invoice_total = invoice.get("invoice_total")

    computed_sum = Decimal("0")

    for item in active_items:
        qty = item.get("quantity")
        unit_price = item.get("unit_price")
        total_price = item.get("total_price")
        name = item.get("product_name") or "?"

        if qty is not None and unit_price is not None and total_price is not None:
            expected = qty * unit_price
            if not _within(expected, total_price):
                warnings.append({
                    "type": "line_total_mismatch",
                    "product": name,
                    "qty": float(qty),
                    "unit_price": float(unit_price),
                    "expected": float(expected.quantize(Decimal("0.01"))),
                    "actual": float(total_price),
                })

        if total_price is not None:
            computed_sum += total_price

    if invoice_total is not None and computed_sum > 0:
        if not _within(computed_sum, invoice_total):
            warnings.append({
                "type": "invoice_total_mismatch",
                "computed": float(computed_sum.quantize(Decimal("0.01"))),
                "invoice_total": float(invoice_total),
            })

    return warnings


def _within(a: Decimal, b: Decimal) -> bool:
    if b == 0:
        return a == 0
    return abs(a - b) / abs(b) <= _TOLERANCE
