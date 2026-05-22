import re
from decimal import Decimal
from functools import lru_cache
from typing import Optional

STANDARD: dict[str, tuple[str, Decimal]] = {
    "g":         ("gram", Decimal("1")),
    "gram":      ("gram", Decimal("1")),
    "kg":        ("gram", Decimal("1000")),
    "kilogram":  ("gram", Decimal("1000")),
    "ml":        ("ml",   Decimal("1")),
    "l":         ("ml",   Decimal("1000")),
    "liter":     ("ml",   Decimal("1000")),
    "litre":     ("ml",   Decimal("1000")),
}

_conversion_cache: dict[str, Optional[Decimal]] = {}


def get_base_unit_info(invoice_unit: str) -> tuple[Decimal, str] | None:
    """
    Returns (divisor, base_unit_of_measure) for a given invoice unit, or None if unknown.
    e.g. "kg" → (Decimal("1000"), "gram"), "L" → (Decimal("1000"), "ml")
    """
    _, clean_unit = _parse_unit(invoice_unit)
    entry = STANDARD.get(clean_unit)
    if not entry:
        return None
    base_unit, divisor = entry
    return divisor, base_unit


def _parse_unit(raw: str) -> tuple[Decimal, str]:
    """Handle embedded-quantity units like '500g', '250ml' → (500, 'g'), (250, 'ml')."""
    m = re.match(r'^(\d+\.?\d*)\s*([a-zA-Z]+)$', raw.strip())
    if m:
        return Decimal(m.group(1)), m.group(2).lower()
    return Decimal("1"), raw.strip().lower()


def get_unit_conversion(invoice_unit: str) -> Optional[Decimal]:
    """Query Seatable Unit Conversions table. Returns multiplier (invoice unit → base unit) or None."""
    key = invoice_unit.lower().strip()
    if key in _conversion_cache:
        return _conversion_cache[key]

    try:
        import os
        from seatable_api import Base
        from dotenv import load_dotenv
        load_dotenv()

        base = Base(os.getenv("SEATABLE_API_TOKEN"), os.getenv("SEATABLE_SERVER_URL"))
        base.auth()
        rows = base.list_rows("Unit Conversions", view_name="Default View")
        for row in rows:
            if (row.get("From Unit") or "").lower().strip() == key:
                multiplier = Decimal(str(row["Conversion Multiplier"]))
                _conversion_cache[key] = multiplier
                return multiplier
    except Exception as e:
        print(f"[LOG] Unit conversion lookup failed for '{invoice_unit}': {e}")

    _conversion_cache[key] = None
    return None


def normalize_invoice_price(
    invoice_unit_price: float | Decimal,
    invoice_unit: str,
    supplier_unit_quantity: float | Decimal,
    supplier_unit_of_measure: str,
) -> Optional[Decimal]:
    """
    Convert invoice price to Price per Pack matching the supplier's pack definition.

    Returns None if units are incompatible or unresolvable.
    """
    quantity_factor, clean_unit = _parse_unit(invoice_unit)
    effective_price = Decimal(str(invoice_unit_price)) / quantity_factor

    if clean_unit in STANDARD:
        base_unit, divisor = STANDARD[clean_unit]
        if base_unit != supplier_unit_of_measure.lower().strip():
            return None
        price_per_base = effective_price / divisor
        return price_per_base * Decimal(str(supplier_unit_quantity))

    multiplier = get_unit_conversion(clean_unit)
    if multiplier is None:
        return None

    price_per_base = effective_price / multiplier
    return price_per_base * Decimal(str(supplier_unit_quantity))
