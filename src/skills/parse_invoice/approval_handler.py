"""
Telegram approval handler — long-polling daemon for button callbacks.

Reads pending payloads from data/pending_approvals/{invoice_number}.json,
processes /yes /no /skip actions, writes to Seatable, and edits the
original message to reflect the action.

Callback data format: act:invoice_num:idx
  act  = yes | no | skip
  idx  = 1, 2, 3, ... or "all"

Run this as a separate process (one terminal):
    python approval_handler.py
"""

import json
import os
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

load_dotenv()

# Use the same bot that sends notifications so it can edit its own messages
SEATABLE_UPDATE_BOT_TOKEN = os.getenv("SEATABLE_UPDATE_BOT_TOKEN")

PENDING_DIR = Path(__file__).parent.parent.parent.parent / "data" / "pending_approvals"
PENDING_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# Pending file management
# ============================================================

def _pending_path(invoice_number: str) -> Path:
    safe = invoice_number.replace("/", "_").replace(" ", "_")
    return PENDING_DIR / f"{safe}.json"


def save_pending(invoice_number: str, data: Dict[str, Any]) -> None:
    """Called by notifier when sending a message — preserves payload for callbacks."""
    path = _pending_path(invoice_number)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_pending(invoice_number: str) -> Optional[Dict[str, Any]]:
    path = _pending_path(invoice_number)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def update_pending(invoice_number: str, data: Dict[str, Any]) -> None:
    save_pending(invoice_number, data)


