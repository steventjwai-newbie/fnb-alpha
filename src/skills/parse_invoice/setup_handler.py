"""
Setup handler for missing suppliers and products.
Manages multi-step Telegram workflow: supplier → product → ingredient → approval.
"""
import json
from pathlib import Path
from typing import Dict, Any, Optional, List
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

load_dotenv()

SETUP_STATE_DIR = Path(__file__).parent.parent.parent.parent / "data" / "setup_state"
SETUP_STATE_DIR.mkdir(parents=True, exist_ok=True)


def _base():
    """Get authenticated Seatable base."""
    import os
    from seatable_api import Base
    token = os.getenv("SEATABLE_API_TOKEN")
    url = os.getenv("SEATABLE_BASE_URL")
    base = Base(token, url)
    base.auth()
    return base


def save_setup_state(invoice_num: str, state: Dict[str, Any]) -> None:
    """Save setup state between Telegram exchanges."""
    safe = invoice_num.replace("/", "_").replace(" ", "_")
    path = SETUP_STATE_DIR / f"{safe}_setup.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def load_setup_state(invoice_num: str) -> Optional[Dict[str, Any]]:
    """Load setup state."""
    safe = invoice_num.replace("/", "_").replace(" ", "_")
    path = SETUP_STATE_DIR / f"{safe}_setup.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def delete_setup_state(invoice_num: str) -> None:
    """Clean up setup state."""
    safe = invoice_num.replace("/", "_").replace(" ", "_")
    path = SETUP_STATE_DIR / f"{safe}_setup.json"
    if path.exists():
        path.unlink()


def _search_ingredients(base, query: str) -> List[Dict[str, Any]]:
    """Fuzzy search Ingredients by Name, return top 3."""
    from rapidfuzz import fuzz

    candidates = []
    try:
        for row in base.list_rows("Ingredients"):
            name = row.get("Name", "")
            if name:
                score = fuzz.token_set_ratio(query.lower(), name.lower())
                candidates.append({"name": name, "row_id": row["_id"], "score": score})
    except Exception as e:
        print(f"[ERROR] Ingredient search failed: {e}")

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:3]


def _send_product_prompt(query, state: Dict[str, Any], invoice_num: str, prefix_text: str) -> None:
    """Edit message to: Create product 'X'? [Add Product] [Skip]"""
    item = state["items"][state["current_item_idx"]]
    product_name = item["product_name"]

    text = (
        f"{prefix_text}\n"
        f"Item {state['current_item_idx'] + 1}/{len(state['items'])}: {product_name}\n"
        f"Supplier: {state['supplier_name']}\n"
        f"Create this product?"
    )

    keyboard = [[
        InlineKeyboardButton("Add Product", callback_data=f"add_product:{invoice_num}"),
        InlineKeyboardButton("Skip", callback_data=f"skip_product:{invoice_num}"),
    ]]

    try:
        query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        print(f"[ERROR] Failed to edit message: {e}")


def _send_ingredient_prompt(query, state: Dict[str, Any], invoice_num: str,
                           product_name: str, candidates: List[Dict], prefix_text: str) -> None:
    """Edit message with up to 3 ingredient buttons + [Skip]."""
    keyboard = []
    for cand in candidates:
        score = cand.get("score", 0)
        btn = InlineKeyboardButton(
            f"{cand['name']} ({score}%)",
            callback_data=f"link_ingredient:{invoice_num}:{cand['row_id']}"
        )
        keyboard.append([btn])

    keyboard.append([InlineKeyboardButton("Skip", callback_data=f"skip_ingredient:{invoice_num}")])

    text = (
        f"{prefix_text}\n"
        f"Item {state['current_item_idx'] + 1}/{len(state['items'])}: {product_name}\n"
        f"Link to which ingredient?"
    )

    try:
        query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        print(f"[ERROR] Failed to edit message: {e}")


