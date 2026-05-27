# Seatable Link Columns: Workaround Solution ✅

## The Problem

The Seatable SDK's `append_row()` and `update_row()` methods **do NOT support writing link columns** directly. Attempts to set link columns silently fail:

```python
# This silently fails:
base.update_row("Supplier Products", sp_id, {
    "Supplier": [supplier_id],      # ← Silently ignored
    "Ingredients": [ingredient_id]  # ← Silently ignored
})
```

## The Solution: `add_link()` API

Use `base.add_link()` with the link column ID to properly create relationships:

```python
# Step 1: Get the link column ID
link_id = base.get_column_link_id("Supplier Products", "Supplier")

# Step 2: Create the link
base.add_link(
    link_id,
    "Supplier Products",  # Table with link column
    "Suppliers",          # Target table
    sp_row_id,           # Row ID in Supplier Products
    supplier_row_id      # Row ID in Suppliers
)
```

## Implementation in seatable_writer.py

### New Functions Added

**`add_row_link()`** — Generic function to create any row link:
```python
add_row_link(
    base,
    link_column_table="Supplier Products",
    link_column_name="Supplier",
    link_column_row_id=sp_id,
    target_table="Suppliers",
    target_row_id=supplier_id,
)
```

**`link_supplier_product()`** — Convenience function for SP links:
```python
link_supplier_product(
    base,
    sp_row_id=sp_id,
    supplier_row_id=supplier_id,
    ingredient_row_id=ingredient_id,
)
```

### Updated Functions

**`upsert_invoice_row()`** — Now uses `add_link()` to link Supplier after creating invoice row.

## Tested & Verified ✅

| Link Type | Status | Evidence |
|-----------|--------|----------|
| Supplier Products → Suppliers | ✅ Works | Turmeric (SP-7) linked to Mooi Tian |
| Supplier Products → Ingredients | ✅ Works | Turmeric SP-7 linked to Turmeric ingredient |
| Invoices → Suppliers | ✅ Should work | Same pattern as SP links |

## Example: Turmeric Setup

```python
# Find IDs
sp_id = "ZVyIaPQpSF6PdpeorqiyEg"  # SP-7 Turmeric
supplier_id = "Lgn90LUKSkiFSS5BYHTiNw"  # Mooi Tian
ingredient_id = "PMh4YFO9TPSEmifOyLkrNg"  # Turmeric Powder

# Create links
link_supplier_product(
    base,
    sp_row_id=sp_id,
    supplier_row_id=supplier_id,
    ingredient_row_id=ingredient_id,
)

# Result: ✓ SP-7 is now linked in Seatable UI
```

## Limitations

- `add_link()` can only create one link at a time (no batch)
- Link column must exist in the table first
- Non-fatal if link creation fails (workflow continues)

## References

- [SeaTable API: Create Row Link](https://api.seatable.com/reference/createrowlink)
- [SeaTable Developer Manual: Links](https://developer.seatable.com/scripts/python/objects/links/)
- [SeaTable Python API: add_link()](https://github.com/seatable/seatable-api-python)
