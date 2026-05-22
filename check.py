import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

data = json.loads(
    Path("data/pending_matches.json").read_text(encoding="utf-8")
)

rec = next(r for r in data if r["id"] == "56744ed7")

print("Product:", rec["product_name"])
print("Candidates:")

for i, c in enumerate(rec.get("candidates", []), 1):
    print(f"  {i}. {c['name']} (id={c['id']}, score={c['score']})")