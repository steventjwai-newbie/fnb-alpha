import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "skills" / "parse_invoice"))

from step2_compare import _match_supplier

SUPPLIERS = [
    {"_id": "aaa", "Supplier Name": "Mooi Tian Trading", "Business Reg No": "202001234567", "Tax ID": "W10-1234-56789012"},
    {"_id": "bbb", "Supplier Name": "Seng Kong Fishery", "Business Reg No": "199901234567", "Tax ID": ""},
    {"_id": "ccc", "Supplier Name": "Pak Brothers", "Business Reg No": "", "Tax ID": ""},
]


def test_brn_exact_match():
    result = _match_supplier("宏记", SUPPLIERS, brn="202001234567")
    assert result is not None
    assert result["Supplier Name"] == "Mooi Tian Trading"


def test_brn_match_with_dashes():
    result = _match_supplier("", SUPPLIERS, brn="2020-01234567")
    assert result is not None
    assert result["Supplier Name"] == "Mooi Tian Trading"


def test_tax_id_exact_match():
    result = _match_supplier("unknown supplier", SUPPLIERS, tax_id="W10-1234-56789012")
    assert result is not None
    assert result["Supplier Name"] == "Mooi Tian Trading"


def test_tax_id_case_insensitive():
    result = _match_supplier("", SUPPLIERS, tax_id="w10-1234-56789012")
    assert result is not None
    assert result["Supplier Name"] == "Mooi Tian Trading"


def test_brn_takes_priority_over_fuzzy():
    # BRN points to Mooi Tian even if name is "Seng Kong"
    result = _match_supplier("Seng Kong Fishery", SUPPLIERS, brn="202001234567")
    assert result["Supplier Name"] == "Mooi Tian Trading"


def test_fallback_to_fuzzy_when_no_brn():
    result = _match_supplier("Pak Brothers", SUPPLIERS)
    assert result is not None
    assert result["Supplier Name"] == "Pak Brothers"


def test_no_match_returns_none():
    result = _match_supplier("Unknown Vendor XYZ", SUPPLIERS)
    assert result is None


def test_empty_brn_skips_brn_match():
    # Empty BRN should not match anything, fall through to name match
    result = _match_supplier("Seng Kong Fishery", SUPPLIERS, brn="")
    assert result is not None
    assert result["Supplier Name"] == "Seng Kong Fishery"
