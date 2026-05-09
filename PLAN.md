# Dynamic Pricing v2 — Implementation Plan

## Context

The tool lives at `/Users/charlesclaw/Documents/git/airbnb-dynamic-pricing/`.
Existing strategies: `DemandStrategy`, `EventStrategy`, `YieldStrategy`, `CompetitorStrategy`
registered in `PricingEngine.__init__`. Weights are normalized in `compute_price()`.

Target property: `731418607849470882` (Frosty Pines Cabin).

---

## Feature 1: WeatherStrategy

### Where to add it
**New file:** `src/pricing_engine/strategies/weather.py`

Pattern matches existing strategies (`DemandStrategy`, etc.):
- Subclass `PricingStrategy`
- Implement `compute()` returning `PriceRecommendation`
- Read `base_prices`, `min_price`/`max_price` via inherited helpers

### API key configuration
- Environment variable: `OPENWEATHERMAP_API_KEY`
- `.env` key: `OPENWEATHERMAP_API_KEY`
- Access via `config.get("openweathermap_api_key")` — the merged config from `EngineConfig.from_env()` already includes env vars
- If key is absent or empty: log a warning and return multiplier **1.0** (confidence 0.0)

### API call
- Endpoint: `https://api.openweathermap.org/data/2.5/forecast`
- Params: `lat`, `lon`, `appid`, `units=imperial`
- Latitude/longitude from property JSON config (already present in `731418607849470882.json` as `latitude`/`longitude`)
- Parse the 5-day/3h forecast; look at `dt_txt` timestamps for next 48 hours
- Identify snow conditions using:
  - `snow` key present in API response → check `snow["3h"]` values
  - Weather condition codes: 2xx (thunderstorm), 5xx (rain/drizzle) + temp < 32°F for mixed precipitation
  - Alert presence via `alerts` key in response

### Multiplier logic
| Condition | Factor | Confidence |
|---|---|---|
| No snow within 48h | 1.00 | 0.70 |
| Snow within 24h | 1.08 | 0.80 |
| Active snowstorm / blizzard warning | 1.15 | 0.85 |

### Graceful failure
- Any API error (timeout, 401, network) → fall back to factor **1.0**, confidence **0.0**
- API response missing `snow` key and no temp < 32°F alerts → factor **1.0**
- Cache API responses in memory for the duration of a single `compute_range()` call (dict keyed by property_uid) to avoid N identical calls for N days

### Registration
- Add `from .weather import WeatherStrategy` to `strategies/__init__.py`
- Add `WeatherStrategy()` to `PricingEngine.__init__` strategy list
- **Initial weight: 0.00** (disabled by default to preserve existing behaviour)
- Charles can enable it by setting `strategy_weights.weather = 0.10` in property JSON

---

## Feature 2: Local Events (extend property JSON)

### Schema change (additions to `config/properties/{uid}.json`)
```json
{
  "local_events": [
    {
      "name": "Soda Springs Ski Day",
      "date": "2026-05-15",
      "factor": 1.10
    }
  ],
  "local_events_config": {
    "default_factor": 1.10
  }
}
```
- `local_events` is a list of per-date event objects
- `date` must be ISO format `YYYY-MM-DD`
- Factor applies on top of the existing seasonal multiplier
- V1 is manually curated (no Eventbrite API integration)

### Integration with EventStrategy
**No new strategy needed.** Modify `EventStrategy.compute()`:
- After computing the seasonal/DOW multiplier, check `config.get("local_events", [])`
- Iterate and match `event["date"] == date`; multiply by `event.get("factor", config.get("local_events_config", {}).get("default_factor", 1.10))`
- Add `local_event_applied: str` to `factors` dict for transparency

Also add a flat lookup table for date→factor for O(1) access:
```python
_local_events_map: dict[str, float] = {e["date"]: e["factor"] for e in local_events}
```

---

## Feature 3: CSV Export

### CLI flag: `--export-csv`
- Add to `run-config` subparser
- When provided: write CSV to `frosty-pines-pricing-{from_date}.csv` (derived from `from_date` arg)

### CSV columns
`date, day_of_week, base_rate, demand_multiplier, event_factor, last_minute_factor, weather_factor, adjusted_rate`

Extracted from `DatePrice` and its `all_factors` sub-dict:
- `base_rate` → `all_factors["demand"]["base_price"]` (or from config fallback)
- `demand_multiplier` → derive from `final_price / (base_rate × other_factors)` OR store explicitly in `all_factors`
  - **Decision:** Store explicit multiplier keys in each strategy's `factors` dict so CSV export can read them directly
  - Add `demand_factor`, `yield_factor`, `competitor_factor` keys to each strategy's factors output (existing EventStrategy already has `seasonal_multiplier`)
