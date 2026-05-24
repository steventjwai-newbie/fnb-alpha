from decimal import Decimal
from typing import Any, Dict, List

_TOLERANCE = Decimal("0.01")


def fill_missing_totals(invoice: dict) -> None:
    """
    Fill in missing values in-place before verification:
      - total_price per line: computed from qty * unit_price if absent
      - invoice_total: computed from sum of line totals if absent
    Computed fields are flagged so callers can distinguish extracted vs derived.
    Crossed-out items are excluded from the invoice_total sum.
    """
    active = [i for i in invoice.get("line_items", []) if not i.get("crossed_out")]

    for item in active:
        if item.get("total_price") is None:
            qty = item.get("quantity")
            up = item.get("unit_price")
            if qty is not None and up is not None:
                try:
                    item["total_price"] = Decimal(str(qty)) * Decimal(str(up))
                    item.setdefault("flags", []).append("total_price_computed")
                except Exception:
                    pass

    if invoice.get("invoice_total") is None:
        total = Decimal("0")
        for item in active:
            tp = item.get("total_price")
            if tp is not None:
                try:
                    total += Decimal(str(tp))
                except Exception:
                    pass
        if total > 0:
            invoice["invoice_total"] = total
            invoice.setdefault("flags", []).append("invoice_total_computed")


def check_invoice(invoice: dict) -> List[Dict[str, Any]]:
    """
    Fill missing totals then verify:
      1. qty * unit_price ≈ total_price per non-crossed line (1% tolerance)
      2. sum(line total_prices) ≈ invoice_total (1% tolerance)
    Returns list of mismatch dicts. Mutates invoice to fill missing values.
    """
    fill_missing_totals(invoice)

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
            expected = Decimal(str(qty)) * Decimal(str(unit_price))
            actual = Decimal(str(total_price))
            if "total_price_computed" not in item.get("flags", []) and not _within(expected, actual):
                warnings.append({
                    "type": "line_total_mismatch",
                    "product": name,
                    "qty": float(qty),
                    "unit_price": float(unit_price),
                    "expected": float(expected.quantize(Decimal("0.01"))),
                    "actual": float(actual),
                })

        if total_price is not None:
            computed_sum += Decimal(str(total_price))

    if invoice_total is not None and computed_sum > 0:
        inv_total_dec = Decimal(str(invoice_total))
        if "invoice_total_computed" not in invoice.get("flags", []) and not _within(computed_sum, inv_total_dec):
            warnings.append({
                "type": "invoice_total_mismatch",
                "computed": float(computed_sum.quantize(Decimal("0.01"))),
                "invoice_total": float(inv_total_dec),
            })

    return warnings


def _within(a: Decimal, b: Decimal) -> bool:
    if b == 0:
        return a == 0
    return abs(a - b) / abs(b) <= _TOLERANCE
