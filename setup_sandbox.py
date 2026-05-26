"""
Sandbox setup — creates minimum schema in an empty Seatable base for
end-to-end testing of the approval workflow.

USAGE:
    1. Rotate the sandbox base's API token in Seatable settings
    2. Add to .env:
         SEATABLE_API_TOKEN_SANDBOX=<new rotated token>
         SEATABLE_BASE_URL_SANDBOX=<sandbox base URL>
    3. Run: python setup_sandbox.py
    4. Switch .env's SEATABLE_API_TOKEN / SEATABLE_BASE_URL to point at
       sandbox values when testing, swap back to production when done.

Idempotent — safe to re-run. Skips tables/columns that already exist.

NOTE: Seatable Python SDK schema operations may vary across SDK versions.
If a call fails, check the SDK docs:
  https://docs.seatable.io/published/dtable-sdk/
And adapt the calls below. The schema definitions in TABLES are the
source of truth — fall back to manual UI creation if needed.
"""

import json
import os
from typing import Dict, List

from dotenv import load_dotenv
from seatable_api import Base
from seatable_api.constants import ColumnTypes

load_dotenv()

SANDBOX_TOKEN = os.getenv("SEATABLE_API_TOKEN_SANDBOX")
SANDBOX_URL = os.getenv("SEATABLE_BASE_URL_SANDBOX") or os.getenv("SEATABLE_SERVER_URL")

if not SANDBOX_TOKEN:
    raise SystemExit("SEATABLE_API_TOKEN_SANDBOX missing from .env")


# ============================================================
# Schema definitions
# ============================================================

# Column type cheat sheet (Seatable common types):
#   "text"           — plain text
#   "long-text"      — multi-line text
#   "number"         — numeric
#   "date"           — date (no time)
#   "checkbox"       — boolean
#   "single-select"  — dropdown
#   "multiple-select"
#   "link"           — link to another table (needs 'data')
#   "auto-number"    — auto-incrementing code
#   "formula"        — computed (needs formula expression)

TABLES = [
    {
        "name": "Suppliers",
        "columns": [
            {"name": "Supplier Name", "type": "text"},
            {"name": "Contact Person", "type": "text"},
            {"name": "Phone", "type": "text"},
            {"name": "Email", "type": "text"},
            {"name": "Notes", "type": "long-text"},
        ],
    },
    {
        "name": "Ingredients",
        "columns": [
            {"name": "Ingredient Name", "type": "text"},
            {"name": "Base Unit", "type": "single-select",
             "data": {"options": [
                 {"name": "g"}, {"name": "ml"}, {"name": "pcs"}, {"name": "loaf"}
             ]}},
            {"name": "Wastage %", "type": "number"},
            {"name": "Yield %", "type": "number"},
            {"name": "Ingredient Code", "type": "auto-number",
             "data": {"format": "ING-00000"}},
        ],
    },
    {
        "name": "Supplier Products",
        "columns": [
            {"name": "Supplier Product Name", "type": "text"},
            # Links populated AFTER all tables exist (see _add_links below)
            {"name": "Pack Size", "type": "text"},
            {"name": "Unit Quantity", "type": "number"},
            {"name": "Unit of Measure", "type": "text"},
            {"name": "Price per Pack", "type": "number"},
            {"name": "Active Status", "type": "checkbox"},
            {"name": "Default Supplier", "type": "checkbox"},
            {"name": "Date Updated", "type": "date"},
            {"name": "SP Code", "type": "auto-number",
             "data": {"format": "SP-00000"}},
        ],
    },
    {
        "name": "Invoices",
        "columns": [
            {"name": "Name", "type": "text"},  # default first column
            {"name": "Invoice Number", "type": "text"},
            {"name": "Invoice Date", "type": "date"},
            {"name": "PDF/Image Attachment", "type": "file"},
            {"name": "Processed", "type": "checkbox"},
            {"name": "Notes", "type": "long-text"},
        ],
    },
    {
        "name": "Price History",
        "columns": [
            {"name": "No", "type": "auto-number",
             "data": {"format": "PH-00000"}},
            {"name": "Old Price", "type": "number"},
            {"name": "New Price", "type": "number"},
            {"name": "Change %", "type": "number"},
            {"name": "Flagged By", "type": "text"},
        ],
    },
]

