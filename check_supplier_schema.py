#!/usr/bin/env python3
"""Check Suppliers table schema."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src" / "skills" / "parse_invoice"))

from seatable_writer import _base

base = _base()

# Get one supplier row and check all fields
suppliers = base.list_rows("Suppliers", limit=1)

if suppliers:
    row = suppliers[0]
    print("First supplier row fields:")
    print()
    for key, value in sorted(row.items()):
        if not key.startswith("_"):
            print(f"  {key}: {value}")
else:
    print("No suppliers found")
