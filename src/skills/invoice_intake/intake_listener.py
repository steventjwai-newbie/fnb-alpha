"""
Invoice intake daemon. Long-polls Telegram for invoice photos/PDFs.
CLI: python src/skills/invoice_intake/intake_listener.py
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

_PARSE_DIR = Path(__file__).parent.parent / "parse_invoice"
_INTAKE_DIR = Path(__file__).parent
sys.path.insert(0, str(_PARSE_DIR))
sys.path.insert(0, str(_INTAKE_DIR))

from step1_extract import extract_invoice
from step2_compare import build_comparison
from notifier import notify_parse_success, notify_parse_failure, notify_invoice_comparison, notify_cross_check_warnings
from cross_check import check_invoice as _cross_check
from pdf_combiner import combine_to_pdf

_RECEIVER_TOKEN = os.getenv("INVOICE_RECEIVER_TOKEN")
_GROUP_CHAT_ID = int(os.getenv("INVOICE_GROUP_CHAT_ID", "-5257569290"))

_DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"
_INBOX_DIR = _DATA_DIR / "invoices_inbox"
_PARSED_DIR = _DATA_DIR / "parsed_results"
_STATE_PATH = _DATA_DIR / "invoice_intake_state.json"

_TG_API = f"https://api.telegram.org/bot{_RECEIVER_TOKEN}"


# ── State ──────────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if _STATE_PATH.exists():
        with open(_STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"last_update_id": 0}


def _save_state(state: dict) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# ── Telegram helpers ───────────────────────────────────────────────────────────

def _send_reply(chat_id: int, text: str) -> None:
    requests.post(f"{_TG_API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)


def _download_file(file_id: str, output_path: Path) -> bool:
    resp = requests.get(f"{_TG_API}/getFile", params={"file_id": file_id}, timeout=10)
    if not resp.ok:
        print(f"[LOG] getFile failed: {resp.status_code} {resp.text}")
        return False
    tg_path = resp.json()["result"]["file_path"]
    r = requests.get(f"https://api.telegram.org/file/bot{_RECEIVER_TOKEN}/{tg_path}", timeout=60)
    if not r.ok:
        print(f"[LOG] Download failed: {r.status_code}")
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(r.content)
    return True


# ── Page detection ─────────────────────────────────────────────────────────────

def detect_page_number(raw_text: str) -> tuple[int | None, int | None]:
    """Returns (page_num, total_pages). Both None if not found."""
    if not raw_text:
        return None, None

    patterns = [
        r"page\s*(\d+)\s*(?:of|/)\s*(\d+)",
        r"pg\.?\s*(\d+)\s*(?:of|/)\s*(\d+)",
        r"\b(\d+)\s*/\s*(\d+)\b",  # bare "1/2" — risky but common
    ]
    for pat in patterns:
        m = re.search(pat, raw_text, re.IGNORECASE)
        if m:
            try:
                page = int(m.group(1))
                total = int(m.group(2))
                if 1 <= page <= total <= 20:
                    return page, total
            except ValueError:
                continue
    return None, None


def _normalize_source_files(raw: list) -> list[dict]:
    """Backwards compat: wrap old list[str] entries as dicts."""
    result = []
    for s in raw:
        if isinstance(s, str):
            result.append({"path": s, "page_num": None, "total_pages": None})
        elif isinstance(s, dict) and s.get("path"):
            result.append(s)
    return result


# ── Parsed result persistence ─────────────────────────────────────────────────

def _save_parsed_result(step1: dict, source_files: list[dict]) -> str:
    invoices = step1.get("invoices", [])
    invoice_number = (invoices[0].get("invoice_number") or "unknown").strip() if invoices else "unknown"
    safe_num = invoice_number.replace("/", "-").replace("\\", "-")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    date_dir = _PARSED_DIR / datetime.now().strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    out_path = date_dir / f"{safe_num}_{timestamp}.json"
    payload = {**step1, "source_files": source_files}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
    print(f"[LOG] Parsed result saved: {out_path}", flush=True)
    return str(out_path)


def _find_existing_result(invoice_number: str) -> dict | None:
    if not _PARSED_DIR.exists():
        return None
    safe_num = invoice_number.replace("/", "-").replace("\\", "-")
    candidates = sorted(
        _PARSED_DIR.rglob(f"{safe_num}_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            invoices = data.get("invoices", [])
            if invoices and (invoices[0].get("invoice_number") or "").strip() == invoice_number:
                print(f"[LOG] Found existing result for {invoice_number}: {path}")
                return data
        except Exception:
            continue
    return None


# ── Core processing ────────────────────────────────────────────────────────────

def _process_file(file_path: str, state: dict) -> None:
    step1 = extract_invoice(file_path)

    if step1.get("status") == "error":
        notify_parse_failure(file_path, step1.get("error_message", "unknown error"))
        return

    invoices = step1.get("invoices", [])
    if not invoices:
        notify_parse_failure(file_path, "No invoices found in file")
        return

    invoice = invoices[0]
    invoice_number = (invoice.get("invoice_number") or "").strip()
    supplier = invoice.get("supplier_name") or ""

    if not invoice_number and not supplier:
        print(f"[LOG] Filtered non-invoice: {file_path}")
        _send_reply(_GROUP_CHAT_ID, "Doesn't look like an invoice — no invoice number or supplier detected. Ignored.")
        Path(file_path).unlink(missing_ok=True)
        return

    page_num, total_pages = detect_page_number(invoice.get("raw_text", ""))
    source_files: list[dict] = [{"path": file_path, "page_num": page_num, "total_pages": total_pages}]

    if invoice_number:
        existing = _find_existing_result(invoice_number)
        if existing:
            raw_prev = existing.get("source_files") or [existing.get("file_path", "")]
            prev_sources = _normalize_source_files(raw_prev)
            prev_sources = [e for e in prev_sources if Path(e["path"]).exists()]
            source_files = prev_sources + [{"path": file_path, "page_num": page_num, "total_pages": total_pages}]

            all_have_page = all(e.get("page_num") is not None for e in source_files)
            if all_have_page:
                source_files = sorted(source_files, key=lambda e: e["page_num"])
                print(f"[LOG] Sorted {len(source_files)} pages by page_num for invoice {invoice_number}")
            else:
                print(f"[LOG] page_order_uncertain for invoice {invoice_number} — keeping receive order")

            safe_num = invoice_number.replace("/", "-").replace("\\", "-")
            combined_dir = _INBOX_DIR / datetime.now().strftime("%Y-%m-%d")
            combined_path = str(combined_dir / f"combined_{safe_num}.pdf")
            combine_to_pdf([e["path"] for e in source_files], combined_path)
            print(f"[LOG] Combined {len(source_files)} files for invoice {invoice_number}")

            step1 = extract_invoice(combined_path)
            if step1.get("status") == "error":
                notify_parse_failure(combined_path, step1.get("error_message", "unknown error"))
                return
            invoices = step1.get("invoices", [])
            if not invoices:
                notify_parse_failure(combined_path, "No invoices found after combining")
                return

            if not all_have_page:
                for inv in step1.get("invoices", []):
                    inv.setdefault("flags", []).append("page_order_uncertain")

            invoice = invoices[0]
            file_path = combined_path
    else:
        print(f"[LOG] No invoice number — cannot dedupe: {file_path}")

    for inv in step1.get("invoices", []):
        warnings = _cross_check(inv)
        if warnings:
            inv["cross_check_warnings"] = warnings
            notify_cross_check_warnings(inv, warnings, file_path)

    try:
        _save_parsed_result(step1, source_files)
    except Exception as e:
        print(f"[LOG] ERROR saving parsed result: {e}", flush=True)

    notify_parse_success(invoice_number or "Unknown", supplier, file_path)

    payloads = build_comparison(step1)
    for payload in payloads:
        notify_invoice_comparison(payload)


# ── Update handler ─────────────────────────────────────────────────────────────

def _handle_update(update: dict, state: dict) -> None:
    message = update.get("message") or update.get("channel_post")
    if not message:
        return

    chat_id = message.get("chat", {}).get("id")
    print(f"[LOG] Message from chat_id={chat_id} (whitelist={_GROUP_CHAT_ID})")

    if chat_id != _GROUP_CHAT_ID:
        return

    photo = message.get("photo")
    document = message.get("document")
    media_group_id = message.get("media_group_id")

    if photo:
        file_id = photo[-1]["file_id"]
        extension = ".jpg"
    elif document:
        mime = document.get("mime_type", "")
        fname = document.get("file_name", "file")
        extension = ".pdf" if "pdf" in mime else (Path(fname).suffix or ".bin")
        file_id = document["file_id"]
    else:
        print(f"[LOG] non-media message ignored")
        return

    date_str = datetime.now().strftime("%Y-%m-%d")
    tag = f"_{media_group_id}" if media_group_id else ""
    filename = f"{chat_id}_{file_id}{tag}{extension}"
    output_path = _INBOX_DIR / date_str / filename

    print(f"[LOG] Downloading {file_id} → {output_path}")
    if not _download_file(file_id, output_path):
        notify_parse_failure(str(output_path), "Failed to download file from Telegram")
        return

    try:
        _process_file(str(output_path), state)
    except Exception as e:
        print(f"[LOG] Error processing {output_path}: {e}")
        notify_parse_failure(str(output_path), str(e))


# ── Main loop ──────────────────────────────────────────────────────────────────

def main():
    print("[LOG] Invoice intake daemon started. Polling...")
    state = _load_state()

    while True:
        try:
            params = {
                "offset": state["last_update_id"] + 1,
                "timeout": 30,
                "allowed_updates": ["message"],
            }
            resp = requests.get(f"{_TG_API}/getUpdates", params=params, timeout=40)

            if not resp.ok:
                print(f"[LOG] getUpdates failed: {resp.status_code} {resp.text}")
                time.sleep(5)
                continue

            updates = resp.json().get("result", [])

            for update in updates:
                try:
                    _handle_update(update, state)
                except Exception as e:
                    print(f"[LOG] Error handling update {update.get('update_id')}: {e}")
                finally:
                    state["last_update_id"] = max(
                        state["last_update_id"], update.get("update_id", 0)
                    )
                    _save_state(state)

        except KeyboardInterrupt:
            print("[LOG] Shutting down.")
            _save_state(state)
            break
        except Exception as e:
            print(f"[LOG] Outer loop error: {e}. Continuing...")
            time.sleep(5)


if __name__ == "__main__":
    main()
