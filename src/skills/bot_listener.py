"""
Telegram bot listener for seatable_update_bot.
Polls for user replies to Tier-4 unmatched items and handles:

  link <id> <N>                       — link product to fuzzy candidate N
  new <id> <ingredient>               — create new Supplier Product row
  skip <id>                           — dismiss product match
  newsupplier <id>                    — create new Supplier row
  linksupplier <id> <N>               — link invoice supplier to candidate N
  skipsupplier <id>                   — dismiss supplier match
  /yes                                — confirm pending action
  /no  or  /cancel                    — cancel pending action
  /pending                            — list all unresolved items (products + suppliers)
  /addproduct <name> | <ingredient>   — manual product creation

Run via Windows Task Scheduler every 5 minutes:
  python C:\\Users\\Admin\\projects\\fnb-alpha\\src\\skills\\bot_listener.py
"""
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv
from rapidfuzz import fuzz, process
from seatable_api import Base

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent / "parse_invoice"))

from pending_matches_store import get_by_id, get_all_pending, resolve
import pending_suppliers_store as _sup_store

_BOT_TOKEN = os.getenv("SEATABLE_BOT_TOKEN")
_CHAT_ID = os.getenv("SEATABLE_BOT_CHAT_ID", "-5150446443")
SEATABLE_API_TOKEN = os.getenv("SEATABLE_API_TOKEN")
SEATABLE_BASE_URL = os.getenv("SEATABLE_BASE_URL")

_STATE_PATH = Path(__file__).parent.parent.parent / "data" / "bot_state.json"


# ── State ──────────────────────────────────────────────────────────────────────

def _load_state() -> Dict[str, Any]:
    if _STATE_PATH.exists():
        with open(_STATE_PATH, encoding="utf-8-sig") as f:
            return json.load(f)
    return {"last_update_id": 0, "pending_confirmation": None}


