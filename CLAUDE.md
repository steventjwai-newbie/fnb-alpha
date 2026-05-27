# Project: F&B Alpha — Cafe Operations System

## Scope
Operate only within this folder and its subfolders.
Never read or write outside this directory.

## Tech Stack
- Python 3.11+
- Seatable Python API
- Gemini API for vision/OCR
- python-telegram-bot for Telegram interface

## Critical Safety Rules
- NEVER write to Seatable's Supplier Products table without explicit /yes from Telegram
- NEVER commit secrets — they're in .gitignore
- ALWAYS log every Seatable write to Price History
- ALWAYS use structured JSON output schemas

## Workflow
- Plan mode (Shift+Tab) before executing
- Commit after every working feature
- One Telegram command = one skill = one file


# CLAUDE.md — fnb-alpha

You are working on **Alpha**, Steven's cafe AI operational system. Steven is a Penang-based F&B operator building a comprehensive system to automate invoice processing, cost tracking, recipe management, and supplier intelligence. This system will eventually become the reusable framework for a future agency business (Beta).

This file is your operating context. Read it carefully before any non-trivial work.

---

## Project goal (charter)

Build a comprehensive cafe AI operational system that runs Steven's own cafe daily, becoming the reusable framework he'll customize for Beta clients.

**In scope right now (Phase 1 — Invoice pipeline):**
- `/parse-invoice`: PDF → 4-tier matching → Seatable Price History
- `/cost-impact`: price change → affected recipes → margin alerts
- Telegram approval workflow for human-in-the-loop writes

**Out of scope (do NOT build):**
- POS systems, nutritional analysis, marketing content generation
- Multi-location support, mobile app for customers
- Recipe ingestion via photo/voice (Beta scope, not Alpha)
- Obsidian Smart Connections integration (Beta scope)
- Full agentic cross-table reasoning (Beta scope)

If a task isn't clearly in the charter, **push back before building it**. Steven explicitly asks for scope discipline.

---

## Hard rules (NEVER violate)

1. **Never write to Seatable Supplier Products or Price History without Telegram `/yes` approval.** Read-only operations are fine; writes require human confirmation through the approval workflow.
2. **No hardcoded secrets.** Always `.env` + `os.getenv()`. If you see a leaked key in code or git history, flag it immediately.
3. **No agent loops (Layer 2+) when single LLM call (Layer 1) works.** Restraint hierarchy is non-negotiable. See "Architecture" below.
4. **Never skip pagination on `base.list_rows()`.** Seatable defaults to 1000-row cap. Supplier Products has 2549+ rows. Always use the `_list_rows_paginated()` helper.
5. **Never auto-write price changes above 30% magnitude.** Even with 100% match score. Force confirm tier. This catches unit-conversion errors masquerading as price moves.
6. **Always log to Price History when updating Supplier Product price.** Both writes happen as a pair, never one without the other.
7. **All LLM data operations return structured JSON.** Never freeform text for matching decisions.
8. **Never use Seatable internal `_id` as a human-facing reference.** Use the auto-number Code columns (SP-XXXXX, ING-XXXXX) for any user-visible identifier.

---

## How Steven wants you to communicate

Steven set these as **non-negotiable** communication directives. Apply to every response.

**Directive 1 — Objective neutral analysis.** No pandering, no emotional reassurance, no validation to make him feel good. Give him the truth as you see it, even when uncomfortable.

**Directive 2 — Direct problem-finding.** Actively point out logical holes, cognitive biases, unsupported claims, and weak reasoning. Be as direct as possible. Name the strongest objection — don't manufacture objections to look critical. If he's wrong, say so and explain why.

**Additional:**
- Clean, scannable, mobile-friendly format (he reads on phone)
- Tables and bullet lists when appropriate
- No excessive caveats or apologies
- When uncertain, say so plainly — don't hedge with weasel words
- Push back when warranted, don't agree to keep him happy
- Surface tradeoffs explicitly
- Ask when unclear, don't hide confusion

**When suggesting code:**
- Minimum viable implementation first
- Point out specific bugs, not vague concerns
- If approach is wrong, say so and explain why
- Surface tradeoffs (speed vs cost, accuracy vs simplicity)

---

## Architecture invariants