def get_actionable_items(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Numbered list combining price_changes + confirm_items (in same order as message)."""
    items = []
    for item in payload.get("price_changes", []):
        items.append({**item, "_category": "price_changes"})
    for item in payload.get("confirm_items", []):
        items.append({**item, "_category": "confirm_items"})
    return items


# ============================================================
# Inline keyboard builder (used by notifier)
# ============================================================

def build_inline_keyboard(payload: Dict[str, Any]) -> Optional[InlineKeyboardMarkup]:
    """
    Build per-item buttons + bulk-approve buttons.
    Returns None if there's nothing to approve.
    """
    items = get_actionable_items(payload)
    if not items:
        return None

    invoice_num = payload["invoice_number"]
    keyboard = []

    for i, _ in enumerate(items, 1):
        row = [
            InlineKeyboardButton(f"✓ {i}", callback_data=f"yes:{invoice_num}:{i}"),
            InlineKeyboardButton(f"✗ {i}", callback_data=f"no:{invoice_num}:{i}"),
            InlineKeyboardButton(f"⏭ {i}", callback_data=f"skip:{invoice_num}:{i}"),
        ]
        keyboard.append(row)

    # Bulk row
    keyboard.append([
        InlineKeyboardButton("✓ Approve All", callback_data=f"yes:{invoice_num}:all"),
        InlineKeyboardButton("⏭ Skip Invoice", callback_data=f"skip:{invoice_num}:all"),
    ])

    return InlineKeyboardMarkup(keyboard)


# ============================================================
# Callback handler
# ============================================================

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass  # query expired (>30s); continue processing anyway

    user = query.from_user
    user_ref = f"{user.username or user.first_name}({user.id})"

    try:
        parts = query.data.split(":", 2)
        action = parts[0]
        invoice_num = parts[1] if len(parts) > 1 else None
    except (ValueError, IndexError):
        await query.answer("Bad callback data", show_alert=True)
        return

    # Delegate setup callbacks to setup_handler
    SETUP_ACTIONS = {
        "add_supplier", "skip_supplier",
        "add_product", "skip_product",
        "link_ingredient", "skip_ingredient",
        "link_existing_product", "create_ingredient",
        "add_and_link",
    }
    if action in SETUP_ACTIONS:
        from setup_handler import handle_setup_callback
        await handle_setup_callback(update, context)
        return

    # Backfill pack info callbacks
    if action in ("backfill_pack", "backfill_skip"):
        sp_row_id = invoice_num  # second field reused as sp_row_id
        backfill_dir = PENDING_DIR.parent / "backfill_pending"
        pending_path = backfill_dir / f"{sp_row_id}.json"

        if action == "backfill_skip":
            if pending_path.exists():
                pending_path.unlink()
            try:
                await query.edit_message_text(query.message.text + f"\n\n⏭ {user_ref}: skipped")
            except Exception:
                pass
            return

        # action == "backfill_pack"
        if not pending_path.exists():
            await query.edit_message_text(query.message.text + "\n\n❌ Backfill data missing.")
            return

        import json as _json
        with open(pending_path, encoding="utf-8") as f:
            bp = _json.load(f)

        update_payload = {}
        if bp.get("pack_size"):
            update_payload["Pack Size"] = bp["pack_size"]
        if bp.get("unit_quantity") is not None:
            update_payload["Unit Quantity"] = bp["unit_quantity"]
        if bp.get("unit_of_measure"):
            update_payload["Unit of Measure"] = bp["unit_of_measure"]

        try:
            from seatable_writer import _base as _sw_base
            base = _sw_base()
            base.update_row("Supplier Products", sp_row_id, update_payload)
            pending_path.unlink()
            result_text = ", ".join(f"{k}={v}" for k, v in update_payload.items())
            await query.edit_message_text(
                query.message.text + f"\n\n✓ {user_ref}: applied — {result_text}"
            )
        except Exception as e:
            await query.edit_message_text(query.message.text + f"\n\n❌ Failed: {e}")
        return

    # Parse approval callback (action, invoice_num, target)
    try:
        _, _, target = query.data.split(":", 2)
    except ValueError:
        await query.answer("Bad callback data", show_alert=True)
        return

    payload = load_pending(invoice_num)
    if not payload:
        await query.edit_message_text(
            query.message.text + f"\n\n❌ Pending payload missing for {invoice_num}."
        )
        return

    items = get_actionable_items(payload)
    statuses = payload.setdefault("_item_status", {str(i): "pending" for i in range(1, len(items) + 1)})

    # Pick target items
    if target == "all":
        targets = [str(i) for i in range(1, len(items) + 1)]
    else:
        targets = [target]

    # For yes actions: auth once + upsert invoice row once, reuse across all items
    _shared_base = None
    _shared_invoice_row_id = None
    if action == "yes":
        from seatable_writer import _base as _sw_base, upsert_invoice_row, attach_invoice_file
        try:
            _shared_base = _sw_base()
            _shared_invoice_row_id = upsert_invoice_row(
                _shared_base,
                invoice_num,
                payload.get("supplier_name", ""),
                payload.get("supplier_row_id", ""),
                payload.get("invoice_date", ""),
            )
            if _shared_invoice_row_id and payload.get("invoice_file_path"):
                attach_invoice_file(_shared_base, _shared_invoice_row_id, payload["invoice_file_path"])
        except Exception as e:
            print(f"[WARNING] Pre-auth failed, will retry per-item: {e}")
            _shared_base = None
            _shared_invoice_row_id = None

    # Process each target
    results = []
    for idx_str in targets:
        if statuses.get(idx_str) != "pending":
            results.append(f"{idx_str}: already {statuses[idx_str]}")
            continue

        try:
            idx = int(idx_str)
            item = items[idx - 1]
        except (ValueError, IndexError):
            results.append(f"{idx_str}: invalid index")
            continue

        if action == "yes":
            from seatable_writer import commit_price_change

            flagged_by = (
                f"Auto:{user_ref}" if item.get("_category") == "price_changes"
                else f"Manual:{user_ref}"
            )

            result = commit_price_change(
                item,
                payload,
                flagged_by=flagged_by,
                base=_shared_base,
                invoice_row_id=_shared_invoice_row_id,
            )
            if result["status"] == "ok":
                statuses[idx_str] = "approved"
                results.append(f"{idx_str}: ✓ {item['sp_code']} → RM{item['new_price']:.2f}")
            elif result["status"] == "partial":
                statuses[idx_str] = "partial"
                results.append(f"{idx_str}: ⚠️ partial — {result['message']}")
            else:
                statuses[idx_str] = "error"
                results.append(f"{idx_str}: ❌ {result.get('step')}: {result.get('message')}")

        elif action == "no":
            statuses[idx_str] = "rejected"
            results.append(f"{idx_str}: ✗ rejected")

        elif action == "skip":
            statuses[idx_str] = "skipped"
            results.append(f"{idx_str}: ⏭ skipped")

    update_pending(invoice_num, payload)

    # Edit message with action summary appended
    summary = "\n".join(results)
    new_text = f"{query.message.text}\n\n— {user_ref} —\n{summary}"

    # If everything resolved, mark invoice processed in Seatable
    all_done = all(v != "pending" for v in statuses.values())
    if all_done:
        try:
            from seatable_writer import mark_invoice_processed, _base as _sw_base, upsert_invoice_row
            b = _shared_base or _sw_base()
            inv_id = _shared_invoice_row_id
            if not inv_id:
                inv_id = upsert_invoice_row(b, invoice_num, payload.get("supplier_name", ""),
                                            payload.get("supplier_row_id", ""), payload.get("invoice_date", ""))
            if inv_id:
                mark_invoice_processed(b, inv_id)
                new_text += "\n✅ Invoice marked Processed."
        except Exception as e:
            new_text += f"\n⚠️ Couldn't mark invoice Processed: {e}"

        # Remove the keyboard since nothing else to do
        try:
            await query.edit_message_text(new_text, reply_markup=None)
        except Exception:
            await query.edit_message_text(new_text)
    else:
        # Keep keyboard for remaining items
        try:
            await query.edit_message_text(new_text, reply_markup=query.message.reply_markup)
        except Exception:
            await query.edit_message_text(new_text)


# ============================================================
# /history command
# ============================================================

async def handle_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /history <product name>  — show price trend for a Supplier Product.
    Uses base.query() (1 call per table) to stay within Seatable API limits.
    """
    term = " ".join(context.args).strip() if context.args else ""
    if not term:
        await update.message.reply_text("Usage: /history <product name or SP code>")
        return

    safe_term = term.replace("'", "''")  # basic SQL escape

    from seatable_writer import _base as _sw_base
    try:
        base = _sw_base()
    except Exception as e:
        await update.message.reply_text(f"❌ Seatable auth failed: {e}")
        return

    # Find matching SP
    try:
        sp_rows = base.query(
            f"SELECT `_id`, `Supplier Product Name`, `Price per Pack` "
            f"FROM `Supplier Products` WHERE `Supplier Product Name` LIKE '%{safe_term}%' LIMIT 5"
        )
    except Exception:
        sp_rows = []

    if not sp_rows:
        await update.message.reply_text(f"No product found matching '{term}'")
        return

    sp = sp_rows[0]
    sp_name = sp.get("Supplier Product Name") or "?"
    sp_id = sp.get("_id") or ""
    current_price = sp.get("Price per Pack")

    # Get recent price history
    try:
        history_rows = base.query(
            "SELECT `_ctime`, `Old Price`, `New Price`, `Change %`, `Flagged By`, "
            "`Supplier product (link)` FROM `Price History` ORDER BY `_ctime` DESC LIMIT 200"
        )
    except Exception:
        history_rows = []

    # Filter by SP row_id via link column
    sp_history = []
    for row in history_rows:
        links = row.get("Supplier product (link)") or []
        for link in links:
            link_id = link.get("row_id") if isinstance(link, dict) else str(link)
            if link_id == sp_id:
                sp_history.append(row)
                break

    price_str = f"RM{float(current_price):.2f}" if current_price is not None else "not set"
    lines = [f"📈 *{sp_name}*", f"Current: {price_str}\n"]

    if not sp_history:
        lines.append("_(no price history yet)_")
    else:
        for row in sp_history[:10]:
            date_str = (row.get("_ctime") or "?")[:10]
            old_p = row.get("Old Price") or 0
            new_p = row.get("New Price") or 0
            chg = row.get("Change %") or 0
            by = (row.get("Flagged By") or "?").split(":")[0]  # "Auto" or "Manual"
            arrow = "↑" if new_p > old_p else ("↓" if new_p < old_p else "→")
            lines.append(f"`{date_str}` RM{old_p:.2f}→RM{new_p:.2f} {arrow}{abs(chg):.0f}% [{by}]")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ============================================================
# Main
# ============================================================

def main():
    if not SEATABLE_UPDATE_BOT_TOKEN:
        print("[ERROR] SEATABLE_UPDATE_BOT_TOKEN missing from .env")
        sys.exit(1)

    app = Application.builder().token(SEATABLE_UPDATE_BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(CommandHandler("history", handle_history))

    print(f"[approval_handler] Listening on bot token ending …{SEATABLE_UPDATE_BOT_TOKEN[-6:]}")
    print(f"[approval_handler] Pending dir: {PENDING_DIR}")
    app.run_polling()


if __name__ == "__main__":
    main()
