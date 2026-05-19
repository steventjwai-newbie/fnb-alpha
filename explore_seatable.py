"""
Explore Seatable using the seatable_api library with fnb_alpha token.
"""
import os
import sys
from dotenv import load_dotenv
from seatable_api import SeaTableAPI

# Fix unicode output on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

load_dotenv()

SEATABLE_URL = os.getenv("SEATABLE_BASE_URL")
API_TOKEN = os.getenv("SEATABLE_API_TOKEN")
BASE_UUID = "8dc4ec23-0ee3-4260-bb09-cc2cae6c21d6"

print(f"Using token: {API_TOKEN[:20]}...")
print(f"Base UUID: {BASE_UUID}")

# Initialize and authenticate
api = SeaTableAPI(token=API_TOKEN, server_url=SEATABLE_URL)
print("\nAuthenticating...")
api.auth()

# List tables
print("\n=== Tables in Base ===")
metadata = api.get_metadata()
for table in metadata['tables']:
    print(f"- {table['name']} (id: {table['_id']})")

# Read 1 recipe
print("\n=== First Recipe ===")
rows = api.list_rows("Recipe List (Full)", limit=1)
if rows:
    recipe = rows[0]
    for key, value in recipe.items():
        # Handle unicode characters safely
        value_str = str(value).encode('utf-8', errors='replace').decode('utf-8')
        print(f"  {key}: {value_str}")
else:
    print("No recipes found")
