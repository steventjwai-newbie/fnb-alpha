import os
import requests
from dotenv import load_dotenv

load_dotenv()

_PARSE_TOKEN = os.getenv("INVOICE_PARSE_NOTIFICATION_TOKEN")
_SEATABLE_TOKEN = os.getenv("SEATABLE_UPDATE_BOT_TOKEN")
_PARSE_CHAT_ID = os.getenv("INVOICE_GROUP_CHAT_ID", "-5257569290")
_SEATABLE_CHAT_ID = os.getenv("SEATABLE_BOT_CHAT_ID", "-5150446443")


def _send_parse(text: str) -> bool:
    url = f"https://api.telegram.org/bot{_PARSE_TOKEN}/sendMessage"
    resp = requests.post(url, json={"chat_id": _PARSE_CHAT_ID, "text": text, "parse_mode": "Markdown"})
    if not resp.ok:
        print(f"[LOG] Parse bot error: {resp.status_code} {resp.text}")
    return resp.ok


def _send_seatable(text: str, reply_markup: dict = None) -> bool:
    url = f"https://api.telegram.org/bot{_SEATABLE_TOKEN}/sendMessage"
    payload = {"chat_id": _SEATABLE_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    resp = requests.post(url, json=payload)
    if not resp.ok:
        print(f"[LOG] Seatable bot error: {resp.status_code} {resp.text}")
    return resp.ok


def notify_handwriting_detected(invoice: dict, file_path: str) -> bool:
    invoice_num = invoice.get("invoice_number") or "Unknown"
    supplier = invoice.get("supplier_name") or "Unknown supplier"
    content = invoice.get("handwriting_content") or "unreadable"

    lines = [
        f"⚠️ *Handwriting detected*",
        f"Invoice: `{invoice_num}`",
        f"Supplier: {supplier}",
        f"File: `{file_path}`",
        f"Note: _{content}_",
    ]

    crossed = [item for item in invoice.get("line_items", []) if item.get("crossed_out")]
    if crossed:
        lines.append("\n*Crossed-out items:*")
        for item in crossed:
            name = item.get("product_name") or "(no name)"
            qty = item.get("quantity")
            unit = item.get("unit") or ""
            price = item.get("unit_price")
            qty_str = f" · {qty} {unit}".rstrip() if qty else ""
            price_str = f" · RM{price}" if price else ""
            lines.append(f"  ✗ {name}{qty_str}{price_str}")

    lines.append("Please review and resolve.")
    print(f"[LOG] Sending handwriting alert for invoice {invoice_num}")
    return _send_parse("\n".join(lines))


def notify_parse_success(invoice_number: str, supplier: str, file_path: str,
                          total_line: str | None = None) -> bool:
    lines = [
        "✅ *Invoice parsed*",
        f"Invoice: `{invoice_number or 'Unknown'}`",
        f"Supplier: {supplier or 'Unknown'}",
    ]
    if total_line:
        lines.append(f"Total: {total_line}")
    lines.append(f"File: `{file_path}`")
    print(f"[LOG] Sending parse success for invoice {invoice_number}")
    return _send_parse("\n".join(lines))


def notify_parse_failure(file_path: str, error_msg: str) -> bool:
    lines = [
        "❌ *Invoice parse failed*",
        f"File: `{file_path}`",
        f"Error: _{error_msg}_",
    ]
    print(f"[LOG] Sending parse failure for {file_path}")
    return _send_parse("\n".join(lines))


def notify_cross_check_warnings(invoice: dict, warnings: list, file_path: str) -> bool:
    invoice_num = invoice.get("invoice_number") or "Unknown"
    supplier = invoice.get("supplier_name") or "Unknown supplier"
    lines = [
        f"🔢 *Cross-check failed* — {len(warnings)} mismatch(es)",
        f"Invoice: `{invoice_num}` ({supplier})",
    ]
    for w in warnings:
        if w["type"] == "line_total_mismatch":
            lines.append(
                f"  • {w['product']}: {w['qty']} × RM{w['unit_price']} = RM{w['expected']} ≠ RM{w['actual']}"
            )
        elif w["type"] == "invoice_total_mismatch":
            lines.append(
                f"  • Lines sum RM{w['computed']} ≠ Invoice total RM{w['invoice_total']}"
            )
    lines.append(f"_File: {file_path}_")
    print(f"[LOG] Sending cross-check warning for invoice {invoice_num} ({len(warnings)} mismatch(es))")
    return _send_parse("\n".join(lines))


def notify_tier4_items(invoice: dict, tier4_records: list, file_path: str) -> None:
    invoice_num = invoice.get("invoice_number") or "Unknown"
    supplier = invoice.get("supplier_name") or "Unknown supplier"

    for rec in tier4_records:
        rid = rec["id"]
        name = rec["product_name"] or "(no name)"
        qty = rec.get("quantity")
        unit_str = rec.get("unit") or ""
        price = rec.get("unit_price")
        price_str = f" · RM{price}" if price else ""
        qty_str = f" · {qty} {unit_str}".rstrip() if qty else ""

        lines = [
            f"🔍 *Unmatched product*",
            f"Invoice: {invoice_num} ({supplier})",
            f"`[{rid}]` {name}{qty_str}{price_str}",
            "",
        ]
        for i, c in enumerate(rec.get("candidates", [])[:5], 1):
            lines.append(f"  {i}\\. {c['name']}")
        lines += [
            "",
            f"`link {rid} <N>` — link to candidate",
            f"`new {rid} <ingredient>` — create new product",
            f"`skip {rid}` — skip",
        ]
        _send_seatable("\n".join(lines))

    print(f"[LOG] Sent {len(tier4_records)} tier4 alert(s) for invoice {invoice_num}")


def notify_invoice_comparison(payload: dict, _skip_setup_check: bool = False) -> bool:
    from step2_compare import format_telegram_message
    from approval_handler import save_pending, build_inline_keyboard
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    invoice_num = payload.get("invoice_number", "Unknown")
    supplier_name = payload.get("supplier_name", "Unknown")
    supplier_matched = payload.get("supplier_matched", True)
    supplier_row_id = payload.get("supplier_row_id", "")
    unmatched = payload.get("unmatched_items", [])

    needs_supplier_setup = not _skip_setup_check and not supplier_matched
    needs_product_setup = (
        not _skip_setup_check
        and supplier_matched
        and bool(unmatched)
    )

    if needs_supplier_setup or needs_product_setup:
        from setup_handler import save_setup_state

        items_state = [{
            "product_name": u["product_name"],
            "invoice_unit": u.get("invoice_unit"),
            "invoice_unit_price": u.get("invoice_unit_price"),
            "product_row_id": None,
            "product_added": False,
            "ingredient_row_id": None,
            "ingredient_linked": False,
            "status": "pending",
        } for u in unmatched]

        if needs_supplier_setup:
            setup_step = "supplier"
            supplier_added_flag = False
            state_supplier_row_id = None
            text = (
                f"*Setup Required* — {invoice_num}\n"
                f"Supplier: `{supplier_name}`\n"
                f"Not found in Seatable.\n\n"
                f"{len(items_state)} product(s) to set up after.\n\n"
                f"Create this supplier?"
            )
            keyboard = [[
                InlineKeyboardButton("Add Supplier", callback_data=f"add_supplier:{invoice_num}"),
                InlineKeyboardButton("Skip", callback_data=f"skip_supplier:{invoice_num}"),
            ]]
        else:
            setup_step = "product"
            supplier_added_flag = True
            state_supplier_row_id = supplier_row_id
            first_product = items_state[0]["product_name"] if items_state else "(none)"
            text = (
                f"*Setup Required* — {invoice_num}\n"
                f"Supplier: `{supplier_name}` (already in Seatable)\n\n"
                f"{len(items_state)} unmatched product(s).\n"
                f"Item 1/{len(items_state)}: {first_product}\n\n"
                f"Create this product?"
            )
            keyboard = [[
                InlineKeyboardButton("Add Product", callback_data=f"add_product:{invoice_num}"),
                InlineKeyboardButton("Skip", callback_data=f"skip_product:{invoice_num}"),
            ]]

        save_setup_state(invoice_num, {
            "invoice_number": invoice_num,
            "supplier_name": supplier_name,
            "supplier_row_id": state_supplier_row_id,
            "supplier_added": supplier_added_flag,
            "items": items_state,
            "current_item_idx": 0,
            "setup_step": setup_step,
            "setup_complete": False,
            "invoice_file_path": payload.get("invoice_file_path", ""),
            "original_payload": payload,
        })
        save_pending(invoice_num, payload)
        return _send_seatable(text, reply_markup=InlineKeyboardMarkup(keyboard).to_dict())

    msg = format_telegram_message(payload)
    save_pending(invoice_num, payload)
    keyboard = build_inline_keyboard(payload)
    markup = keyboard.to_dict() if keyboard else None
    return _send_seatable(msg, reply_markup=markup)


def notify_cost_alert(text: str) -> bool:
    print(f"[LOG] Sending cost alert ({len(text)} chars)")
    return _send_seatable(text)
