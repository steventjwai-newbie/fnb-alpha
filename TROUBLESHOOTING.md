# Troubleshooting: Invoice No Response

## Quick Checklist

### 1. Are both daemons still running?

**Terminal 1 should show:**
```
[LOG] Invoice intake daemon started. Polling...
```

**Terminal 2 should show:**
```
[approval_handler] Listening on bot token ending …[6 chars]
```

If either stopped, restart it.

---

## Diagnostic Steps (Run When Back)

### Step 1: Run the intake diagnostic

```bash
python diagnose_intake.py 2>&1 | tee diagnostic_output.txt
```

This will show:
- ✓ If file was downloaded from Telegram
- ✓ If Gemini extraction worked
- ✓ If comparison generated payload
- ✓ If notifier would send message

**Share the output when stuck.**

---

### Step 2: Check daemon logs

**If you restarted daemons, capture fresh logs:**

Terminal 1:
```bash
python src/skills/invoice_intake/intake_listener.py 2>&1 | tee intake.log
# Send invoice
# Wait 10 seconds
# Ctrl+C to stop
# Share intake.log
```

Terminal 2:
```bash
python src/skills/parse_invoice/approval_handler.py 2>&1 | tee approval.log
# Wait for messages
# Ctrl+C to stop
# Share approval.log
```

---

### Step 3: Check file system

```bash
# Did file download?
ls -lah data/invoices_inbox/2026-05-28/

# Was state saved?
ls -lah data/setup_state/

# Was payload saved?
ls -lah data/pending_approvals/
```

---

## Common Failure Points & Fixes

### ❌ "No response from bot"

**Most likely:** intake_listener crashed or not receiving messages

**Check:**
- Is Terminal 1 still showing "Polling..."?
- Did you send to the right Telegram group?
- Are you in the invoice intake group? (`INVOICE_GROUP_CHAT_ID = -1003900127445`)

**Fix:**
```bash
# Restart intake_listener
python src/skills/invoice_intake/intake_listener.py
```

---

### ❌ "Parse success but no approval buttons"

**Likely cause:** Supplier check failed, but setup prompt didn't send

**Check logs for:**
```
[LOG] Supplier 'SENG KONG FISHERY...' not found
```

If this line appears but no Telegram message → notifier crashed

**Check Terminal 2 for errors** when you restart

---

### ❌ "Setup Required prompt appeared but [Add] does nothing"

**Likely cause:** setup_handler crashed or missing imports

**Check Terminal 2 logs for:**
```
[ERROR] ... in setup_handler
```

**Fix: Check imports in setup_handler.py**
```bash
python -c "from setup_handler import handle_setup_callback; print('OK')"
```

If that fails, there's an import error.

---

### ❌ "Supplier created but product check skipped"

**Likely cause:** Product name extraction failed (items list was empty)

**Fix:** Already applied in notifier.py (line 160-165)
- If issue persists, check if payload structure changed

---

## Data Flow Checklist

After sending invoice, check these in order:

```
[1] Telegram message sent?
    ↓
[2] File in data/invoices_inbox/2026-05-28/?
    ↓ (If yes, continue. If no, intake_listener not receiving)
    ↓
[3] Run: python diagnose_intake.py
    ├─ Extraction OK?
    │  ├─ Yes → Continue
    │  └─ No → Gemini error, check .env GEMINI_API_KEY
    ├─ Comparison OK?
    │  ├─ Yes → Continue
    │  └─ No → step2_compare error, check Terminal 1
    └─ Notifier OK?
       ├─ Yes → Message should have sent. Check if it appeared in Telegram (delayed?)
       └─ No → Error in notifier.py, check Terminal 1
    ↓
[4] Setup state saved?
    └─ File at: data/setup_state/CINV-1256-0526_setup.json
    ↓
[5] Telegram message appeared?
    ├─ Yes → Click [Add]
    │  ├─ Setup handler processed?
    │  │  └─ Check data/pending_approvals/CINV-1256-0526.json exists
    │  └─ Approval buttons sent?
    └─ No → Notifier sent to wrong chat, or _send_seatable() failed
```

---

## When You're Back

1. **Kill both daemons** (Ctrl+C)
2. **Run diagnostic:**
   ```bash
   python diagnose_intake.py 2>&1
   ```
3. **Paste output**
4. I'll tell you exactly what's wrong

---

## Emergency: Start from scratch

If everything is broken:

```bash
# Clean up state
rm -rf data/setup_state/*.json
rm -rf data/pending_approvals/*.json

# Restart daemons
python src/skills/invoice_intake/intake_listener.py &
python src/skills/parse_invoice/approval_handler.py &

# Send invoice again
```

---

## Questions to Answer When Diagnosing

1. **Did you see any message in the Seatable bot Telegram chat?**
   - Yes / No
   - If yes, what did it say?

2. **What do the Terminal logs show?**
   - Terminal 1: [paste last 20 lines]
   - Terminal 2: [paste last 20 lines]

3. **Did the file download?**
   - Run: `ls -lah data/invoices_inbox/2026-05-28/`
   - Paste output

4. **Did you already add Seng Kong Fishery via the [Add] button, or no response at all?**
   - First time sending this invoice
   - Already sent, got setup prompt, clicked [Add] but nothing happened

---

## TL;DR

When back at laptop:

```bash
# 1. Run diagnostic
python diagnose_intake.py 2>&1 | tee output.txt

# 2. Paste output here
# 3. I'll fix it
```
