"""End-to-end test: simulate every Telegram callback for the salmon invoice.

Tests:
  T1: setup state + pending payload created from supplier-not-matched invoice
  T2: add_supplier callback creates supplier in Seatable
  T3: add_product callback creates SP linked to supplier
  T4: link_ingredient callback links SP to ingredient
  T5: approval payload sent to Telegram
  T6: yes:all callback writes Price History + updates SP price
  T7: Invoice marked Processed in Seatable
"""
import asyncio, sys, json, os, glob
from pathlib import Path

sys.path.insert(0, 'src/skills/parse_invoice')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

INVOICE_NUM = "CINV-1256-0526"

# ────── Mock telegram objects ──────
class FakeUser:
    username = "test_runner"
    first_name = "Test"
    id = 999

class FakeMessage:
    def __init__(self, text="", reply_markup=None):
        self.text = text
        self.reply_markup = reply_markup

class FakeQuery:
    def __init__(self, data, message_text="", reply_markup=None):
        self.data = data
        self.from_user = FakeUser()
        self.message = FakeMessage(message_text, reply_markup)
        self.edited = []
    async def answer(self, *args, **kwargs):
        pass
    async def edit_message_text(self, text, reply_markup=None):
        self.edited.append({"text": text, "reply_markup": reply_markup})
        snippet = text.replace("\n", " | ")[:120]
        print(f"  [EDITED MSG] {snippet}")

class FakeUpdate:
    def __init__(self, query):
        self.callback_query = query

async def run_setup(data, msg_text="", markup=None):
    q = FakeQuery(data, msg_text, markup)
    from setup_handler import handle_setup_callback
    await handle_setup_callback(FakeUpdate(q), None)
    return q

async def run_approval(data, msg_text="", markup=None):
    q = FakeQuery(data, msg_text, markup)
    from approval_handler import handle_callback
    await handle_callback(FakeUpdate(q), None)
    return q

# ────── Helpers ──────
def get_base():
    from seatable_writer import _base
    return _base()

def find_supplier(name_match: str):
    base = get_base()
    for r in base.list_rows("Suppliers"):
        if (r.get("Supplier Name") or "").strip() == name_match:
            return r
    return None

def find_sp_by_name(name_match: str):
    base = get_base()
    start, page = 0, 1000
    while True:
        batch = base.list_rows("Supplier Products", start=start, limit=page)
        if not batch:
            break
        for r in batch:
            if (r.get("Supplier Product Name") or "").strip() == name_match:
                return r
        if len(batch) < page:
            break
        start += page
    return None

def get_sp_by_id(row_id):
    base = get_base()
    return base.get_row("Supplier Products", row_id)

def find_invoice(invoice_num):
    base = get_base()
    for r in base.list_rows("Invoices"):
        if (r.get("Invoice Number") or "").strip() == invoice_num.strip():
            return r
    return None

def count_price_history_for_sp(sp_row_id):
    base = get_base()
    n = 0
    for r in base.list_rows("Price History"):
        links = r.get("Supplier product (link)") or []
        for link in links:
            link_id = link.get("row_id") if isinstance(link, dict) else link
            if link_id == sp_row_id:
                n += 1
                break
    return n