# Link columns added after all base tables exist
LINKS = [
    # (table_with_link, column_name, target_table)
    ("Supplier Products", "Supplier",        "Suppliers"),
    ("Supplier Products", "Ingredients",     "Ingredients"),
    ("Invoices",          "Supplier (Link)", "Suppliers"),
    ("Price History",     "Supplier product (link)", "Supplier Products"),
    ("Price History",     "Invoice Reference",       "Invoices"),
]

# Seed rows for end-to-end testing
SEED = {
    "Suppliers": [
        {"Supplier Name": "MOOI TIAN TRADING"},  # Match exact case from invoice
    ],
    "Ingredients": [
        {"Ingredient Name": "Tomato (General)", "Base Unit": "g",
         "Wastage %": 5, "Yield %": 0.95},
        {"Ingredient Name": "Yellow Lemon", "Base Unit": "pcs",
         "Wastage %": 0, "Yield %": 1},
        {"Ingredient Name": "Serai (Lemongrass)", "Base Unit": "g",
         "Wastage %": 10, "Yield %": 0.9},
        {"Ingredient Name": "Old Ginger", "Base Unit": "g",
         "Wastage %": 5, "Yield %": 0.95},
    ],
}


# ============================================================
# Setup
# ============================================================

def _get_column_type_enum(type_str: str):
    """Convert string column type to ColumnTypes enum."""
    type_map = {
        'text': ColumnTypes.TEXT,
        'long-text': ColumnTypes.LONG_TEXT,
        'number': ColumnTypes.NUMBER,
        'date': ColumnTypes.DATE,
        'checkbox': ColumnTypes.CHECKBOX,
        'single-select': ColumnTypes.SINGLE_SELECT,
        'multiple-select': ColumnTypes.MULTIPLE_SELECT,
        'link': ColumnTypes.LINK,
        'auto-number': ColumnTypes.AUTO_NUMBER,
        'formula': ColumnTypes.FORMULA,
        'file': ColumnTypes.FILE,
    }
    return type_map.get(type_str)


def _connect() -> Base:
    base = Base(SANDBOX_TOKEN, SANDBOX_URL)
    base.auth()
    return base


def _existing_table_names(base: Base) -> List[str]:
    """Returns list of table names already in the base."""
    metadata = base.get_metadata()
    return [t["name"] for t in metadata.get("tables", [])]


def create_tables(base: Base) -> None:
    """Create tables without columns (links added in separate pass)."""
    existing = _existing_table_names(base)
    print(f"[setup] Existing tables: {existing or '(empty base)'}")

    for table_spec in TABLES:
        name = table_spec["name"]
        if name in existing:
            print(f"[setup]   Skip {name!r} (already exists)")
            continue

        try:
            # Create table without columns (API Gateway limitation)
            base.add_table(name)
            print(f"[setup]   Created table {name!r}")

            # Add columns after table creation
            cols = [c for c in table_spec["columns"] if c["type"] != "link"]
            for col in cols:
                try:
                    col_name = col.get("name")
                    col_type_str = col.get("type")
                    col_data = col.get("data")
                    col_type_enum = _get_column_type_enum(col_type_str)

                    if not col_type_enum:
                        print(f"[setup]     WARNING: unknown column type {col_type_str!r}")
                        continue

                    base.insert_column(
                        table_name=name,
                        column_name=col_name,
                        column_type=col_type_enum,
                        column_data=col_data
                    )
                except Exception as e:
                    print(f"[setup]     ERROR adding column {col_name!r}: {e}")

        except Exception as e:
            print(f"[setup]   ERROR creating {name!r}: {e}")
            print(f"[setup]   (fall back to manual creation in Seatable UI)")


def create_links(base: Base) -> None:
    """Add link columns between tables once all tables exist."""
    metadata = base.get_metadata()
    by_name = {t["name"]: t for t in metadata.get("tables", [])}

    for table_name, col_name, target in LINKS:
        if table_name not in by_name or target not in by_name:
            print(f"[setup]   Skip link {table_name}.{col_name} -> {target} (table missing)")
            continue

        existing_cols = [c["name"] for c in by_name[table_name].get("columns", [])]
        if col_name in existing_cols:
            print(f"[setup]   Skip link {table_name}.{col_name} (exists)")
            continue

        try:
            base.insert_column(
                table_name=table_name,
                column_name=col_name,
                column_type=ColumnTypes.LINK,
                column_data={"table": target},
            )
            print(f"[setup]   Linked {table_name}.{col_name} -> {target}")
        except Exception as e:
            print(f"[setup]   ERROR linking {table_name}.{col_name}: {e}")
            print(f"[setup]   (fall back to manual creation in Seatable UI)")