- `event_factor` → `all_factors["event"]["seasonal_multiplier"] × local_events_multiplier`
- `last_minute_factor` → `all_factors["demand"]["demand_multiplier"]` (demand strategy already computes it)
- `weather_factor` → `all_factors["weather"]["weather_factor"]` (new)
- `adjusted_rate` → `final_price`

### Edge cases
- If a strategy didn't run (weight=0), its factor column shows `1.00`
- If weather strategy not enabled, `weather_factor` column shows `1.00`

---

## Feature 4: Arbitrary Date Range (`--from` / `--to`)

### CLI changes
- Add `--from` (default: today) and `--to` (default: today + 30 days) args to `run-config`
- Validation: `--to` must be >= `--from`; range must be <= 365 days
- Override `from_date` / `to_date` in `cmd_run_config` when these args are present
- Currently `cmd_run_config` hard-codes `from_date = datetime.now()`; change to respect CLI args

### CLI examples
```bash
python -m pricing_engine run-config --property 731418607849470882 --from 2025-05-01 --to 2025-05-31
python -m pricing_engine run-config --property 731418607849470882 --from 2025-05-01 --to 2025-05-31 --export-csv
```

---

## Feature 5: Strategy Weight Auto-Normalization

### Current behavior
`compute_price()` already normalizes weights:
```python
total_weight = sum(weights.values())
# ...
weights.get(name, 0.0) / total_weight
```

### Impact of adding WeatherStrategy (weight 0.00)
- New total = 1.00 (Weather contributes 0)
- Existing strategies keep their proportions → **no change needed**
- When Charles sets `weather: 0.10`, new total = 1.10, all others scale down proportionally
- This is the correct desired behavior

### Explicit normalization step
Add a normalization helper in `engine.py`:
```python
def _normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    total = sum(weights.values())
    if total <= 0 or total == 1.0:
        return weights
    return {k: round(v / total, 3) for k, v in weights.items()}
```
Call after weights are resolved in `compute_price()`.

---

## File changes summary

| File | Change |
|---|---|
| `src/pricing_engine/strategies/weather.py` | **New** — WeatherStrategy |
| `src/pricing_engine/strategies/__init__.py` | Add WeatherStrategy export |
| `src/pricing_engine/strategies/event.py` | Read `local_events` from config; apply per-date factors |
| `src/pricing_engine/engine.py` | Add WeatherStrategy to list; add `_normalize_weights` helper |
| `src/pricing_engine/config.py` | Add `openweathermap_api_key` field |
| `src/pricing_engine/cli.py` | Add `--from`, `--to`, `--export-csv` to `run-config`; add CSV writing function |
| `config/properties/731418607849470882.json` | Add `local_events`, `latitude`/`longitude`, weather API fields |
| `SPEC.md` | Update to reflect v2 changes |

---

## Acceptance Criteria (per feature)

### WeatherStrategy
- [ ] `python -m pricing_engine weather-check --property 731418607849470882` (new CLI) returns forecast data
- [ ] API failure (bad key) returns factor 1.00, confidence 0.0, no crash
- [ ] Snow within 24h in forecast → factor 1.08 returned
- [ ] API response cached within single compute_range() call

### Local Events
- [ ] `local_events` in property JSON applies extra factor on matching dates
- [ ] EventStrategy `factors` dict includes `local_event_applied` key
- [ ] No local events → no change to existing behavior

### CSV Export
- [ ] `--export-csv` writes correctly formatted CSV
- [ ] Columns match spec exactly
- [ ] File named `frosty-pines-pricing-{from_date}.csv`
- [ ] CSV opens in Excel without format issues

### Date Range
- [ ] `--from 2025-05-01 --to 2025-05-31` produces 31 rows
- [ ] Default (no args) still produces 30-day window from today

### Weight Normalization
- [ ] Adding weather: 0.10 to weights keeps total ≤ 1.0 and others proportionally scaled
- [ ] All 4 original strategies still produce same relative prices when weather weight is 0

---

## Implementation Order

1. `weather.py` + registration (isolated, easy to test)
2. `EventStrategy` local_events extension (touch existing file)
3. `_normalize_weights` in engine.py (isolated helper)
4. CLI `--from`/`--to`/`--export-csv` (touch cli.py)
5. CSV writing function (isolated)
6. Property config JSON update for Frosty Pines
7. Update SPEC.md
8. Run full test suite — all 32 tests must pass
