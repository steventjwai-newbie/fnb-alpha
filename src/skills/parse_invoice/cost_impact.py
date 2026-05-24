import argparse
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv
from rapidfuzz import fuzz, process
from seatable_api import Base

load_dotenv()

_here = str(Path(__file__).parent)
if _here not in sys.path:
    sys.path.insert(0, _here)

from unit_normalizer import get_base_unit_info

SEATABLE_API_TOKEN = os.getenv("SEATABLE_API_TOKEN")
SEATABLE_BASE_URL = os.getenv("SEATABLE_BASE_URL")

_TELEGRAM_CHAR_LIMIT = 4000

_recipe_list_cache: Optional[List[Dict]] = None
_recipe_ingredients_cache: Optional[List[Dict]] = None
_ingredients_cache: Optional[List[Dict]] = None
_supplier_products_cache: Optional[List[Dict]] = None


def _seatable_base() -> Base:
    base = Base(SEATABLE_API_TOKEN, SEATABLE_BASE_URL)
    base.auth()
    return base


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _get_linked_ids(cell_value) -> List[str]:
    if not cell_value or not isinstance(cell_value, list):
        return []
    ids = []
    for item in cell_value:
        if isinstance(item, dict):
            row_id = item.get("row_id") or item.get("_id") or ""
            if row_id:
                ids.append(row_id)
    return ids


def _list_all(base: Base, table_name: str) -> List[Dict]:
    rows = []
    offset = 0
    while True:
        batch = base.list_rows(table_name, start=offset, limit=1000)
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += len(batch)
    print(f"[LOG] Loaded {len(rows)} rows from {table_name}")
    return rows


def _chunk_message(header: str, body_lines: List[str]) -> List[str]:
    """Split header + body lines into <=_TELEGRAM_CHAR_LIMIT-char chunks."""
    chunks = []
    current = header
    for line in body_lines:
        candidate = current + "\n" + line
        if len(candidate) > _TELEGRAM_CHAR_LIMIT and current != header:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _load_recipes(base: Base) -> List[Dict]:
    global _recipe_list_cache
    if _recipe_list_cache is None:
        _recipe_list_cache = _list_all(base, "Recipe List (Full)")
    return _recipe_list_cache


def _load_recipe_ingredients(base: Base) -> List[Dict]:
    global _recipe_ingredients_cache
    if _recipe_ingredients_cache is None:
        _recipe_ingredients_cache = _list_all(base, "Recipe Ingredients (Final)")
    return _recipe_ingredients_cache


def _load_ingredients(base: Base) -> List[Dict]:
    global _ingredients_cache
    if _ingredients_cache is None:
        _ingredients_cache = _list_all(base, "Ingredients")
    return _ingredients_cache


def _load_supplier_products(base: Base) -> List[Dict]:
    global _supplier_products_cache
    if _supplier_products_cache is None:
        _supplier_products_cache = _list_all(base, "Supplier Products")
    return _supplier_products_cache


def _debug_schema() -> None:
    base = _seatable_base()
    checks = [
        ("Recipe List (Full)",          ["Link from Recipe Ingredients (Final)"]),
        ("Recipe Ingredients (Final)",  ["Ingredients used", "Recipe Name"]),
        ("Ingredients",                 ["Link to Supplier Product"]),
        ("Supplier Products",           ["Ingredients"]),
    ]
    for table, fields in checks:
        rows = base.list_rows(table, limit=1)
        print(f"\n=== {table} (row 0) ===")
        if not rows:
            print("  (empty table)")
            continue
        row = rows[0]
        for f in fields:
            print(f"\n  field: {f!r}")
            print("  raw:", json.dumps(row.get(f), indent=2, default=str))


def run_current() -> None:
    base = _seatable_base()
    recipes = _load_recipes(base)

    retail_count = sum(1 for r in recipes if r.get("Type") == "Retail")
    if retail_count:
        print(f"[LOG] Skipped {retail_count} Retail recipes")

    active = [
        r for r in recipes
        if r.get("Status") == "Active"
        and (r.get("Type") or "") not in ("WIP", "Retail")
        and r.get("Menu Pricing") is not None
        and float(r["Menu Pricing"]) > 0
    ]

    flagged = []
    for r in active:
        name = r.get("Recipe Name (Core)") or r.get("Recipe Name") or r["_id"]
        cost_pct = r.get("Cost %")
        if cost_pct is None:
            print(f"[LOG] WARNING: Cost % is None for active recipe {name!r}, skipping")
            continue
        if cost_pct > 0.30:
            flagged.append((name, cost_pct, r.get("Menu Pricing"), r.get("Recipe Cost")))

    if not flagged:
        print("[LOG] All active recipes at or below 30% food cost.")
        return

    flagged.sort(key=lambda x: x[1], reverse=True)

    tier_data_error = [(n, p, m, c) for n, p, m, c in flagged if p > 1.00]
    tier_review     = [(n, p, m, c) for n, p, m, c in flagged if 0.50 < p <= 1.00]
    tier_alert      = [(n, p, m, c) for n, p, m, c in flagged if 0.30 < p <= 0.50]

    header = "⚠️ *Food cost report*\n"
    body_lines = []
    for tier_label, tier_rows in [
        (f"🔴 *Likely data error ({len(tier_data_error)} recipes — Cost > 100%):*", tier_data_error),
        (f"🟡 *Needs review ({len(tier_review)} recipes — 50-100%):*",              tier_review),
        (f"🟠 *Margin alert ({len(tier_alert)} recipes — 30-50%):*",                tier_alert),
    ]:
        if not tier_rows:
            continue
        body_lines.append("")
        body_lines.append(tier_label)
        for name, cost_pct, menu_pricing, recipe_cost in tier_rows:
            recipe_cost_f = float(recipe_cost) if recipe_cost is not None else 0.0
            body_lines.append(f"  • {name}: {cost_pct * 100:.2f}%  (Menu RM{menu_pricing} / Cost RM{recipe_cost_f:.2f})")

    chunks = _chunk_message(header, body_lines)
    print("\n".join(chunks))

    from notifier import notify_cost_alert
    for chunk in chunks:
        notify_cost_alert(chunk)