def seatable_cleanup():
    """Delete all Seatable rows created by this test. Scoped to SENG KONG FISHERY / CINV-1256-0526 only."""
    base = get_base()
    print("[CLEANUP] Starting Seatable cleanup...")

    supplier = find_supplier("SENG KONG FISHERY SDN BHD")
    if not supplier:
        print("[CLEANUP] Supplier not found, skipping.")
        return
    supplier_row_id = supplier["_id"]

    # Find SPs linked to this supplier
    sp_row_ids = []
    start, page = 0, 1000
    while True:
        batch = base.list_rows("Supplier Products", start=start, limit=page)
        if not batch:
            break
        for row in batch:
            links = row.get("Supplier") or []
            link_ids = [l.get("row_id") if isinstance(l, dict) else l for l in links]
            if supplier_row_id in link_ids:
                sp_row_ids.append(row["_id"])
        if len(batch) < page:
            break
        start += page
    print(f"[CLEANUP] Found {len(sp_row_ids)} SP row(s).")

    # Collect ingredient IDs linked to these SPs before deleting
    ingredient_ids = []
    for sp_id in sp_row_ids:
        sp_row = base.get_row("Supplier Products", sp_id)
        if sp_row:
            for l in (sp_row.get("Ingredients") or []):
                ing_id = l.get("row_id") if isinstance(l, dict) else l
                if ing_id:
                    ingredient_ids.append(ing_id)

    # Delete Price History rows linked to these SPs
    for row in base.list_rows("Price History"):
        links = row.get("Supplier product (link)") or []
        link_ids = [l.get("row_id") if isinstance(l, dict) else l for l in links]
        if any(sp_id in link_ids for sp_id in sp_row_ids):
            base.delete_row("Price History", row["_id"])
            print(f"[CLEANUP] Deleted Price History {row['_id']}")

    # Delete SP rows
    for sp_id in sp_row_ids:
        base.delete_row("Supplier Products", sp_id)
        print(f"[CLEANUP] Deleted SP {sp_id}")

    # Delete Invoice row
    inv = find_invoice(INVOICE_NUM)
    if inv:
        base.delete_row("Invoices", inv["_id"])
        print(f"[CLEANUP] Deleted Invoice {inv['_id']}")

    # Delete supplier
    base.delete_row("Suppliers", supplier_row_id)
    print(f"[CLEANUP] Deleted Supplier {supplier_row_id}")

    # Delete test-created ingredients (those with no remaining SP links after SP deletion)
    import time; time.sleep(1)  # brief pause for Seatable to reflect deletions
    for ing_id in ingredient_ids:
        try:
            ing_row = base.get_row("Ingredients", ing_id)
            if not ing_row:
                continue
            # Check reverse link column (Ingredients → SP)
            has_other_links = bool(ing_row.get("Link to Supplier Product"))
            if not has_other_links:
                base.delete_row("Ingredients", ing_id)
                print(f"[CLEANUP] Deleted Ingredient {ing_id}")
            else:
                print(f"[CLEANUP] Kept Ingredient {ing_id} (has other SP links)")
        except Exception as e:
            print(f"[CLEANUP] Could not check/delete Ingredient {ing_id}: {e}")

    print("[CLEANUP] Done.")

# ────── Test runner ──────
results = []
def check(name, condition, detail=""):
    results.append({"name": name, "pass": condition, "detail": detail})
    icon = "PASS" if condition else "FAIL"
    print(f"  [{icon}] {name}" + (f" — {detail}" if detail else ""))

