# Clear any pending updates Telegram has queued
import requests, os
from dotenv import load_dotenv
load_dotenv()
token = os.getenv("TELEGRAM_BOT_TOKEN")
# Force-close any active getUpdates connection
requests.get(f"https://api.telegram.org/bot{token}/getUpdates?offset=-1&limit=1")
print("Reset done")