def _build_approval_payload(state: Dict[str, Any], original_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Build synthetic payload with new products in confirm_items, supplier_matched=True."""
    payload = original_payload.copy()
    payload["supplier_matched"] = True
    payload["supplier_row_id"] = state["supplier_row_id"]

    confirm_items = []
    for item in state["items"]:
        if item["status"] == "skipped":
            continue

        confirm_items.append({
            "product_name": item["product_name"],
            "sp_row_id": item["product_row_id"],
            "sp_code": f"SP-{item['product_row_id'][:8]}",
            "matched_product_name": item["product_name"],
            "old_price": 0,
            "new_price": item["invoice_unit_price"],
            "invoice_unit": item["invoice_unit"],
            "invoice_unit_price": item["invoice_unit_price"],
        })

    payload["confirm_items"] = confirm_items
    payload["price_changes"] = []
    payload["unmatched_items"] = []

    return payload


async def _advance_to_next_item_or_finish(query, state: Dict[str, Any], invoice_num: str,
                                         base, new_text: str) -> None:
    """Check if more items remain; if yes, show product prompt; if no, send approval buttons."""
    state["current_item_idx"] += 1

    if state["current_item_idx"] < len(state["items"]):
        save_setup_state(invoice_num, state)
        _send_product_prompt(query, state, invoice_num, new_text)
    else:
        from approval_handler import load_pending
        from notifier import notify_invoice_comparison

        original_payload = load_pending(invoice_num)
        if not original_payload:
            try:
                query.edit_message_text(new_text + "\n\n[ERROR] Original payload missing. Setup cancelled.")
            except:
                pass
            delete_setup_state(invoice_num)
            return

        state["setup_complete"] = True
        save_setup_state(invoice_num, state)

        approval_payload = _build_approval_payload(state, original_payload)

        notify_invoice_comparison(approval_payload, _skip_setup_check=True)

        try:
            query.edit_message_text(new_text + "\n\n[OK] Setup complete — sending approval buttons...")
        except:
            pass


async def handle_setup_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main handler for setup callbacks."""
    query = update.callback_query
    await query.answer()

    user = query.from_user
    user_ref = f"{user.username or user.first_name}({user.id})"

    try:
        parts = query.data.split(":", 2)
        action = parts[0]
        invoice_num = parts[1] if len(parts) > 1 else None
        extra = parts[2] if len(parts) > 2 else None
    except (ValueError, IndexError):
        await query.answer("Bad callback data", show_alert=True)
        return

    state = load_setup_state(invoice_num)
    if not state:
        await query.edit_message_text(f"[ERROR] Setup state missing for {invoice_num}")
        return

    base = _base()

    if action == "add_supplier":
        supplier_name = state["supplier_name"]
        try:
            row_data = {"Supplier Name": supplier_name}
            row = base.append_row("Suppliers", row_data)
            supplier_row_id = row["_id"]

            state["supplier_row_id"] = supplier_row_id
            state["supplier_added"] = True
            save_setup_state(invoice_num, state)

            new_text = f"[OK] Supplier '{supplier_name}' created."
            await _advance_to_next_item_or_finish(query, state, invoice_num, base, new_text)
        except Exception as e:
            try:
                await query.edit_message_text(f"[ERROR] Failed to create supplier: {e}")
            except:
                pass

    elif action == "skip_supplier":
        try:
            await query.edit_message_text(f"[SKIP] Invoice {invoice_num} left for manual review.")
        except:
            pass
        delete_setup_state(invoice_num)

    elif action == "add_product":
        item = state["items"][state["current_item_idx"]]
        product_name = item["product_name"]
        supplier_row_id = state["supplier_row_id"]

        try:
            row_data = {
                "Supplier Product Name": product_name,
                "Supplier": [supplier_row_id],
                "Active Status": "Active",
            }
            row = base.append_row("Supplier Products", row_data)
            product_row_id = row["_id"]

            item["product_row_id"] = product_row_id
            item["product_added"] = True
            save_setup_state(invoice_num, state)

            new_text = f"[OK] Product '{product_name}' created."
            candidates = _search_ingredients(base, product_name)

            if candidates:
                await _send_ingredient_prompt(query, state, invoice_num, product_name, candidates, new_text)
            else:
                item["ingredient_linked"] = False
                save_setup_state(invoice_num, state)
                await _advance_to_next_item_or_finish(query, state, invoice_num, base, new_text + " [No ingredients found, skipping.]")
        except Exception as e:
            try:
                await query.edit_message_text(f"[ERROR] Failed to create product: {e}")
            except:
                pass

    elif action == "skip_product":
        item = state["items"][state["current_item_idx"]]
        item["status"] = "skipped"
        save_setup_state(invoice_num, state)

        new_text = f"[SKIP] Product '{item['product_name']}' skipped."
        await _advance_to_next_item_or_finish(query, state, invoice_num, base, new_text)

    elif action == "link_ingredient":
        if not extra:
            await query.answer("Invalid ingredient", show_alert=True)
            return

        ingredient_row_id = extra
        item = state["items"][state["current_item_idx"]]
        product_row_id = item["product_row_id"]

        try:
            from seatable_writer import add_row_link
            add_row_link(
                base=base,
                link_column_table="Supplier Products",
                link_column_name="Ingredients",
                link_column_row_id=product_row_id,
                target_table="Ingredients",
                target_row_id=ingredient_row_id,
            )

            item["ingredient_row_id"] = ingredient_row_id
            item["ingredient_linked"] = True
            save_setup_state(invoice_num, state)

            new_text = f"[OK] Ingredient linked."
            await _advance_to_next_item_or_finish(query, state, invoice_num, base, new_text)
        except Exception as e:
            try:
                await query.edit_message_text(f"[ERROR] Failed to link ingredient: {e}")
            except:
                pass

    elif action == "skip_ingredient":
        item = state["items"][state["current_item_idx"]]
        item["ingredient_linked"] = False
        save_setup_state(invoice_num, state)

        new_text = f"[SKIP] Ingredient linking skipped."
        await _advance_to_next_item_or_finish(query, state, invoice_num, base, new_text)
