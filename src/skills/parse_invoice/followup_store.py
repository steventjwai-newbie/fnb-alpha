import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any

_STORE_PATH = Path(__file__).parent.parent.parent.parent / "data" / "pending_followups.json"


def _load() -> List[Dict[str, Any]]:
    if not _STORE_PATH.exists():
        return []
    with open(_STORE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(records: List[Dict[str, Any]]):
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)


def add_followup(invoice: dict, file_path: str) -> str:
    records = _load()
    now = datetime.now()
    record = {
        "id": str(uuid.uuid4()),
        "invoice_number": invoice.get("invoice_number"),
        "supplier_name": invoice.get("supplier_name"),
        "file_path": str(file_path),
        "handwriting_content": invoice.get("handwriting_content"),
        "parsed_at": now.isoformat(),
        "followup_due": (now + timedelta(days=7)).isoformat(),
        "followup_sent": False,
    }
    records.append(record)
    _save(records)
    print(f"[LOG] Follow-up scheduled for invoice {record['invoice_number']} on {record['followup_due'][:10]}")
    return record["id"]


def get_due_followups() -> List[Dict[str, Any]]:
    now = datetime.now()
    return [
        r for r in _load()
        if not r.get("followup_sent")
        and datetime.fromisoformat(r["followup_due"]) <= now
    ]


def mark_followup_sent(record_id: str):
    records = _load()
    for r in records:
        if r["id"] == record_id:
            r["followup_sent"] = True
    _save(records)


def get_all_pending() -> List[Dict[str, Any]]:
    return [r for r in _load() if not r.get("followup_sent")]