async def main():
    print("=" * 70)
    print("E2E TEST: Setup flow for CINV-1256-0526 (Seng Kong Fishery)")
    print("=" * 70)

    # Pre-clean local files
    for d in ["pending_approvals", "setup_state"]:
        p = Path(f"data/{d}/{INVOICE_NUM}.json" if d == "pending_approvals" else f"data/{d}/{INVOICE_NUM}_setup.json")
        if p.exists():
            p.unlink()
            print(f"[CLEAN] deleted {p}")

    # Auto-clean Seatable test rows (scoped to this supplier only)
    seatable_cleanup()

    # ── T1: Trigger setup prompt ──
    print("\n── T1: Build comparison, trigger setup prompt ──")
    import step2_compare, notifier
    step2_compare.clear_caches()

    files = sorted(glob.glob('data/parsed_results/*/CINV-1256-0526_*.json'),
                   key=os.path.getmtime, reverse=True)
    with open(files[0], encoding='utf-8') as f:
        step1 = json.load(f)

    source_files = step1.get('source_files', [])
    invoice_file_path = (source_files[0]['path'] if source_files and isinstance(source_files[0], dict)
                        else (source_files[0] if source_files else ''))

    payloads = step2_compare.build_comparison(step1)
    for p in payloads:
        p['invoice_file_path'] = invoice_file_path
        notifier.notify_invoice_comparison(p)

    state_path = Path(f"data/setup_state/{INVOICE_NUM}_setup.json")
    pending_path = Path(f"data/pending_approvals/{INVOICE_NUM}.json")
    check("T1.1 setup state file created", state_path.exists())
    check("T1.2 pending payload created", pending_path.exists())

    with open(state_path, encoding="utf-8") as f:
        state = json.load(f)
    check("T1.3 setup_step=supplier", state["setup_step"] == "supplier",
          f"setup_step={state['setup_step']}")
    check("T1.4 supplier_added=False", state["supplier_added"] is False)
    check("T1.5 1 item with salmon trout", len(state["items"]) == 1
          and "SALMON TROUT" in state["items"][0]["product_name"])

    # ── T2: Click [Add Supplier] ──
    print("\n── T2: Simulate add_supplier callback ──")
    await run_setup(f"add_supplier:{INVOICE_NUM}")

    supplier = find_supplier("SENG KONG FISHERY SDN BHD")
    check("T2.1 supplier created in Seatable", supplier is not None,
          f"row_id={supplier['_id'] if supplier else 'NONE'}")

    with open(state_path, encoding="utf-8") as f:
        state = json.load(f)
    check("T2.2 supplier_added=True in state", state["supplier_added"] is True)
    check("T2.3 supplier_row_id stored", bool(state["supplier_row_id"]))
    check("T2.4 supplier_row_id matches Seatable", supplier and state["supplier_row_id"] == supplier["_id"])

    supplier_row_id = state["supplier_row_id"]

    # ── T3: Click [Add Product] ──
    print("\n── T3: Simulate add_product callback ──")
    await run_setup(f"add_product:{INVOICE_NUM}")

    with open(state_path, encoding="utf-8") as f:
        state = json.load(f)
    product_row_id = state["items"][0].get("product_row_id")
    check("T3.1 product_row_id stored in state", bool(product_row_id))

    if product_row_id:
        sp_row = get_sp_by_id(product_row_id)
        check("T3.2 SP exists in Seatable", sp_row is not None)
        if sp_row:
            check("T3.3 SP name correct",
                  "SALMON TROUT" in (sp_row.get("Supplier Product Name") or ""))
            sup_links = sp_row.get("Supplier") or []
            sup_link_ids = [s.get("row_id") if isinstance(s, dict) else s for s in sup_links]
            check("T3.4 SP linked to supplier",
                  supplier_row_id in sup_link_ids,
                  f"links={sup_link_ids}")

    # ── T4: LLM ingredient match and link/create ──
    print("\n── T4: LLM ingredient match and simulate callback ──")
    from setup_handler import _search_ingredients, _llm_match_ingredient
    base = get_base()
    product_name = state["items"][0]["product_name"]
    candidates = _search_ingredients(base, product_name)
    llm_result = _llm_match_ingredient(product_name, candidates)
    match_id = llm_result.get("match_row_id")
    suggested = llm_result.get("suggested_name") or product_name
    check("T4.1 LLM ingredient call succeeded", bool(suggested),
          f"match={llm_result.get('match_name')} suggested={suggested}")

    if match_id:
        await run_setup(f"link_ingredient:{INVOICE_NUM}:{match_id}")
        sp_row = get_sp_by_id(product_row_id)
        ing_links = sp_row.get("Ingredients") or []
        ing_link_ids = [i.get("row_id") if isinstance(i, dict) else i for i in ing_links]
        check("T4.2 SP linked to existing ingredient", match_id in ing_link_ids,
              f"links={ing_link_ids}")
    else:
        await run_setup(f"create_ingredient:{INVOICE_NUM}:{suggested}")
        sp_row = get_sp_by_id(product_row_id)
        ing_links = sp_row.get("Ingredients") or []
        check("T4.2 SP linked to newly created ingredient", len(ing_links) >= 1,
              f"links={ing_links}")

    with open(state_path, encoding="utf-8") as f:
        state = json.load(f)
    check("T4.3 setup_complete=True", state.get("setup_complete") is True)

    # ── T4.4-T4.5: Verify create_ingredient mechanics directly ──
    print("\n── T4.4: Verify create_ingredient mechanics (append_row + link) ──")
    base2 = get_base()
    test_ing_name = "_E2E_TestIng_"
    new_ing = base2.append_row("Ingredients", {"Ingredient Name": test_ing_name})
    check("T4.4 append_row Ingredients returns row with _id",
          bool(new_ing and isinstance(new_ing, dict) and new_ing.get("_id")),
          f"result={repr(new_ing) if new_ing else 'None'}")

    if new_ing and new_ing.get("_id"):
        from seatable_writer import add_row_link
        ok = add_row_link(
            base=base2,
            link_column_table="Supplier Products",
            link_column_name="Ingredients",
            link_column_row_id=product_row_id,
            target_table="Ingredients",
            target_row_id=new_ing["_id"],
        )
        check("T4.5 SP→Ingredient link created", ok)
        base2.delete_row("Ingredients", new_ing["_id"])
        print(f"  [CLEAN] deleted test ingredient {new_ing['_id']}")

    # ── T5: Approval payload sent ──
    print("\n── T5: Verify approval payload was written ──")
    # After setup completes, the pending file should have been overwritten with
    # the new approval payload (supplier_matched=True, confirm_items populated)
    with open(pending_path, encoding="utf-8") as f:
        approval_payload = json.load(f)
    check("T5.1 pending payload has supplier_matched=True",
          approval_payload.get("supplier_matched") is True)
    check("T5.2 pending payload has 1 confirm_item",
          len(approval_payload.get("confirm_items", [])) == 1)
    if approval_payload.get("confirm_items"):
        ci = approval_payload["confirm_items"][0]
        check("T5.3 confirm_item has SP row id", ci.get("sp_row_id") == product_row_id)
        check("T5.4 confirm_item has price 74.0", abs(ci.get("new_price", 0) - 74.0) < 0.01)

    # ── T6: Click [Approve All] ──
    print("\n── T6: Simulate approval yes:all callback ──")
    # Need to reload pending payload for approval handler to find items
    history_before = count_price_history_for_sp(product_row_id)

    # Mock message text for approval handler (it uses query.message.text)
    fake_text = f"📋 {INVOICE_NUM} | SENG KONG | 2026-05-08\n\n[1] FROZEN SMOKED SALMON\nRM0.00 → RM74.00"
    await run_approval(f"yes:{INVOICE_NUM}:all", msg_text=fake_text)

    history_after = count_price_history_for_sp(product_row_id)
    check("T6.1 Price History row created",
          history_after == history_before + 1,
          f"before={history_before}, after={history_after}")

    sp_row = get_sp_by_id(product_row_id)
    new_price = sp_row.get("Price per Pack")
    check("T6.2 SP price updated to 74.0",
          new_price is not None and abs(float(new_price) - 74.0) < 0.01,
          f"Price per Pack={new_price}")

    # ── T7: Invoice marked Processed ──
    print("\n── T7: Verify Invoice marked Processed ──")
    inv = find_invoice(INVOICE_NUM)
    check("T7.1 Invoices row exists", inv is not None)
    if inv:
        check("T7.2 Invoices row Processed=True",
              inv.get("Processed") is True,
              f"Processed={inv.get('Processed')}")

    # ── Summary ──
    print("\n" + "=" * 70)
    passed = sum(1 for r in results if r["pass"])
    failed = sum(1 for r in results if not r["pass"])
    print(f"RESULTS: {passed} passed, {failed} failed (out of {len(results)})")
    print("=" * 70)
    for r in results:
        icon = "PASS" if r["pass"] else "FAIL"
        print(f"  [{icon}] {r['name']}" + (f" — {r['detail']}" if r["detail"] and not r["pass"] else ""))

    if failed:
        sys.exit(1)

asyncio.run(main())