### Restraint Hierarchy (mandatory)
Use the simplest layer that solves the problem:
1. **Layer 0 — Deterministic code.** Default. Fuzzy match, regex, lookup tables.
2. **Layer 1 — Single LLM call with structured JSON output.** Only when L0 is insufficient. Used for semantic matching, multilingual disambiguation.
3. **Layer 2 — Agent loop with tool use.** Only when iteration is genuinely needed.
4. **Layer 3 — Multi-agent.** Almost never.

When in doubt, go lower. The current matcher should stay at L0 + L1 only.

### Matching architecture (locked)
- **L1 — Supplier Product match.** Within scoped supplier, rapidfuzz `token_set_ratio` on Supplier Product Name. Auto-accept ≥95, confirm 80-94, show candidates 60-79, fall through <60.
- **L2 — Ingredient match (semantic, LLM, deferred).** When L1 misses, search Ingredients table globally via single Gemini Flash call with structured JSON. Not yet built.
- **Two-level decisions:** match existing SP / new SP for existing Ingredient / new Ingredient + new SP. Each has a separate Telegram approval flow.

### Unit handling (locked)
- Only **g/kg/ml/L** normalized globally via `unit_normalizer.STANDARD` dict.
- **Container units** (pack, ctn, btl, pcs, unit, biji, etc.) trust per-product Seatable Unit Quantity + UoM. When invoice has a container unit, code in `step2_compare` assumes 1 invoice container = 1 supplier pack and routes to `confirm_items` for /yes. Never auto-tier.
- **Pack Size** column = free-text descriptive note. Ignored in matching logic.
- **Per-ingredient** unit conversions in `Unit Conversions` table (per-product, not global).

### Seatable schema (relational ERP, treat it as such)
- **Ingredients** = canonical master. Recipes reference Ingredients, not SKUs.
- **Supplier Products** = SKU-level. Each links to ONE Ingredient. Multiple SPs can share the same Ingredient.
- **Price History** = audit log. Every SP price update writes a row here with old/new/change%/invoice ref/flagged_by.
- **Price cascade** (auto via Formula columns): SP.Price per Pack → Ingredient.Lowest Gross Cost → Net Cost per Base Unit (× Wastage/Yield) → Recipe Cost → Cost %.
- **Auto-number Code columns** added on Ingredients and Supplier Products. Use these as stable references, not Seatable internal `_id`.

### Three-bot Telegram architecture (locked)
- `Invoice_Receiver_Bot` — receives photos/PDFs in invoice intake group
- `invoice_parse_notification_bot` — sends parse status messages
- `seatable_update_bot` — sends price/cost notifications AND handles approval button callbacks (same bot for both because it edits its own messages)

Daemons run in separate terminals: `intake_listener.py` and `approval_handler.py`.

---

## Current file map (as of 2026-05-25)

| File | Purpose | State |
|---|---|---|
| `step1_extract.py` | PDF/image → JSON via Gemini 2.5 Flash | LIVE, working |
| `gemini_extractor.py` | Multilingual + handwriting detection | LIVE |
| `unit_normalizer.py` | Normalize g/kg/ml/L. Compute equivalent Price per Pack. | LIVE, recently rewritten |
| `step2_compare.py` | Match invoice items → Supplier Products. Categorize. | LIVE, recently rewritten |
| `notifier.py` | Send Telegram messages via seatable_update_bot | **May need integration with approval workflow — verify** |
| `approval_handler.py` | NEW. Long-polling callback handler for button presses. | DRAFTED, may not be integrated yet |
| `seatable_writer.py` | NEW. Write to Invoices, Price History, update SPs. | DRAFTED |
| `intake_listener.py` | Long-polling daemon receiving invoice photos | LIVE |
| `cost_impact.py` | `--current` and `--simulate` modes for margin alerts | LIVE (Week 3 charter), awaiting Price History writes |
| `data/parsed_results/` | Output dir for parsed invoice JSON | Active |
| `data/pending_approvals/` | NEW. JSON files for pending Telegram approvals | Created by approval_handler |

---

## Recent decisions (2026-05-25 session)

Major architectural decisions made in this session — all locked unless explicitly revisited.