def _save_state(state: Dict[str, Any]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# ── Telegram helpers ────────────────────────────────────────────────────────────

def _send(text: str) -> None:
    url = f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": _CHAT_ID, "text": text, "parse_mode": "Markdown"})


def _get_updates(offset: int) -> list:
    url = f"https://api.telegram.org/bot{_BOT_TOKEN}/getUpdates"
    resp = requests.get(url, params={"offset": offset, "timeout": 0, "limit": 20})
    if not resp.ok:
        print(f"[LOG] getUpdates error: {resp.status_code}")
        return []
    return resp.json().get("result", [])


# ── Seatable helpers ────────────────────────────────────────────────────────────

def _seatable_base() -> Base:
    base = Base(SEATABLE_API_TOKEN, SEATABLE_BASE_URL)
    base.auth()
    return base


def _find_ingredient_id(base: Base, ingredient_name: str) -> Optional[Dict[str, str]]:
    rows = base.list_rows("Ingredients")
    names = [r.get("Ingredient Name", "") for r in rows]
    results = process.extract(ingredient_name, names, scorer=fuzz.token_sort_ratio, limit=1)
    if not results:
        return None
    best_name, score, idx = results[0]
    if score < 60:
        return None
    return {"name": best_name, "id": rows[idx].get("_id", "")}


def _find_supplier_id(base: Base, supplier_name: str) -> Optional[Dict[str, str]]:
    rows = base.list_rows("Suppliers")
    names = [r.get("Supplier Name", "") for r in rows]
    results = process.extract(supplier_name, names, scorer=fuzz.token_sort_ratio, limit=1)
    if not results:
        return None
    best_name, score, idx = results[0]
    if score < 60:
        return None
    return {"name": best_name, "id": rows[idx].get("_id", "")}


def _create_supplier_product(record: Dict[str, Any], ingredient_name: str) -> Dict[str, Any]:
    base = _seatable_base()

    ingredient = _find_ingredient_id(base, ingredient_name)
    supplier = _find_supplier_id(base, record.get("supplier_name", ""))

    row_data: Dict[str, Any] = {
        "Supplier Product Name": record["product_name"],
        "Active Status": "Active",
    }
    if record.get("unit_price") is not None:
        row_data["Price per Pack"] = record["unit_price"]
    if record.get("unit"):
        row_data["Unit of Measure"] = record["unit"]

    new_row = base.append_row("Supplier Products", row_data)
    new_id = new_row.get("_id", "")

    if new_id:
        meta = base.get_metadata()
        tables = {t["name"]: t for t in meta.get("tables", [])}
        sp_table = tables.get("Supplier Products", {})
        cols = {c["name"]: c for c in sp_table.get("columns", [])}

        if supplier and "Supplier" in cols:
            try:
                base.add_link(cols["Supplier"]["key"], "Supplier Products", "Suppliers", new_id, supplier["id"])
            except Exception as e:
                print(f"[LOG] Supplier link failed: {e}")

        if ingredient and "Ingredients" in cols:
            try:
                base.add_link(cols["Ingredients"]["key"], "Supplier Products", "Ingredients", new_id, ingredient["id"])
            except Exception as e:
                print(f"[LOG] Ingredient link failed: {e}")

    return {
        "new_id": new_id,
        "ingredient_matched": ingredient["name"] if ingredient else None,
        "supplier_matched": supplier["name"] if supplier else None,
    }


# ── Command handlers ────────────────────────────────────────────────────────────

def _handle_link(parts: list, state: Dict[str, Any]) -> None:
    # link <record_id> <N>
    if len(parts) < 3:
        _send("Usage: `link <id> <candidate number>`")
        return
    record_id, num_str = parts[1], parts[2]
    if not num_str.isdigit():
        _send("Candidate number must be a digit, e.g. `link abc12345 2`")
        return
    num = int(num_str)

    record = get_by_id(record_id)
    if not record:
        _send(f"No pending item with id `{record_id}`.")
        return
    if record["status"] != "pending":
        _send(f"`{record_id}` is already resolved ({record['status']}).")
        return

    candidates = record.get("candidates", [])
    if num < 1 or num > len(candidates):
        _send(f"Candidate {num} out of range (1–{len(candidates)}).")
        return

    chosen = candidates[num - 1]
    state["pending_confirmation"] = {
        "action": "link",
        "record_id": record_id,
        "product_name": record["product_name"],
        "matched_product_name": chosen["name"],
        "matched_product_id": chosen["id"],
    }
    _send(
        f"Confirm: link *{record['product_name']}* → *{chosen['name']}*?\n"
        f"Reply `/yes` to confirm or `/no` to cancel."
    )


def _handle_new(parts: list, state: Dict[str, Any]) -> None:
    # new <record_id> <ingredient name...>
    if len(parts) < 3:
        _send("Usage: `new <id> <ingredient name>`")
        return
    record_id = parts[1]
    ingredient_name = " ".join(parts[2:])

    record = get_by_id(record_id)
    if not record:
        _send(f"No pending item with id `{record_id}`.")
        return
    if record["status"] != "pending":
        _send(f"`{record_id}` is already resolved ({record['status']}).")
        return

    state["pending_confirmation"] = {
        "action": "new",
        "record_id": record_id,
        "product_name": record["product_name"],
        "ingredient_name": ingredient_name,
        "unit_price": record.get("unit_price"),
        "unit": record.get("unit"),
        "supplier_name": record.get("supplier_name"),
    }
    price_str = f"RM{record.get('unit_price')}" if record.get("unit_price") else "no price"
    _send(
        f"Confirm: create new Supplier Product\n"
        f"  Name: *{record['product_name']}*\n"
        f"  Ingredient: *{ingredient_name}*\n"
        f"  Supplier: {record.get('supplier_name') or 'unknown'}\n"
        f"  Price: {price_str}\n"
        f"Reply `/yes` to create or `/no` to cancel."
    )


def _handle_skip(parts: list) -> None:
    if len(parts) < 2:
        _send("Usage: `skip <id>`")
        return
    record_id = parts[1]
    record = get_by_id(record_id)
    if not record:
        _send(f"No pending item with id `{record_id}`.")
        return
    resolve(record_id, {"type": "skipped"})
    _send(f"Skipped `{record['product_name']}` ({record_id}).")


def _handle_yes(state: Dict[str, Any]) -> None:
    conf = state.get("pending_confirmation")
    if not conf:
        _send("No pending action to confirm.")
        return

    action = conf["action"]
    state["pending_confirmation"] = None

    if action == "link":
        resolve(conf["record_id"], {
            "type": "linked",
            "matched_product_name": conf["matched_product_name"],
            "matched_product_id": conf["matched_product_id"],
        })
        _send(f"✅ Linked *{conf['product_name']}* → *{conf['matched_product_name']}*.")

    elif action == "new":
        _send("Creating new Supplier Product in Seatable...")
        try:
            result = _create_supplier_product(conf, conf["ingredient_name"])
            resolve(conf["record_id"], {
                "type": "created",
                "new_product_id": result["new_id"],
                "ingredient_matched": result["ingredient_matched"],
                "supplier_matched": result["supplier_matched"],
            })
            _send(
                f"✅ Created *{conf['product_name']}*\n"
                f"  Ingredient: {result['ingredient_matched'] or '⚠️ not matched'}\n"
                f"  Supplier: {result['supplier_matched'] or '⚠️ not matched'}"
            )
        except Exception as e:
            _send(f"❌ Failed to create product: {e}")

    elif action == "addproduct":
        _send("Creating new Supplier Product in Seatable...")
        try:
            result = _create_supplier_product(conf, conf["ingredient_name"])
            _send(
                f"✅ Created *{conf['product_name']}*\n"
                f"  Ingredient: {result['ingredient_matched'] or '⚠️ not matched'}\n"
                f"  Supplier: {result['supplier_matched'] or '⚠️ not matched'}"
            )
        except Exception as e:
            _send(f"❌ Failed to create product: {e}")

    elif action == "newsupplier":
        _send("Creating new Supplier in Seatable...")
        try:
            base = _seatable_base()
            new_row = base.append_row("Suppliers", {"Supplier Name": conf["supplier_name"]})
            new_id = new_row.get("_id", "")
            _sup_store.resolve(conf["record_id"], {"type": "created", "new_supplier_id": new_id})
            _send(f"✅ Created supplier *{conf['supplier_name']}*.")
        except Exception as e:
            _send(f"❌ Failed to create supplier: {e}")

    elif action == "linksupplier":
        _sup_store.resolve(conf["record_id"], {
            "type": "linked",
            "matched_supplier_name": conf["matched_supplier_name"],
            "matched_supplier_id": conf["matched_supplier_id"],
        })
        _send(f"✅ Linked *{conf['invoice_supplier_name']}* → *{conf['matched_supplier_name']}*.")


def _handle_addproduct(text: str, state: Dict[str, Any]) -> None:
    # /addproduct <name> | <ingredient>
    body = text[len("/addproduct"):].strip()
    if "|" not in body:
        _send("Usage: `/addproduct <product name> | <ingredient name>`")
        return
    name, ingredient = [p.strip() for p in body.split("|", 1)]
    if not name or not ingredient:
        _send("Both product name and ingredient name are required.")
        return

    state["pending_confirmation"] = {
        "action": "addproduct",
        "product_name": name,
        "ingredient_name": ingredient,
        "supplier_name": None,
        "unit_price": None,
        "unit": None,
    }
    _send(
        f"Confirm: create new Supplier Product\n"
        f"  Name: *{name}*\n"
        f"  Ingredient: *{ingredient}*\n"
        f"Reply `/yes` to create or `/no` to cancel."
    )


def _handle_newsupplier(parts: list, state: Dict[str, Any]) -> None:
    # newsupplier <record_id>
    if len(parts) < 2:
        _send("Usage: `newsupplier <id>`")
        return
    record_id = parts[1]
    record = _sup_store.get_by_id(record_id)
    if not record:
        _send(f"No pending supplier with id `{record_id}`.")
        return
    if record["status"] != "pending":
        _send(f"`{record_id}` is already resolved ({record['status']}).")
        return

    state["pending_confirmation"] = {
        "action": "newsupplier",
        "record_id": record_id,
        "supplier_name": record["invoice_supplier_name"],
    }
    _send(
        f"Confirm: create new supplier *{record['invoice_supplier_name']}*?\n"
        f"Reply `/yes` to confirm or `/no` to cancel."
    )


def _handle_linksupplier(parts: list, state: Dict[str, Any]) -> None:
    # linksupplier <record_id> <N>
    if len(parts) < 3:
        _send("Usage: `linksupplier <id> <candidate number>`")
        return
    record_id, num_str = parts[1], parts[2]
    if not num_str.isdigit():
        _send("Candidate number must be a digit, e.g. `linksupplier abc12345 2`")
        return
    num = int(num_str)

    record = _sup_store.get_by_id(record_id)
    if not record:
        _send(f"No pending supplier with id `{record_id}`.")
        return
    if record["status"] != "pending":
        _send(f"`{record_id}` is already resolved ({record['status']}).")
        return

    candidates = record.get("candidates", [])
    if num < 1 or num > len(candidates):
        _send(f"Candidate {num} out of range (1–{len(candidates)}).")
        return

    chosen = candidates[num - 1]
    state["pending_confirmation"] = {
        "action": "linksupplier",
        "record_id": record_id,
        "invoice_supplier_name": record["invoice_supplier_name"],
        "matched_supplier_name": chosen["name"],
        "matched_supplier_id": chosen["id"],
    }
    _send(
        f"Confirm: *{record['invoice_supplier_name']}* → *{chosen['name']}*?\n"
        f"Reply `/yes` to confirm or `/no` to cancel."
    )


def _handle_skipsupplier(parts: list) -> None:
    if len(parts) < 2:
        _send("Usage: `skipsupplier <id>`")
        return
    record_id = parts[1]
    record = _sup_store.get_by_id(record_id)
    if not record:
        _send(f"No pending supplier with id `{record_id}`.")
        return
    _sup_store.resolve(record_id, {"type": "skipped"})
    _send(f"Skipped supplier `{record['invoice_supplier_name']}` ({record_id}).")


def _handle_pending() -> None:
    products = get_all_pending()
    suppliers = _sup_store.get_all_pending()

    if not products and not suppliers:
        _send("No pending unmatched items.")
        return

    lines = []
    if products:
        lines.append(f"*{len(products)} unmatched product(s):*")
        for r in products:
            lines.append(
                f"`[{r['id']}]` {r['product_name']}\n"
                f"  Invoice: {r['invoice_number']} · {r['supplier_name']}\n"
                f"  {len(r.get('candidates', []))} candidate(s)"
            )

    if suppliers:
        if lines:
            lines.append("")
        lines.append(f"*{len(suppliers)} unmatched supplier(s):*")
        for r in suppliers:
            lines.append(
                f"`[{r['id']}]` {r['invoice_supplier_name']}\n"
                f"  Invoice: {r['invoice_number']}\n"
                f"  {len(r.get('candidates', []))} candidate(s)"
            )

    _send("\n".join(lines))


# ── Main loop ───────────────────────────────────────────────────────────────────

def run() -> None:
    state = _load_state()
    updates = _get_updates(state["last_update_id"] + 1)

    for update in updates:
        state["last_update_id"] = update["update_id"]
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            continue

        text = (msg.get("text") or "").strip()
        if not text:
            continue

        print(f"[LOG] Received: {text!r}")
        lower = text.lower()
        parts = lower.split()

        if lower.startswith("link "):
            _handle_link(lower.split(), state)
        elif lower.startswith("new "):
            orig_parts = text.split(None, 2)
            _handle_new([p.lower() for p in orig_parts[:2]] + ([orig_parts[2]] if len(orig_parts) > 2 else []), state)
        elif lower.startswith("skip "):
            _handle_skip(parts)
        elif lower.startswith("newsupplier "):
            _handle_newsupplier(parts, state)
        elif lower.startswith("linksupplier "):
            _handle_linksupplier(parts, state)
        elif lower.startswith("skipsupplier "):
            _handle_skipsupplier(parts)
        elif lower in ("/yes", "yes"):
            _handle_yes(state)
        elif lower in ("/no", "/cancel", "no", "cancel"):
            state["pending_confirmation"] = None
            _send("Cancelled.")
        elif lower.startswith("/addproduct"):
            _handle_addproduct(text, state)
        elif lower == "/pending":
            _handle_pending()
        else:
            _send(
                "Commands:\n"
                "`link <id> <N>` — link product to candidate\n"
                "`new <id> <ingredient>` — create new product\n"
                "`skip <id>` — skip product\n"
                "`newsupplier <id>` — create new supplier\n"
                "`linksupplier <id> <N>` — link supplier to candidate\n"
                "`skipsupplier <id>` — skip supplier\n"
                "`/pending` — list all unresolved\n"
                "`/addproduct <name> | <ingredient>` — manual product entry\n"
                "`/yes` / `/no` — confirm or cancel"
            )

    _save_state(state)
    print(f"[LOG] Processed {len(updates)} update(s). Last id: {state['last_update_id']}")


if __name__ == "__main__":
    run()
