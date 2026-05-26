"""
Unit normalization for invoice → Seatable Price per Pack comparison.

Only handles measurement units (g/kg/ml/L). Container units (pcs/ctn/btl/pkt)
are intentionally not normalized — trust per-product Seatable Unit Quantity + UoM.
"""

import re
import unicodedata
from decimal import Decimal
from typing import Optional

# Canonical mass/volume units → (base_unit, factor_to_base)
STANDARD: dict[str, tuple[str, Decimal]] = {
    # Mass (base: gram)
    "g":          ("gram", Decimal("1")),
    "gr":         ("gram", Decimal("1")),
    "gram":       ("gram", Decimal("1")),
    "grams":      ("gram", Decimal("1")),
    "kg":         ("gram", Decimal("1000")),
    "kgs":        ("gram", Decimal("1000")),
    "kilo":       ("gram", Decimal("1000")),
    "kilos":      ("gram", Decimal("1000")),
    "kilogram":   ("gram", Decimal("1000")),
    "kilograms":  ("gram", Decimal("1000")),
    # Volume (base: ml)
    "ml":         ("ml",   Decimal("1")),
    "mls":        ("ml",   Decimal("1")),
    "millilitre": ("ml",   Decimal("1")),
    "milliliter": ("ml",   Decimal("1")),
    "l":          ("ml",   Decimal("1000")),
    "litre":      ("ml",   Decimal("1000")),
    "liter":      ("ml",   Decimal("1000")),
    "litres":     ("ml",   Decimal("1000")),
    "liters":     ("ml",   Decimal("1000")),
}


def _clean_uom(uom: str) -> str:
    """
    Normalize unit string: strip 'per ' prefix, lowercase, NFC unicode, collapse spaces.
    Handles: 'per kg' → 'kg', 'PER KG' → 'kg', '  KG  ' → 'kg'.
    """
    if not uom:
        return ""
    s = unicodedata.normalize("NFC", str(uom)).lower().strip()
    if s.startswith("per "):
        s = s[4:].strip()
    return s


def _parse_unit(raw: str) -> tuple[Decimal, str]:
    """
    Parse embedded-quantity units like '500g' → (500, 'g'), '250ml' → (250, 'ml').
    Plain 'kg' → (1, 'kg').
    """
    if not raw:
        return Decimal("1"), ""
    cleaned = _clean_uom(raw)
    m = re.match(r"^(\d+\.?\d*)\s*([a-zA-Z]+)$", cleaned)
    if m:
        return Decimal(m.group(1)), m.group(2).lower()
    return Decimal("1"), cleaned


def get_base_unit_info(invoice_unit: str) -> Optional[tuple[Decimal, str]]:
    """Returns (factor_to_base, base_unit) or None if unit unknown."""
    _, clean_unit = _parse_unit(invoice_unit)
    entry = STANDARD.get(clean_unit)
    if not entry:
        return None
    base_unit, factor = entry
    return factor, base_unit


def normalize_invoice_price(
    invoice_unit_price,
    invoice_unit: str,
    supplier_unit_quantity,
    supplier_unit_of_measure: str,
) -> Optional[Decimal]:
    """
    Convert invoice unit price → equivalent Price per Pack matching supplier's pack definition.

    Returns None if:
    - Either unit is unknown / not a measurement
    - Units are incompatible (mass vs volume)
    - Quantity inputs aren't numeric

    Example:
        invoice: RM 74 per kg
        supplier pack: 1000 g (Unit Quantity=1000, UoM='G')
        → returns RM 74.00 (price per pack)

        invoice: RM 0.50 per 100g (embedded qty)
        supplier pack: 1 kg (Unit Quantity=1, UoM='kg')
        → returns RM 5.00 (price per pack)
    """
    # Parse invoice unit (handles embedded qty like '250ml')
    inv_qty_factor, inv_unit_clean = _parse_unit(invoice_unit)
    if inv_unit_clean not in STANDARD:
        return None
    inv_base, inv_to_base = STANDARD[inv_unit_clean]

    # Parse supplier UoM (strips 'per ' prefix)
    sup_unit_clean = _clean_uom(supplier_unit_of_measure)
    if sup_unit_clean not in STANDARD:
        return None
    sup_base, sup_to_base = STANDARD[sup_unit_clean]

    # Reject incompatible classes (mass vs volume)
    if inv_base != sup_base:
        return None

    try:
        # invoice price per inv_unit → price per base unit (gram or ml)
        # → × supplier qty (in supplier units) × sup_to_base → price per pack
        price_per_base = Decimal(str(invoice_unit_price)) / inv_qty_factor / inv_to_base
        price_per_pack = price_per_base * sup_to_base * Decimal(str(supplier_unit_quantity))
        return price_per_pack
    except (ValueError, TypeError, ArithmeticError):
        return None