1. **Pagination fix critical.** `_list_rows_paginated()` helper added. Without it only 39% of Supplier Products were loaded (1000/2549).
2. **Unit comparison fixed.** Both invoice and supplier units now parsed through STANDARD dict and compared by base unit, not literal string. `"per kg"` prefix stripped.
3. **30% magnitude safeguard.** Price changes above 30% force confirm tier regardless of match score.
4. **Container unit handling.** UNIT/CTN/PACK/BTL/BIJI etc. → assume 1 unit = 1 supplier pack, route to confirm_items.
5. **Match scores visible in data_gaps + unit_mismatches.** So wrong matches (like GREEN LOLO → Apple-Green) are diagnosable.
6. **Numbered items in Telegram message** so button callbacks reference visible indices.
7. **Audit field `Flagged By`** stores `Auto:user(id)` or `Manual:user(id)` for every Price History row.
8. **LLM matcher = Layer 1, not agent loop.** When built, single Gemini Flash call with structured JSON. No iteration.

---

## Known gaps and parked items

### Seatable data hygiene (Steven needs to fix, not code)
- LENGKUAS row missing Price per Pack
- TURMERIC POWDER row missing Price/Qty/UoM
- Kara Coconut Milk has UoM = "G" but coconut milk is liquid (should be ML)
- SENG KONG FISHERY has 0 Supplier Products linked
- PENAVON only has 4 Supplier Products in scope (verify if more should be linked)
- Mushroom SPs use UoM = G but are sold per PACK in invoices

### Parked code (do NOT build until trigger)
- Multi-candidate deviation scorer with parse_pack_multiplier (revisit if 2-week log shows ambiguity >20%)
- Telegram candidate picker UI (`/1 /2 /3`) — current buttons cover the use case
- New Ingredient / new SP creation via Telegram button — manual for now
- LLM L2 matcher for semantic gaps (CILIBOH, GREEN LOLO/coral, multilingual) — build after enough daily-use data
- Telegram text commands `/yes 1 2 3` — buttons cover this
- Recipe ingestion (handwritten/voice → Seatable) — Beta scope
- Obsidian Smart Connections — Beta scope

---

## Tasks to investigate first (Steven is away from laptop)

In priority order. Don't make destructive changes without his /yes.

### 1. Diagnose "can't approve to Seatable" (notifier integration)
Steven reports buttons don't appear or don't work. Most likely cause: `notifier.py` wasn't updated to use the new approval flow.

**Check:**
- Does `notifier.py` import `save_pending` and `build_inline_keyboard` from `approval_handler`?
- Does the send_message call include `reply_markup=keyboard`?
- Is `save_pending(payload["invoice_number"], payload)` called before send?
- Is `approval_handler.py` running in a separate terminal alongside `intake_listener.py`?
- Is `SEATABLE_UPDATE_BOT_TOKEN` in `.env`?
- Are the Seatable column names in `seatable_writer.py` matching the actual labels in his base? (`Supplier (Link)`, `Supplier product (link)`, `Flagged By`, `Date Updated`, `Processed`)
- Verify `SP_CODE_COLUMN` constant in `step2_compare.py` matches the actual auto-number column name (might be `Code`, `SP Code`, `SP-Code`, etc.)

### 2. Container_assumed items need magnitude flag too
Minor bug: in `step2_compare.py`, the `container_assumed` branch doesn't check magnitude. Should still set `magnitude_flag=True` when `diff_pct > 30`. Apply this fix:

```python
elif container_assumed:
    item_payload["container_assumed"] = True
    item_payload["candidates"] = _make_candidates(results)
    if diff_pct > PRICE_CHANGE_SANITY_CAP_PCT:
        item_payload["magnitude_flag"] = True  # ← add this
    confirm_items.append(item_payload)
```

### 3. Invoice orientation handling
Steven showed two test runs of the same Mooi Tian invoice: sideways gave parsing errors (SERAI price taken from lemon row; MUSHROOM BUTTON price from next row). Rotated worked correctly.

**Investigate `intake_listener.py`:**
- Does it use EXIF rotation? Master knowledge says yes, but the test shows it isn't catching all cases.
- Some phones save photos with `Orientation` EXIF tag set but pixel data unrotated. PIL `ImageOps.exif_transpose()` is the correct call.
- For PDFs, check if pages are being rotated based on dominant text orientation.
- Consider: run Gemini Flash on a low-res first-page thumbnail asking "is this invoice right-side-up?" before processing. Cheap, catches edge cases.

