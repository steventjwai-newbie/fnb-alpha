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


def notify_invoice_comparison(payload: dict) -> bool:
    from step2_compare import format_telegram_message
    from approval_handler import save_pending, build_inline_keyboard
    msg = format_telegram_message(payload)
    save_pending(payload["invoice_number"], payload)
    keyboard = build_inline_keyboard(payload)
    markup = keyboard.to_dict() if keyboard else None
    return _send_seatable(msg, reply_markup=markup)


def notify_missing_supplier(invoice_number: str, supplier_name: str, record_id: str, candidates: list, file_path: str) -> bool:
    lines = [f"🏭 *Unknown supplier — {invoice_number}*\n"]
    safe_name = supplier_name or "(no name)"
    lines.append(f"Invoice says: `{safe_name}`")
    if candidates:
        lines.append("Closest matches:")
        for i, c in enumerate(candidates[:5], 1):
            score_pct = int(c.get("score", 0))
            lines.append(f"  {i}\\. {c['name']} ({score_pct}%)")
    else:
        lines.append("_No close matches found._")
    lines.append("")
    lines.append(f"`newsupplier {record_id}` — create new")
    lines.append(f"`linksupplier {record_id} <N>` — link to candidate N")
    lines.append(f"`skipsupplier {record_id}` — skip")
    lines.append(f"\n_File: {file_path}_")

    print(f"[LOG] Sending missing supplier alert for invoice {invoice_number}")
    return _send_seatable("\n".join(lines))


def notify_cost_alert(text: str) -> bool:
    print(f"[LOG] Sending cost alert ({len(text)} chars)")
    return _send_seatable(text)
