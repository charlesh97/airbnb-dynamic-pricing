# Quick test - fetch iGMS calendar + run pricing engine

import sys
sys.path.insert(0, "src")

from pathlib import Path
from pricing_engine.config import EngineConfig
from pricing_engine.client import PricingClient
from pricing_engine.engine import PricingEngine
from pricing_engine.config_store import PropertyConfigStore

REPO_ROOT = Path(__file__).parent
env_path = REPO_ROOT / ".env"
config = EngineConfig.from_env(env_path)

print("=== Config ===")
token_display = config.igms_access_token[:20] + "..." if config.igms_access_token else "MISSING"
print(f"IGMS_ACCESS_TOKEN: {token_display}")
print(f"IGMS_CLIENT_ID: {config.igms_client_id}")
print(f"IGMS_SCOPE: {config.igms_scope}")

if not config.igms_access_token:
    print("\nERROR: IGMS_ACCESS_TOKEN is missing from .env")
    print("Add it to .env and re-run.")
    sys.exit(1)

client = PricingClient()
client.set_access_token(config.igms_access_token)

uid = "645841896772032198"
print(f"\n=== Fetching calendar for {uid} ===")
try:
    cal = client.get_calendar(
        property_uid=uid,
        from_date="2026-05-01",
        to_date="2026-05-31",
    )
    entries = cal if isinstance(cal, list) else cal.get("data", [])
    print(f"Got {len(entries)} calendar entries")
    for e in entries[:5]:
        print(f"  {e.get('date')}: ${e.get('price')}")
except Exception as e:
    print(f"ERROR fetching calendar: {e}")
    sys.exit(1)

store = PropertyConfigStore()
prop_config = store.load(uid)
print(f"\n=== Property config ===")
print(f"  name: {prop_config.get('name', 'N/A')}")
print(f"  base_price: {prop_config.get('base_price', 'N/A')}")
print(f"  min_price: {prop_config.get('min_price', 'N/A')}")
print(f"  max_price: {prop_config.get('max_price', 'N/A')}")
print(f"  seasonal_months: {prop_config.get('seasonal_months', 'N/A')}")

engine = PricingEngine()
print(f"\n=== Pricing engine output ===")
for day in range(1, 32):
    date = f"2026-05-{day:02d}"
    entry = next((e for e in entries if e.get("date") == date), None)
    dp = engine.compute_price(
        property_uid=uid,
        date=date,
        calendar_entry=entry,
        bookings_in_window=[],
        config={**prop_config, **config.__dict__},
    )
    current = entry.get("price") if entry else None
    print(f"  {date}: recommended=${dp.final_price:7.2f}  current=${str(current):>8}  confidence={dp.confidence:.2f}")
