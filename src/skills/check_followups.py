"""
Check and send due follow-up notifications for invoices with handwriting.
Run daily via Windows Task Scheduler, or trigger via /checkfollowups Telegram command.

Task Scheduler command:
  python C:\Users\Admin\projects\fnb-alpha\src\skills\check_followups.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "parse_invoice"))

from parse_invoice.followup_store import get_due_followups, mark_followup_sent, get_all_pending
from parse_invoice.notifier import notify_followup_due


def check_and_notify():
    due = get_due_followups()

    if not due:
        print("[LOG] No follow-ups due.")
        return 0

    print(f"[LOG] {len(due)} follow-up(s) due.")
    sent = 0
    for record in due:
        success = notify_followup_due(record)
        if success:
            mark_followup_sent(record["id"])
            sent += 1
        else:
            print(f"[LOG] Failed to send follow-up for invoice {record.get('invoice_number')}")

    return sent


def print_pending():
    pending = get_all_pending()
    if not pending:
        print("No pending follow-ups.")
        return
    print(f"{len(pending)} pending follow-up(s):\n")
    for r in pending:
        due_date = r.get("followup_due", "")[:10]
        print(f"  [{due_date}] {r.get('invoice_number')} — {r.get('supplier_name')}")
        print(f"           Handwriting: {r.get('handwriting_content')}")


if __name__ == "__main__":
    if "--list" in sys.argv:
        print_pending()
    else:
        sent = check_and_notify()
        print(f"[LOG] Done. {sent} follow-up(s) sent.")
