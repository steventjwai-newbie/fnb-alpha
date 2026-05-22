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
    return _send("\n".join(lines))


def _send_seatable(text: str) -> bool:
    url = f"https://api.telegram.org/bot{_SEATABLE_BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={"chat_id": _SEATABLE_CHAT_ID, "text": text, "parse_mode": "Markdown"})
    if not resp.ok:
        print(f"[LOG] Seatable bot error: {resp.status_code} {resp.text}")
    return resp.ok


def notify_tier4_items(invoice: dict, tier4_records: list, file_path: str) -> None:
    """Send one Telegram message per unmatched item to seatable_update_bot."""
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
    msg = format_telegram_message(payload)
    return _send_seatable(msg)


def notify_missing_supplier(invoice_number: str, supplier_name: str, record_id: str, candidates: list, file_path: str) -> bool:
    """Alert seatable_update_bot when invoice supplier isn't in Seatable."""
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
