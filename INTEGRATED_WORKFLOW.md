# Integrated Invoice Workflow with Auto-Setup

## Overview

The system now automatically handles missing suppliers and products via interactive Telegram prompts, eliminating the need for manual API scripts.

## Flow Diagram

```
┌─ intake_listener.py (long-polling daemon)
│  └─ [1] Receives invoice image from Telegram
│  └─ [2] Extracts via Gemini (step1_extract)
│  └─ [3] Cross-checks totals (cross_check)
│  └─ [4] Compares to Supplier Products (step2_compare)
│  └─ [5] Calls notifier.notify_invoice_comparison(payload)
│
├─ notifier.py (enhanced)
│  └─ [6] Checks if supplier exists in Seatable
│  ├─ IF supplier NOT found:
│  │  ├─ [7] Save setup_state (setup_handler.py)
│  │  ├─ [8] Send Telegram: "Add Supplier?" [Add] [Skip]
│  │  └─ AWAIT user callback...
│  │
│  └─ IF supplier found:
│     └─ [9] Send approval buttons normally
│
└─ approval_handler.py (long-polling daemon, delegating)
   ├─ IF callback is approval (yes/no/skip):
   │  └─ [10] Process price change normally
   │
   └─ IF callback is setup (add_supplier/add_product/skip_setup):
      ├─ [11] Delegate to setup_handler.handle_setup_callback()
      │
      ├─ setup_handler.handle_setup_callback():
      │  ├─ [12] Add supplier to Seatable
      │  ├─ [13] Update setup_state
      │  ├─ [14] Check if product exists
      │  ├─ IF product NOT found:
      │  │  ├─ [15] Send "Add Product?" [Add] [Skip]
      │  │  └─ AWAIT user callback...
      │  │
      │  └─ IF product found or added:
      │     ├─ [16] Load original payload
      │     ├─ [17] Update with new supplier/product IDs
      │     ├─ [18] Call notify_invoice_comparison(payload)
      │     └─ [19] Send approval buttons
      │
      └─ User approves/rejects items
         └─ [20] Write to Price History, SP price, Invoices
```

## Running the System

### Terminal 1: Invoice Intake Daemon

```bash
python src/skills/invoice_intake/intake_listener.py
```

Monitors Telegram for incoming invoice photos/PDFs.

### Terminal 2: Approval & Setup Handler

```bash
python src/skills/parse_invoice/approval_handler.py
```

Handles both:
- **Setup callbacks** (add_supplier, add_product) → delegates to setup_handler
- **Approval callbacks** (yes/no/skip) → processes price changes normally

## Scenario Walkthrough

### Scenario 1: New Supplier + Existing Product

**User sends:** Seng Kong Fishery invoice with Salmon product

**Step 1-5:** intake_listener extracts and compares
```json
{
  "supplier_matched": false,
  "supplier_name": "SENG KONG FISHERY SDN BHD",
  "price_changes": []
}
```

**Step 6-8:** notifier detects missing supplier
```
Telegram message:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Setup Required — CINV-1256-0526
Supplier: `SENG KONG FISHERY SDN BHD`
Not found in Seatable.

Add supplier and continue?
[Add] [Skip]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**User clicks:** [Add]

**Step 11-16:** approval_handler → setup_handler
- Creates supplier in Seatable
- Updates setup_state
- Checks for product "FROZEN SMOKED SALMON TROUT"
- **Found!** Product exists (linked to Pok Brothers)
- Sets `setup_complete = True`

**Step 17-19:** Loads payload, updates supplier_row_id, sends approval buttons
```
Telegram message:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CINV-1256-0526 | SENG KONG FISHERY SDN BHD
08-May-2026

[1] Salmon Trout Fillet
    RM0.00 → RM74.00 (+∞%) [match]

[✓ 1] [✗ 1] [⏭ 1]
[✓ Approve All] [⏭ Skip]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**User clicks:** [✓ Approve All]

**Step 20:** Writes to Seatable:
- ✓ Invoices row created
- ✓ Price History row logged
- ✓ SP price updated to RM74.00

---

### Scenario 2: New Supplier + New Product

**User sends:** Mooi Tian invoice with unknown product

**Step 1-8:** intake_listener → notifier detects missing supplier (same as above)

**User clicks:** [Add]

**Step 11-14:** setup_handler
- Creates supplier ✓
- Checks for product "XYZ PRODUCT"
- **Not found!** Product doesn't exist

**Step 15:** Sends product setup prompt
```
Telegram message:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Setup Required — CINV-123-456
Product: `XYZ PRODUCT`
Supplier: MOOI TIAN TRADING (just added)
Not found in Seatable.

Add product and continue?
[Add] [Skip]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**User clicks:** [Add]

**Step 12-13:** setup_handler creates product
- Creates in Supplier Products
- Links to Mooi Tian
- Sets `setup_complete = True`

**Step 17-19:** Loads payload, sends approval buttons as normal

---

## Implementation Details

### setup_handler.py

New module with:

```python
handle_setup_callback()      # Main handler for setup callbacks
supplier_exists()            # Check if supplier in Seatable
product_exists()             # Check if product exists for supplier
save_setup_state()           # Save state between Telegram exchanges
load_setup_state()           # Load for resuming workflow
delete_setup_state()         # Clean up on completion
```

### notifier.py (Enhanced)

`notify_invoice_comparison()` now:
1. Checks supplier with `supplier_exists()`
2. If missing, sends setup prompt via Telegram
3. If found, proceeds with approval buttons as before

### approval_handler.py (Enhanced)

`handle_callback()` now:
1. Parses callback data
2. Detects setup vs approval callbacks
3. Delegates setup → `setup_handler.handle_setup_callback()`
4. Handles approval normally

## Data Files

### Setup State

Location: `data/setup_state/{invoice_number}_setup.json`

```json
{
  "invoice_number": "CINV-1256-0526",
  "supplier_name": "SENG KONG FISHERY SDN BHD",
  "product_name": "FROZEN SMOKED SALMON TROUT",
  "supplier_row_id": "emagV9InSPSwKD6F6JAZTw",
  "supplier_added": true,
  "product_row_id": "I3B4s9GdSfOjziy0pcmxWA",
  "product_added": false,
  "setup_complete": true
}
```

### Pending Approvals

Location: `data/pending_approvals/{invoice_number}.json` (unchanged)

Saved after setup complete, loaded for approval buttons.

## Error Handling

- **Supplier creation fails:** Shows error, allows user to [Skip]
- **Product creation fails:** Shows error, allows user to [Skip]
- **Setup state corrupt:** Shows error, requires invoice re-sent
- **Payload missing:** Shows error, setup cancelled

## Testing

### Test 1: New Supplier (Existing Product)

```bash
# Send Seng Kong Fishery invoice
# Expected: Setup prompt → Add → Approval buttons
```

### Test 2: New Supplier + New Product

```bash
# Send invoice with non-existent supplier AND product
# Expected: Setup prompt → Add supplier → Setup prompt → Add product → Approval buttons
```

### Test 3: Skip Setup

```bash
# Send new supplier invoice → Click [Skip]
# Expected: Invoice marked for manual review
```

## Benefits

| Before | After |
|--------|-------|
| Extract → Compare → Silent fail | Extract → Compare → Interactive setup |
| Manual API script to add supplier | [Add] button in Telegram |
| Manual testing required | Real operational flow |
| Data gaps require investigation | Clear prompts guide user |

## Future Enhancements

- [ ] Product candidate picker (if match score 60-80%)
- [ ] Ingredient → Product linking during setup
- [ ] Bulk supplier import via CSV
- [ ] Setup confirmation with preview before writing