Don't ship a fix — write a diagnostic that processes a known-sideways invoice and reports what happens at each step. Show Steven the trace.

### 4. Verify the new base
Steven created a new Seatable base and shared its API token. (He's been told to rotate it.) Once rotated:
- Add `SEATABLE_API_TOKEN_TEST=...` to `.env` if it's a separate test base
- Confirm whether this new base is for testing, for a Beta client, or replacing the main one
- Don't write anything to it without his /yes

## Sandbox environment

A blank Seatable base exists for end-to-end testing. Setup script at 
`setup_sandbox.py` creates schema + seed data.

Environment switch:
- Production: standard `SEATABLE_API_TOKEN` / `SEATABLE_BASE_URL` 
- Sandbox: `SEATABLE_API_TOKEN_SANDBOX` / `SEATABLE_BASE_URL_SANDBOX`

To switch, swap the values in `.env`. No code change needed since 
all modules read via `os.getenv()`.

Sandbox testing protocol:
1. Always test new write paths against sandbox first
2. Verify Price History rows look right
3. Verify SP price updates 
4. Then promote to production by swapping .env back

Never write to production Seatable from a test session. If you're 
not sure which base you're hitting, print SEATABLE_BASE_URL at 
startup and verify.

Investigate the dtable-db big data routing issue in seatable_writer.py.
Steps:
1. Run update_sp_price() against production base on a known-safe SP 
   (pick an Active=False or unused product). Report whether it errors.
2. If production works but sandbox fails → sandbox config issue, 
   document the difference.
3. If production also fails → try the direct-HTTP workaround in 
   seatable_writer.py. Inspect base object attributes to find the 
   right URL and token. Iterate.
4. Do NOT redesign the schema (Workaround B) without Steven's /yes.
   It's a big change requiring his judgment.
   
---

## Testing protocol

Before any code change is considered done:

```bash
# 1. Re-run all 3 reference invoices, compare to last-known-good output
python step2_compare.py data/test_invoices/IV-109491.pdf       # Penavon
python step2_compare.py data/test_invoices/INV027448.pdf       # Mooi Tian
python step2_compare.py data/test_invoices/CINV-1256-0526.pdf  # Seng Kong

# 2. Check daemon stack
# Terminal 1:
python intake_listener.py

# Terminal 2:
python approval_handler.py

# 3. Send a test invoice photo to the intake Telegram group
# 4. Verify message appears in seatable_update_bot group with buttons
# 5. Click one button, verify:
#    - Price History row created
#    - Supplier Product row updated
#    - Original message edited to show action
#    - data/pending_approvals/{invoice_num}.json updated
```

---

## What to do without Steven present

**Safe to do:**
- Read files, understand code
- Diagnose problems and document findings
- Write small bug fixes (one-line, type fixes, etc.)
- Add logging/print statements for diagnosis
- Run test invoices and report results
- Document Seatable schema observations

**Requires Steven's /yes:**
- Any change to `step1_extract.py` or `step2_compare.py` core logic
- Adding/removing categories from the payload structure
- Changing column names in `seatable_writer.py`
- Adding LLM calls or model changes
- Touching the Telegram bot configuration
- Anything that writes to production Seatable

**Always:**
- If you find a bug, write it up and propose the fix, don't push it
- If multiple paths exist, present the tradeoffs
- If something looks like scope creep, say so

---

## Steven's tech stack reference

- Python 3.11+
- Seatable SDK (`seatable-api`)
- python-telegram-bot
- rapidfuzz for fuzzy matching
- Gemini 2.5 Flash via Google AI Studio (production) + $300 GCP credit (testing)
- DeepSeek V3 (overflow fallback, planned)
- Windows 11 + WSL Ubuntu 24.04, Ryzen 7 5700U, 24GB RAM
- Project root: `C:\Users\Admin\projects\fnb-alpha\`

---

## Final note

This system runs Steven's actual cafe daily. **Don't break it.** When in doubt, ask. He's available for confirmation via the message channel even when away from the laptop.

If you see a critical security issue (leaked credentials, exposed endpoint), surface it immediately and stop everything until resolved.