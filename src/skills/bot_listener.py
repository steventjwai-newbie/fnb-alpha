"""
Telegram bot listener for seatable_update_bot.

Each confirmation request sends a message; user replies to THAT message with
/yes or /no, so multiple pending actions can coexist without conflict.

Commands:
  link <id> <N>                       — link product to fuzzy candidate N
  new <id> <ingredient>               — create new Supplier Product row
  skip <id>                           — dismiss product match
  newsupplier <id>                    — create new Supplier row
  linksupplier <id> <N>               — link invoice supplier to candidate N
  skipsupplier <id>                   — dismiss supplier match
  /yes  (reply to confirm message)    — confirm that specific action
  /no   (reply to confirm message)    — cancel that specific action
  /pending                            — list all unresolved items
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
from unit_normalizer import get_base_unit_info

_BOT_TOKEN = os.getenv("SEATABLE_BOT_TOKEN")
_CHAT_ID = os.getenv("SEATABLE_BOT_CHAT_ID", "-5150446443")
SEATABLE_API_TOKEN = os.getenv("SEATABLE_API_TOKEN")
SEATABLE_BASE_URL = os.getenv("SEATABLE_BASE_URL")

_STATE_PATH = Path(__file__).parent.parent.parent / "data" / "bot_state.json"


# ── State ──────────────────────────────────────────────────────────────────────

def _load_state() -> Dict[str, Any]:
    if _STATE_PATH.exists():
        with open(_STATE_PATH, encoding="utf-8-sig") as f:
            state = json.load(f)
        # Migrate old single pending_confirmation to new dict format
        if "pending_confirmation" in state and "pending_confirmations" not in state:
            old = state.pop("pending_confirmation")
            state["pending_confirmations"] = {}
            if old:
                state["pending_confirmations"]["0"] = old
        state.setdefault("pending_confirmations", {})
        return state
    return {"last_update_id": 0, "pending_confirmations": {}}


def _save_state(state: Dict[str, Any]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# ── Telegram helpers ────────────────────────────────────────────────────────────

def _send(text: str) -> Optional[int]:
    """Send message, return message_id or None on failure."""
    url = f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={"chat_id": _CHAT_ID, "text": text, "parse_mode": "Markdown"})
    if not resp.ok:
        print(f"[LOG] Telegram error: {resp.status_code} {resp.text[:100]}")
        return None
    return resp.json().get("result", {}).get("message_id")


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
    if not results or results[0][1] < 60:
        return None
    best_name, score, idx = results[0]
    return {"name": best_name, "id": rows[idx].get("_id", "")}


def _find_supplier_id(base: Base, supplier_name: str) -> Optional[Dict[str, str]]:
    rows = base.list_rows("Suppliers")
    names = [r.get("Supplier Name", "") for r in rows]
    results = process.extract(supplier_name, names, scorer=fuzz.token_sort_ratio, limit=1)
    if not results or results[0][1] < 60:
        return None
    best_name, score, idx = results[0]
    return {"name": best_name, "id": rows[idx].get("_id", "")}


def _create_supplier_product(conf: Dict[str, Any], ingredient_name: str) -> Dict[str, Any]:
    base = _seatable_base()
    ingredient = _find_ingredient_id(base, ingredient_name)
    supplier = _find_supplier_id(base, conf.get("supplier_name") or "")

    row_data: Dict[str, Any] = {"Supplier Product Name": conf["product_name"], "Active Status": "Active"}
    unit = conf.get("unit") or ""
    unit_price = conf.get("unit_price")
    unit_info = get_base_unit_info(unit) if unit else None

    if unit_info and unit_price is not None:
        divisor, base_uom = unit_info
        unit_qty = int(divisor)
        price_per_pack = float(unit_price)
        row_data["Price per Pack"] = price_per_pack
        row_data["Unit Quantity"] = unit_qty
        row_data["Unit of Measure"] = base_uom
    elif unit_price is not None:
        row_data["Price per Pack"] = float(unit_price)
        row_data["Unit of Measure"] = unit
        print(f"[LOG] Unknown unit '{unit}' — stored raw, Unit Quantity not set")

    new_row = base.append_row("Supplier Products", row_data)
    new_id = new_row.get("_id", "")

    if new_id:
        meta = base.get_metadata()
        cols = {c["name"]: c for c in next(
            (t for t in meta.get("tables", []) if t["name"] == "Supplier Products"), {}
        ).get("columns", [])}

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

        ph_row = base.append_row("Price History", {
            "Old Price": 0.0,
            "New Price": row_data.get("Price per Pack", 0.0),
            "Change %": None,
            "Invoice Reference": conf.get("invoice_number") or "",
            "Flagged By": "New product",
        })
        ph_row_id = ph_row.get("_id", "")
        if ph_row_id:
            ph_cols = {c["name"]: c for c in next(
                (t for t in meta.get("tables", []) if t["name"] == "Price History"), {}
            ).get("columns", [])}
            if "Supplier product" in ph_cols:
                try:
                    base.add_link(ph_cols["Supplier product"]["key"], "Price History", "Supplier Products", ph_row_id, new_id)
                except Exception as e:
                    print(f"[LOG] Price History link failed: {e}")

    return {
        "new_id": new_id,
        "ingredient_matched": ingredient["name"] if ingredient else None,
        "supplier_matched": supplier["name"] if supplier else None,
    }


# ── Command handlers ────────────────────────────────────────────────────────────

def _handle_link(parts: list, state: Dict[str, Any]) -> None:
    if len(parts) < 3:
        _send("Usage: `link <id> <candidate number>`")
        return
    record_id, num_str = parts[1], parts[2]
    if not num_str.isdigit():
        _send("Candidate number must be a digit.")
        return

    record = get_by_id(record_id)
    if not record:
        _send(f"No pending item with id `{record_id}`.")
        return
    if record["status"] != "pending":
        _send(f"`{record_id}` is already resolved ({record['status']}).")
        return

    candidates = record.get("candidates", [])
    num = int(num_str)
    if num < 1 or num > len(candidates):
        _send(f"Candidate {num} out of range (1–{len(candidates)}).")
        return

    chosen = candidates[num - 1]
    msg_id = _send(
        f"Confirm: link *{record['product_name']}* → *{chosen['name']}*?\n"
        f"Reply `/yes` to this message to confirm, or `/no` to cancel."
    )
    if msg_id:
        state["pending_confirmations"][str(msg_id)] = {
            "action": "link",
            "record_id": record_id,
            "product_name": record["product_name"],
            "matched_product_name": chosen["name"],
            "matched_product_id": chosen["id"],
        }


def _handle_new(parts: list, state: Dict[str, Any]) -> None:
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

    price_str = f"RM{record.get('unit_price')}" if record.get("unit_price") else "no price"
    msg_id = _send(
        f"Confirm: create new Supplier Product\n"
        f"  Name: *{record['product_name']}*\n"
        f"  Ingredient: *{ingredient_name}*\n"
        f"  Supplier: {record.get('supplier_name') or 'unknown'}\n"
        f"  Price: {price_str}\n"
        f"Reply `/yes` to this message to confirm, or `/no` to cancel."
    )
    if msg_id:
        state["pending_confirmations"][str(msg_id)] = {
            "action": "new",
            "record_id": record_id,
            "product_name": record["product_name"],
            "ingredient_name": ingredient_name,
            "unit_price": record.get("unit_price"),
            "unit": record.get("unit"),
            "supplier_name": record.get("supplier_name"),
        }


def _handle_skip(parts: list) -> None:
    if len(parts) < 2:
        _send("Usage: `skip <id>`")
        return
    record = get_by_id(parts[1])
    if not record:
        _send(f"No pending item with id `{parts[1]}`.")
        return
    resolve(parts[1], {"type": "skipped"})
    _send(f"Skipped `{record['product_name']}`.")


def _handle_newsupplier(parts: list, state: Dict[str, Any]) -> None:
    if len(parts) < 2:
        _send("Usage: `newsupplier <id>`")
        return
    record = _sup_store.get_by_id(parts[1])
    if not record:
        _send(f"No pending supplier with id `{parts[1]}`.")
        return
    if record["status"] != "pending":
        _send(f"`{parts[1]}` is already resolved ({record['status']}).")
        return

    msg_id = _send(
        f"Confirm: create new supplier *{record['invoice_supplier_name']}*?\n"
        f"Reply `/yes` to this message to confirm, or `/no` to cancel."
    )
    if msg_id:
        state["pending_confirmations"][str(msg_id)] = {
            "action": "newsupplier",
            "record_id": parts[1],
            "supplier_name": record["invoice_supplier_name"],
        }


def _handle_linksupplier(parts: list, state: Dict[str, Any]) -> None:
    if len(parts) < 3:
        _send("Usage: `linksupplier <id> <N>`")
        return
    record_id, num_str = parts[1], parts[2]
    if not num_str.isdigit():
        _send("Candidate number must be a digit.")
        return

    record = _sup_store.get_by_id(record_id)
    if not record:
        _send(f"No pending supplier with id `{record_id}`.")
        return
    if record["status"] != "pending":
        _send(f"`{record_id}` is already resolved ({record['status']}).")
        return

    candidates = record.get("candidates", [])
    num = int(num_str)
    if num < 1 or num > len(candidates):
        _send(f"Candidate {num} out of range (1–{len(candidates)}).")
        return

    chosen = candidates[num - 1]
    msg_id = _send(
        f"Confirm: *{record['invoice_supplier_name']}* → *{chosen['name']}*?\n"
        f"Reply `/yes` to this message to confirm, or `/no` to cancel."
    )
    if msg_id:
        state["pending_confirmations"][str(msg_id)] = {
            "action": "linksupplier",
            "record_id": record_id,
            "invoice_supplier_name": record["invoice_supplier_name"],
            "matched_supplier_name": chosen["name"],
            "matched_supplier_id": chosen["id"],
        }


def _handle_skipsupplier(parts: list) -> None:
    if len(parts) < 2:
        _send("Usage: `skipsupplier <id>`")
        return
    record = _sup_store.get_by_id(parts[1])
    if not record:
        _send(f"No pending supplier with id `{parts[1]}`.")
        return
    _sup_store.resolve(parts[1], {"type": "skipped"})
    _send(f"Skipped supplier `{record['invoice_supplier_name']}`.")


def _handle_addproduct(text: str, state: Dict[str, Any]) -> None:
    body = text[len("/addproduct"):].strip()
    if "|" not in body:
        _send("Usage: `/addproduct <product name> | <ingredient name>`")
        return
    name, ingredient = [p.strip() for p in body.split("|", 1)]
    if not name or not ingredient:
        _send("Both product name and ingredient name are required.")
        return

    msg_id = _send(
        f"Confirm: create new Supplier Product\n"
        f"  Name: *{name}*\n"
        f"  Ingredient: *{ingredient}*\n"
        f"Reply `/yes` to this message to confirm, or `/no` to cancel."
    )
    if msg_id:
        state["pending_confirmations"][str(msg_id)] = {
            "action": "addproduct",
            "product_name": name,
            "ingredient_name": ingredient,
            "supplier_name": None,
            "unit_price": None,
            "unit": None,
        }


def _handle_yes(state: Dict[str, Any], reply_msg_id: str) -> None:
    confs = state.get("pending_confirmations", {})

    # Prefer the message the user replied to; fall back to most recent
    if reply_msg_id and reply_msg_id in confs:
        conf = confs.pop(reply_msg_id)
    elif confs:
        key = list(confs.keys())[-1]
        conf = confs.pop(key)
    else:
        _send("No pending action to confirm. Reply `/yes` directly to a confirmation message.")
        return

    action = conf["action"]

    if action == "link":
        resolve(conf["record_id"], {
            "type": "linked",
            "matched_product_name": conf["matched_product_name"],
            "matched_product_id": conf["matched_product_id"],
        })
        _send(f"✅ Linked *{conf['product_name']}* → *{conf['matched_product_name']}*.")

    elif action in ("new", "addproduct"):
        _send("Creating new Supplier Product in Seatable...")
        try:
            result = _create_supplier_product(conf, conf["ingredient_name"])
            if action == "new":
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


def _handle_no(state: Dict[str, Any], reply_msg_id: str) -> None:
    confs = state.get("pending_confirmations", {})
    if reply_msg_id and reply_msg_id in confs:
        confs.pop(reply_msg_id)
        _send("Cancelled.")
    elif confs:
        key = list(confs.keys())[-1]
        confs.pop(key)
        _send("Cancelled.")
    else:
        _send("Nothing to cancel.")


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

# Add at top of run()
import msvcrt  # Linux only — on Windows use msvcrt or a lock file
LOCK_PATH = Path(__file__).parent.parent.parent / "data" / "bot.lock"

def run():
    try:
        lock = open(LOCK_PATH, "w")
        # Windows lock file approach
        lock.write(str(os.getpid()))
        lock.flush()
    except:
        print("[LOG] Another instance running, exiting.")
        return
        
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

        # Get reply context — which bot message did the user reply to?
        reply_to = msg.get("reply_to_message") or {}
        reply_msg_id = str(reply_to.get("message_id", "")) if reply_to else ""

        print(f"[LOG] Received: {text!r} (reply_to={reply_msg_id or 'none'})")

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
            _handle_yes(state, reply_msg_id)
        elif lower in ("/no", "/cancel", "no", "cancel"):
            _handle_no(state, reply_msg_id)
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
                "`/yes` / `/no` — reply to a confirm message to act on it"
            )

    _save_state(state)
    print(f"[LOG] Processed {len(updates)} update(s). Last id: {state['last_update_id']}")


if __name__ == "__main__":
    run()
