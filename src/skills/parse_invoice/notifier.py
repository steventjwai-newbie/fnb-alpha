import os
import requests
from dotenv import load_dotenv

load_dotenv()

_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-5257569290")

_SEATABLE_BOT_TOKEN = os.getenv("SEATABLE_BOT_TOKEN")
_SEATABLE_CHAT_ID = os.getenv("SEATABLE_BOT_CHAT_ID", "-5150446443")


def _send(text: str) -> bool:
    url = f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={"chat_id": _CHAT_ID, "text": text, "parse_mode": "Markdown"})
    if not resp.ok:
        print(f"[LOG] Telegram error: {resp.status_code} {resp.text}")
    return resp.ok


def notify_handwriting_detected(invoice: dict, file_path: str) -> bool:
    invoice_num = invoice.get("invoice_number") or "Unknown"
    supplier = invoice.get("supplier_name") or "Unknown supplier"
    content = invoice.get("handwriting_content") or "unreadable"
    text = (
        f"⚠️ *Handwriting detected*\n"
        f"Invoice: `{invoice_num}`\n"
        f"Supplier: {supplier}\n"
        f"File: `{file_path}`\n"
        f"Content: _{content}_\n"
        f"Please review and resolve."
    )
    print(f"[LOG] Sending handwriting alert for invoice {invoice_num}")
    return _send(text)


def _send_seatable(text: str) -> bool:
    url = f"https://api.telegram.org/bot{_SEATABLE_BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={"chat_id": _SEATABLE_CHAT_ID, "text": text, "parse_mode": "Markdown"})
    if not resp.ok:
        print(f"[LOG] Seatable bot error: {resp.status_code} {resp.text}")
    return resp.ok


def notify_tier4_items(invoice: dict, tier4_records: list, file_path: str) -> bool:
    """Send unmatched item prompt to seatable_update_bot. tier4_records: [{id, product_name, quantity, unit, unit_price, candidates}]"""
    invoice_num = invoice.get("invoice_number") or "Unknown"
    supplier = invoice.get("supplier_name") or "Unknown supplier"

    lines = [f"🔍 *Unmatched items — {invoice_num}* ({supplier})\n"]
    for rec in tier4_records:
        rid = rec["id"]
        name = rec["product_name"] or "(no name)"
        qty = rec.get("quantity")
        unit = rec.get("unit") or ""
        price = rec.get("unit_price")
        price_str = f" · RM{price}" if price else ""
        qty_str = f" · {qty} {unit}".rstrip() if qty else ""
        lines.append(f"`[{rid}]` {name}{qty_str}{price_str}")
        for i, c in enumerate(rec.get("candidates", [])[:5], 1):
            lines.append(f"  {i}\\. {c['name']}")
        lines.append("")

    lines.append("*Reply:*")
    lines.append("`link <id> <N>` — link to candidate N")
    lines.append("`new <id> <ingredient name>` — create new product")
    lines.append("`skip <id>` — skip for now")
    lines.append(f"\n_File: {file_path}_")

    text = "\n".join(lines)
    print(f"[LOG] Sending tier4 alert for invoice {invoice_num} ({len(tier4_records)} items)")
    return _send_seatable(text)


def notify_followup_due(record: dict) -> bool:
    invoice_num = record.get("invoice_number") or "Unknown"
    supplier = record.get("supplier_name") or "Unknown supplier"
    content = record.get("handwriting_content") or "unreadable"
    parsed_date = record.get("parsed_at", "")[:10]
    text = (
        f"\U0001f4cb *7-day follow-up*\n"
        f"Invoice: `{invoice_num}`\n"
        f"Supplier: {supplier}\n"
        f"Parsed on: {parsed_date}\n"
        f"Handwriting noted: _{content}_\n"
        f"Has this been resolved?"
    )
    print(f"[LOG] Sending follow-up alert for invoice {invoice_num}")
    return _send(text)