def seed_data(base: Base) -> None:
    """Insert minimal seed rows for testing. Skips if already populated."""
    # Suppliers
    sup_rows = base.list_rows("Suppliers")
    if not sup_rows:
        for row in SEED["Suppliers"]:
            base.append_row("Suppliers", row)
        print(f"[setup] Seeded {len(SEED['Suppliers'])} supplier(s)")
    else:
        print(f"[setup] Suppliers already populated ({len(sup_rows)} rows)")

    # Ingredients
    ing_rows = base.list_rows("Ingredients")
    if not ing_rows:
        for row in SEED["Ingredients"]:
            base.append_row("Ingredients", row)
        print(f"[setup] Seeded {len(SEED['Ingredients'])} ingredient(s)")
    else:
        print(f"[setup] Ingredients already populated ({len(ing_rows)} rows)")

    # Supplier Products — needs supplier + ingredient row IDs for linking
    sp_rows = base.list_rows("Supplier Products")
    if not sp_rows:
        suppliers = {r["Supplier Name"]: r["_id"] for r in base.list_rows("Suppliers")}
        ingredients = {r["Ingredient Name"]: r["_id"] for r in base.list_rows("Ingredients")}

        mooi_id = suppliers.get("MOOI TIAN TRADING")
        tomato_id = ingredients.get("Tomato (General)")
        lemon_id = ingredients.get("Yellow Lemon")
        serai_id = ingredients.get("Serai (Lemongrass)")
        ginger_id = ingredients.get("Old Ginger")

        sp_items = [
            {
                "name": "TOMATO",
                "ingredient_id": tomato_id,
                "pack_size": "per kg",
                "unit_qty": 1000,
                "uom": "g",
                "price": 4.00,
            },
            {
                "name": "YELLOW LEMON",
                "ingredient_id": lemon_id,
                "pack_size": "per biji",
                "unit_qty": 1,
                "uom": "biji",
                "price": 1.90,
            },
            {
                "name": "SERAI",
                "ingredient_id": serai_id,
                "pack_size": "per kg",
                "unit_qty": 1000,
                "uom": "g",
                "price": 4.50,
            },
            {
                "name": "OLD GINGER",
                "ingredient_id": ginger_id,
                "pack_size": "per kg",
                "unit_qty": 1000,
                "uom": "g",
                "price": 8.00,
            },
        ]

        for sp in sp_items:
            if mooi_id and sp["ingredient_id"]:
                base.append_row("Supplier Products", {
                    "Supplier Product Name": sp["name"],
                    "Supplier": [mooi_id],
                    "Ingredients": [sp["ingredient_id"]],
                    "Pack Size": sp["pack_size"],
                    "Unit Quantity": sp["unit_qty"],
                    "Unit of Measure": sp["uom"],
                    "Price per Pack": sp["price"],
                    "Active Status": True,
                    "Default Supplier": True,
                })
                print(f"[setup]   Seeded SP: {sp['name']} @ RM{sp['price']:.2f}/{sp['uom']}")
    else:
        print(f"[setup] Supplier Products already populated ({len(sp_rows)} rows)")


def main():
    print(f"[setup] Connecting to sandbox base…")
    base = _connect()
    base.timeout = 60  # Increase timeout for link column creation
    print(f"[setup] Connected.\n")

    print("[setup] Pass 1 — create tables")
    create_tables(base)

    print("\n[setup] Pass 2 — add link columns")
    create_links(base)

    print("\n[setup] Pass 3 — seed data")
    seed_data(base)

    print("\n[setup] Done.")
    print("[setup] Next: switch .env's SEATABLE_API_TOKEN to the sandbox token,")
    print("[setup] then run the approval workflow against this base.")


if __name__ == "__main__":
    main()
