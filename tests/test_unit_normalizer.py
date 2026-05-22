import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "skills" / "parse_invoice"))

from unit_normalizer import normalize_invoice_price, _parse_unit


def test_kg_to_gram():
    assert normalize_invoice_price(16, "kg", 1000, "gram") == Decimal("16")


def test_g_price_per_gram():
    assert normalize_invoice_price(0.016, "g", 1000, "gram") == Decimal("16")


def test_g_expensive():
    # RM8/gram × 1000g = RM8000 per pack — valid, just expensive
    assert normalize_invoice_price(8, "g", 1000, "gram") == Decimal("8000")


def test_L_to_ml():
    assert normalize_invoice_price(15, "L", 1000, "ml") == Decimal("15")


def test_unit_mismatch_returns_none():
    assert normalize_invoice_price(16, "kg", 1000, "ml") is None


def test_unknown_unit_returns_none():
    assert normalize_invoice_price(16, "unknown_unit", 1000, "gram") is None


def test_embedded_quantity_unit():
    # Gemini extracts unit="500g" instead of unit="g", quantity=500
    # RM8 per 500g pack → RM0.016/g → RM16 per 1000g pack
    assert normalize_invoice_price(8, "500g", 1000, "gram") == Decimal("16")


def test_parse_unit_embedded():
    factor, unit = _parse_unit("500g")
    assert factor == Decimal("500")
    assert unit == "g"


def test_parse_unit_plain():
    factor, unit = _parse_unit("kg")
    assert factor == Decimal("1")
    assert unit == "kg"
