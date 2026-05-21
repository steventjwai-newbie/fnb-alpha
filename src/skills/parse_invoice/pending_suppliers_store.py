"""JSON-backed store for invoices whose supplier wasn't matched in Seatable."""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_STORE_PATH = Path(__file__).parent.parent.parent.parent / "data" / "pending_suppliers.json"


def _load() -> List[Dict[str, Any]]:
    if not _STORE_PATH.exists():
        return []
    with open(_STORE_PATH, encoding="utf-8-sig") as f:
        return json.load(f)


def _save(records: List[Dict[str, Any]]) -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)


def add_pending_supplier(
    invoice_number: str,
    invoice_supplier_name: str,
    file_path: str,
    candidates: List[Dict[str, Any]],  # [{name, id, score}]
) -> str:
    records = _load()
    record_id = str(uuid.uuid4())[:8]
    records.append(
        {
            "id": record_id,
            "invoice_number": invoice_number,
            "invoice_supplier_name": invoice_supplier_name,
            "file_path": str(file_path),
            "candidates": candidates,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
            "resolved_at": None,
            "resolution": None,
        }
    )
    _save(records)
    return record_id


def get_by_id(record_id: str) -> Optional[Dict[str, Any]]:
    for r in _load():
        if r["id"] == record_id:
            return r
    return None


def get_all_pending() -> List[Dict[str, Any]]:
    return [r for r in _load() if r["status"] == "pending"]


def resolve(record_id: str, resolution: Dict[str, Any]) -> None:
    records = _load()
    for r in records:
        if r["id"] == record_id:
            r["status"] = resolution.get("type", "resolved")
            r["resolved_at"] = datetime.now(timezone.utc).isoformat()
            r["resolution"] = resolution
            break
    _save(records)