def run_simulate(ingredient_query: str, new_price_str: str, unit: str) -> None:
    base = _seatable_base()
    ingredients = _load_ingredients(base)

    names = [r.get("Ingredient Name", "") for r in ingredients]
    norm_names = [_norm(n) for n in names]
    results = process.extract(_norm(ingredient_query), norm_names, scorer=fuzz.token_sort_ratio, limit=3)

    print("Top matches:")
    for match_name, score, idx in results:
        print(f"  {names[idx]} ({score:.0f}%)")

    if not results or results[0][1] < 95:
        top_score = results[0][1] if results else 0
        print(f"\nNo exact match (top score {top_score:.0f}%). Re-run with one of the above names.")
        sys.exit(0)

    _, _, best_idx = results[0]
    ingredient = ingredients[best_idx]
    ingredient_name = names[best_idx]

    # Multi-supplier check
    sp_ids = _get_linked_ids(ingredient.get("Link to Supplier Product"))
    supplier_products = _load_supplier_products(base)
    sp_by_id = {sp["_id"]: sp for sp in supplier_products}
    linked_sps = [sp_by_id[sid] for sid in sp_ids if sid in sp_by_id]
    active_count = sum(
        1 for sp in linked_sps
        if (sp.get("Active Status") or "").strip().lower() == "active"
    )
    multi_supplier_warning = ""
    if active_count > 1:
        multi_supplier_warning = (
            f"⚠️ This ingredient has {active_count} active suppliers. "
            "Seatable Lowest Gross Cost picks the minimum. "
            "Simulation is APPROXIMATE — only accurate if the changed supplier currently has the lowest price."
        )
        print(multi_supplier_warning)

    # Delta on base unit
    current_net_cost = Decimal(str(ingredient.get("Net Cost per Base Unit") or 0))
    info = get_base_unit_info(unit)
    if info is None:
        print(f"Unknown unit '{unit}'. Exit.")
        sys.exit(1)
    divisor, _ = info
    new_cost_per_base_unit = Decimal(str(new_price_str)) / divisor
    delta = new_cost_per_base_unit - current_net_cost

    # Affected RI rows via Ingredients backlink
    ri_ids = _get_linked_ids(ingredient.get("Recipe Ingredients (Final)"))
    ri_rows = _load_recipe_ingredients(base)
    ri_by_id = {r["_id"]: r for r in ri_rows}

    recipe_rows = _load_recipes(base)
    recipe_by_id = {r["_id"]: r for r in recipe_rows}

    impacts = []
    for ri_id in ri_ids:
        ri = ri_by_id.get(ri_id)
        if ri is None:
            continue
        # Skip WIP rows (Ingredients used is empty)
        if not _get_linked_ids(ri.get("Ingredients used")):
            continue
        qty = ri.get("Quantity")
        if not qty:
            continue
        delta_recipe = delta * Decimal(str(qty))

        recipe_link_ids = _get_linked_ids(ri.get("Recipe Name"))
        if not recipe_link_ids:
            continue
        recipe = recipe_by_id.get(recipe_link_ids[0])
        if recipe is None:
            continue
        if recipe.get("Status") != "Active":
            continue
        if (recipe.get("Type") or "") == "WIP":
            continue
        menu_pricing = recipe.get("Menu Pricing")
        if not menu_pricing or float(menu_pricing) <= 0:
            continue
        current_recipe_cost = recipe.get("Recipe Cost")
        if current_recipe_cost is None:
            continue

        old_pct = float(current_recipe_cost) / float(menu_pricing)
        new_pct = (float(current_recipe_cost) + float(delta_recipe)) / float(menu_pricing)
        recipe_name = recipe.get("Recipe Name (Core)") or recipe.get("Recipe Name") or recipe["_id"]
        impacts.append((recipe_name, old_pct, new_pct))

    impacts.sort(key=lambda x: x[2], reverse=True)

    lines = ["📊 *Cost impact simulation*"]
    if multi_supplier_warning:
        lines.append(multi_supplier_warning)
    lines.append("")
    lines.append(f"Ingredient: {ingredient_name}")
    lines.append(
        f"Price/base unit: RM{float(current_net_cost):.4f} → RM{float(new_cost_per_base_unit):.4f} "
        f"(Δ {float(delta):+.4f})"
    )
    lines.append(f"\nAffected recipes ({len(impacts)}):")
    for recipe_name, old_pct, new_pct in impacts:
        arrow = "▲" if new_pct > old_pct else ("▼" if new_pct < old_pct else "")
        flag = " ⚠️" if new_pct > 0.30 else ""
        lines.append(f"  • {recipe_name}: {old_pct * 100:.2f}% → {new_pct * 100:.2f}% {arrow}{flag}")

    print("\n".join(lines))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Recipe food-cost impact tool")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--debug-schema", action="store_true")
    group.add_argument("--current", action="store_true")
    group.add_argument("--simulate", nargs=3, metavar=("NAME", "PRICE", "UNIT"))

    args = parser.parse_args()

    if args.debug_schema:
        _debug_schema()
    elif args.current:
        run_current()
    elif args.simulate:
        run_simulate(*args.simulate)


if __name__ == "__main__":
    main()
