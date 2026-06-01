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
    # Imperial mass (base: gram)
    "lb":         ("gram", Decimal("453.592")),
    "lbs":        ("gram", Decimal("453.592")),
    "pound":      ("gram", Decimal("453.592")),
    "pounds":     ("gram", Decimal("453.592")),
    "oz":         ("gram", Decimal("28.3495")),
    "ounce":      ("gram", Decimal("28.3495")),
    "ounces":     ("gram", Decimal("28.3495")),
    # Imperial volume (base: ml)
    "gal":        ("ml",   Decimal("3785.41")),
    "gallon":     ("ml",   Decimal("3785.41")),
    "gallons":    ("ml",   Decimal("3785.41")),
    "floz":       ("ml",   Decimal("29.5735")),
    "fl oz":      ("ml",   Decimal("29.5735")),
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


def to_base_qty(unit_quantity, uom: str) -> Optional[float]:
    """
    Convert unit_quantity × uom → equivalent quantity in the smallest base unit.
    Returns grams for mass units, ml for volume units. Returns None for
    container/unknown units (PCS, BTL, TUB, etc.) or missing inputs.

    Examples: (1, 'KG') → 1000.0   (500, 'G') → 500.0   (1.5, 'L') → 1500.0
    """
    if unit_quantity is None or not uom:
        return None
    clean = _clean_uom(uom)
    entry = STANDARD.get(clean)
    if not entry:
        return None
    _, factor = entry
    try:
        return float(Decimal(str(unit_quantity)) * factor)
    except (ValueError, TypeError, ArithmeticError):
        return None


def to_seatable_base(unit_quantity, uom: str) -> Optional[tuple]:
    """
    Convert unit_quantity + uom into the values to store in Seatable.

    Follows the existing convention: Unit Quantity is always stored in the
    smallest base unit (grams for mass, ml for volume), never in KG/L/LB etc.
    Container units (PCS, BTL, sachets, etc.) are returned unchanged.

    Returns (seatable_unit_qty: float, seatable_uom: str) or None if inputs missing.

    Examples:
        (1,    'kg')     → (1000.0, 'G')
        (1.5,  'L')      → (1500.0, 'ML')
        (500,  'G')      → (500.0,  'G')
        (1,    'lb')     → (453.592,'G')
        (100,  'sachets')→ (100,    'SACHETS')   # container, no conversion
        (1,    'PCS')    → (1,      'PCS')        # container, no conversion
    """
    if unit_quantity is None or not uom:
        return None
    clean = _clean_uom(uom)
    entry = STANDARD.get(clean)
    if entry:
        # Measurement unit — convert to base
        base_unit_name, factor = entry
        try:
            base_qty = float(Decimal(str(unit_quantity)) * factor)
        except (ValueError, TypeError, ArithmeticError):
            return None
        seatable_uom = "G" if base_unit_name == "gram" else "ML"
        return base_qty, seatable_uom
    else:
        # Container unit — store as-is, uppercase
        return unit_quantity, uom.upper()


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