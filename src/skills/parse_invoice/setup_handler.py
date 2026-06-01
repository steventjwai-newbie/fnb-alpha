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


def _search_ingredients(base, query: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Fuzzy search Ingredients by Name, return top candidates for LLM context."""
    from rapidfuzz import fuzz

    candidates = []
    try:
        for row in base.list_rows("Ingredients"):
            name = row.get("Ingredient Name", "")
            if name:
                score = fuzz.token_set_ratio(query.lower(), name.lower())
                candidates.append({"name": name, "row_id": row["_id"], "score": score})
    except Exception as e:
        print(f"[ERROR] Ingredient search failed: {e}")

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:limit]


def _llm_classify_sp(
    sp_name: str,
    candidates: List[Dict[str, Any]],
    invoice_unit: str = None,
    invoice_unit_price=None,
) -> Dict[str, Any]:
    """Single Gemini call: classify a supplier product.

    Returns ingredient match + pack metadata:
    {
      "match_row_id": str|None, "match_name": str|None, "suggested_name": str,
      "whole_piece": bool,
      "pack_size": str|None, "unit_quantity": float|None, "unit_of_measure": str|None,
      "pack_reasoning": str
    }
    """
    import os, json
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    candidate_lines = "\n".join(
        f"  id={c['row_id']} | {c['name']}" for c in candidates
    ) if candidates else "  (none)"

    invoice_ctx = ""
    if invoice_unit or invoice_unit_price is not None:
        parts = []
        if invoice_unit:
            parts.append(f"unit={invoice_unit}")
        if invoice_unit_price is not None:
            parts.append(f"unit_price={invoice_unit_price}")
        invoice_ctx = f"\nInvoice context: {', '.join(parts)}"

    prompt = (
        f"Malaysian cafe supplier product classifier.\n\n"
        f"Supplier product: {sp_name}{invoice_ctx}\n\n"
        f"Ingredient table candidates:\n{candidate_lines}\n\n"
        f"Tasks:\n"
        f"1. Match ingredient: pick best candidate if it fits well, else return null for match fields. "
        f"Always return a short canonical ingredient name (e.g. 'Smoked Salmon').\n"
        f"2. Pack info: extract pack_size (free-text like '10KG/CTN', '12X1L'), "
        f"unit_quantity (the numeric selling quantity), unit_of_measure (g/kg/ml/l/lb/oz/gal/floz — lowercase). "
        f"Use invoice context to resolve carton-vs-unit ambiguity. "
        f"Imperial units (lb=453.592g, oz=28.3495g, gal=3785.41ml, floz=29.5735ml) are valid.\n"
        f"3. whole_piece: true ONLY if this item is always sold/served as one indivisible unit "
        f"(e.g. a burrata ball, an individual cake, a whole fish, a single-serve portion pack). "
        f"Bulk commodities like salmon fillets, cream, flour, butter blocks are NOT whole_piece. "
        f"When whole_piece=true: set unit_quantity=1 and unit_of_measure to the container word (tub/pcs/btl).\n\n"
        f"Return ONLY valid JSON:\n"
        f'{{"match_row_id": "string or null", "match_name": "string or null", '
        f'"suggested_name": "string", "whole_piece": false, '
        f'"pack_size": "string or null", "unit_quantity": null, '
        f'"unit_of_measure": "string or null", "pack_reasoning": "string"}}'
    )

    import time
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model=model,
                contents=[prompt],
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
            result = json.loads(response.text.strip())
            if not result.get("suggested_name"):
                result["suggested_name"] = sp_name
            if result.get("whole_piece") and not result.get("unit_quantity"):
                result["unit_quantity"] = 1
            return result
        except Exception as e:
            print(f"[ERROR] LLM SP classify failed (attempt {attempt + 1}/3): {e}")
            if attempt < 2:
                time.sleep(10)
    return {
        "match_row_id": None, "match_name": None, "suggested_name": sp_name,
        "whole_piece": False, "pack_size": None, "unit_quantity": None,
        "unit_of_measure": None, "pack_reasoning": "LLM failed",
    }


def _search_sps_by_name(base, query: str, supplier_row_id: str = None) -> List[Dict[str, Any]]:
    """Fuzzy search Supplier Products by name at >=80 score.
    When supplier_row_id is given, only considers SPs linked to that supplier.
    """
    from rapidfuzz import fuzz

    candidates = []
    start, page = 0, 1000
    try:
        while True:
            batch = base.list_rows("Supplier Products", start=start, limit=page)
            if not batch:
                break
            for row in batch:
                if supplier_row_id:
                    links = row.get("Supplier") or []
                    link_ids = [l.get("row_id") if isinstance(l, dict) else l for l in links]
                    if supplier_row_id not in link_ids:
                        continue
                name = row.get("Supplier Product Name") or ""
                if name:
                    score = fuzz.token_set_ratio(query.lower(), name.lower())
                    if score >= 80:
                        candidates.append({"name": name, "row_id": row["_id"], "score": score})
            if len(batch) < page:
                break
            start += page
    except Exception as e:
        print(f"[ERROR] SP search failed: {e}")

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:2]


async def _send_product_prompt(query, state: Dict[str, Any], invoice_num: str, prefix_text: str,
                                base=None) -> None:
    """Edit message to: Create product 'X'? [pack info] [Link Existing?] [Add Product] [Skip]"""
    item = state["items"][state["current_item_idx"]]
    product_name = item["product_name"]

    # LLM classification — cache on state item so add_product handler reuses it
    llm_result = item.get("llm_classification")
    if not llm_result and base:
        candidates = _search_ingredients(base, product_name)
        llm_result = _llm_classify_sp(
            product_name, candidates,
            invoice_unit=item.get("invoice_unit"),
            invoice_unit_price=item.get("invoice_unit_price"),
        )
        item["llm_classification"] = llm_result
        save_setup_state(invoice_num, state)

    # Build pack preview lines
    pack_lines = []
    if llm_result:
        if llm_result.get("pack_size"):
            pack_lines.append(f"  Pack: {llm_result['pack_size']}")
        if llm_result.get("unit_quantity") is not None and llm_result.get("unit_of_measure"):
            pack_lines.append(f"  Unit: {llm_result['unit_quantity']} {llm_result['unit_of_measure']}")

    pack_str = ("\n" + "\n".join(pack_lines)) if pack_lines else ""
    text = (
        f"{prefix_text}\n"
        f"Item {state['current_item_idx'] + 1}/{len(state['items'])}: {product_name}\n"
        f"Supplier: {state['supplier_name']}{pack_str}\n"
        f"Create this product?"
    )

    keyboard = []
    if base:
        supplier_row_id = state.get("supplier_row_id")
        existing = _search_sps_by_name(base, product_name, supplier_row_id=supplier_row_id)
        for sp in existing:
            label = f"Link: {sp['name'][:35]} ({sp['score']}%)"
            keyboard.append([InlineKeyboardButton(
                label, callback_data=f"link_existing_product:{invoice_num}:{sp['row_id']}"
            )])

    keyboard.append([
        InlineKeyboardButton("Add Product", callback_data=f"add_product:{invoice_num}"),
        InlineKeyboardButton("Skip", callback_data=f"skip_product:{invoice_num}"),
    ])

    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        print(f"[ERROR] Failed to edit message: {e}")


async def _send_ingredient_prompt_llm(query, state: Dict[str, Any], invoice_num: str,
                                       product_name: str, llm_result: Dict, prefix_text: str) -> None:
    """Edit message with LLM-determined ingredient button(s) + [Skip]."""
    match_row_id = llm_result.get("match_row_id")
    match_name = llm_result.get("match_name")
    suggested_name = llm_result.get("suggested_name") or product_name

    keyboard = []
    if match_row_id and match_name:
        keyboard.append([InlineKeyboardButton(
            f"Link: {match_name}",
            callback_data=f"link_ingredient:{invoice_num}:{match_row_id}"
        )])
    cb_name = suggested_name[:40]  # Telegram callback_data hard limit is 64 bytes
    keyboard.append([InlineKeyboardButton(
        f"Create: {cb_name}",
        callback_data=f"create_ingredient:{invoice_num}:{cb_name}"
    )])
    keyboard.append([InlineKeyboardButton("Skip", callback_data=f"skip_ingredient:{invoice_num}")])

    text = (
        f"{prefix_text}\n"
        f"Item {state['current_item_idx'] + 1}/{len(state['items'])}: {product_name}\n"
        f"Link to which ingredient?"
    )

    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        print(f"[ERROR] Failed to edit message: {e}")


def _build_approval_payload(state: Dict[str, Any], original_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Build payload merging original matched/changed items with newly-created products."""
    payload = dict(original_payload)
    payload["supplier_matched"] = True
    payload["supplier_row_id"] = state["supplier_row_id"]

    new_confirm = []
    for item in state["items"]:
        if item.get("status") == "skipped":
            continue
        if not item.get("product_row_id"):
            continue
        new_confirm.append({
            "product_name": item["product_name"],
            "seatable_product": item["product_name"],
            "sp_row_id": item["product_row_id"],
            "sp_code": f"SP-{item['product_row_id'][:8]}",
            "matched_product_name": item["product_name"],
            "match_score": 100,
            "old_price": 0,
            "new_price": item.get("invoice_unit_price") or 0,
            "unit": item.get("invoice_unit") or "",
            "invoice_unit": item.get("invoice_unit"),
            "invoice_unit_price": item.get("invoice_unit_price"),
            "diff_pct": "N/A",
        })

    payload["price_changes"] = list(original_payload.get("price_changes", []))
    payload["confirm_items"] = list(original_payload.get("confirm_items", [])) + new_confirm
    payload["unmatched_items"] = []

    return payload


async def _advance_to_next_item_or_finish(query, state: Dict[str, Any], invoice_num: str,
                                         base, new_text: str) -> None:
    """Check if more items remain; if yes, show product prompt; if no, send approval buttons."""
    state["current_item_idx"] += 1

    if state["current_item_idx"] < len(state["items"]):
        save_setup_state(invoice_num, state)
        await _send_product_prompt(query, state, invoice_num, new_text, base=base)
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
            await query.edit_message_text(new_text + "\n\n[OK] Setup complete — sending approval buttons...")
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
        if state.get("supplier_added") and state.get("supplier_row_id"):
            new_text = f"[INFO] Supplier '{supplier_name}' already created."
            if state["items"]:
                await _send_product_prompt(query, state, invoice_num, new_text, base=base)
            return
        try:
            row_data = {"Supplier Name": supplier_name}
            row = base.append_row("Suppliers", row_data)
            supplier_row_id = row["_id"]

            state["supplier_row_id"] = supplier_row_id
            state["supplier_added"] = True
            state["setup_step"] = "product"
            save_setup_state(invoice_num, state)

            from step2_compare import clear_caches
            clear_caches()

            new_text = f"[OK] Supplier '{supplier_name}' created."
            if state["items"]:
                await _send_product_prompt(query, state, invoice_num, new_text, base=base)
            else:
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

        # Use cached LLM classification (set by _send_product_prompt) or call now
        llm_result = item.get("llm_classification")
        if not llm_result:
            candidates = _search_ingredients(base, product_name)
            llm_result = _llm_classify_sp(
                product_name, candidates,
                invoice_unit=item.get("invoice_unit"),
                invoice_unit_price=item.get("invoice_unit_price"),
            )

        if item.get("product_added") and item.get("product_row_id"):
            new_text = f"[INFO] Product '{product_name}' already created."
            await _send_ingredient_prompt_llm(query, state, invoice_num, product_name, llm_result, new_text)
            return

        try:
            row_data = {
                "Supplier Product Name": product_name,
                "Supplier": [supplier_row_id],
                "Active Status": "Active",
            }
            if llm_result.get("pack_size"):
                row_data["Pack Size"] = llm_result["pack_size"]
            if llm_result.get("unit_quantity") is not None:
                row_data["Unit Quantity"] = llm_result["unit_quantity"]
            if llm_result.get("unit_of_measure"):
                uom = llm_result["unit_of_measure"]
                row_data["Unit of Measure"] = uom.upper()
                from unit_normalizer import to_base_qty
                base_qty = to_base_qty(llm_result["unit_quantity"], uom)
                if base_qty is not None:
                    row_data["Base Qty"] = base_qty

            row = base.append_row("Supplier Products", row_data)
            product_row_id = row["_id"]

            from seatable_writer import add_row_link
            add_row_link(
                base=base,
                link_column_table="Supplier Products",
                link_column_name="Supplier",
                link_column_row_id=product_row_id,
                target_table="Suppliers",
                target_row_id=supplier_row_id,
            )

            item["product_row_id"] = product_row_id
            item["product_added"] = True
            save_setup_state(invoice_num, state)

            from step2_compare import clear_caches
            clear_caches()

            pack_parts = []
            if llm_result.get("pack_size"):
                pack_parts.append(f"Pack: {llm_result['pack_size']}")
            if llm_result.get("unit_quantity") is not None and llm_result.get("unit_of_measure"):
                pack_parts.append(f"Unit: {llm_result['unit_quantity']} {llm_result['unit_of_measure']}")
            pack_suffix = f" ({', '.join(pack_parts)})" if pack_parts else ""
            new_text = f"[OK] Product '{product_name}' created.{pack_suffix}"

            await _send_ingredient_prompt_llm(query, state, invoice_num, product_name, llm_result, new_text)
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

    elif action == "link_existing_product":
        if not extra:
            await query.answer("Invalid product", show_alert=True)
            return

        sp_row_id = extra
        item = state["items"][state["current_item_idx"]]
        supplier_row_id = state["supplier_row_id"]
        product_name = item["product_name"]

        try:
            from seatable_writer import add_row_link
            add_row_link(
                base=base,
                link_column_table="Supplier Products",
                link_column_name="Supplier",
                link_column_row_id=sp_row_id,
                target_table="Suppliers",
                target_row_id=supplier_row_id,
            )

            item["product_row_id"] = sp_row_id
            item["product_added"] = True
            save_setup_state(invoice_num, state)

            from step2_compare import clear_caches
            clear_caches()

            new_text = f"[OK] Linked existing product '{product_name}'."
            llm_result = item.get("llm_classification")
            if not llm_result:
                candidates = _search_ingredients(base, product_name)
                llm_result = _llm_classify_sp(
                    product_name, candidates,
                    invoice_unit=item.get("invoice_unit"),
                    invoice_unit_price=item.get("invoice_unit_price"),
                )
            await _send_ingredient_prompt_llm(query, state, invoice_num, product_name, llm_result, new_text)
        except Exception as e:
            try:
                await query.edit_message_text(f"[ERROR] Failed to link existing product: {e}")
            except:
                pass

    elif action == "create_ingredient":
        if not extra:
            await query.answer("Invalid ingredient name", show_alert=True)
            return

        ingredient_name = extra
        item = state["items"][state["current_item_idx"]]
        product_row_id = item.get("product_row_id")

        try:
            import time as _time
            new_ing = base.append_row("Ingredients", {"Ingredient Name": ingredient_name})
            if not new_ing or not isinstance(new_ing, dict) or not new_ing.get("_id"):
                # Fallback: search for the inserted row
                _time.sleep(1)
                for row in base.list_rows("Ingredients"):
                    if (row.get("Ingredient Name") or "").strip() == ingredient_name.strip():
                        new_ing = row
                        break
            if not new_ing or not new_ing.get("_id"):
                raise ValueError(f"Could not create or locate Ingredient '{ingredient_name}'")
            ingredient_row_id = new_ing["_id"]

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

            new_text = f"[OK] Ingredient '{ingredient_name}' created and linked."
            await _advance_to_next_item_or_finish(query, state, invoice_num, base, new_text)
        except Exception as e:
            try:
                await query.edit_message_text(f"[ERROR] Failed to create ingredient: {e}")
            except:
                pass

    elif action == "skip_ingredient":
        item = state["items"][state["current_item_idx"]]
        item["ingredient_linked"] = False
        save_setup_state(invoice_num, state)

        new_text = f"[SKIP] Ingredient linking skipped."
        await _advance_to_next_item_or_finish(query, state, invoice_num, base, new_text)
