# Pricing Engine — Input Parameters & Strategy Guide

> How the pricing engine works, what parameters are available, and how to modify them.

## Overview

The engine computes a **weighted average** of 4 independent pricing strategies, then clamps the result to `[min_price, max_price]` bounds.

```
final_price = clamp(
    demand_price × demand_wt + event_price × event_wt + competitor_price × comp_wt + yield_price × yield_wt,
    min_price,
    max_price
)
```

## Config Hierarchy

Config values flow in this precedence order (highest wins):
1. **Per-property JSON** (`config/properties/{uid}.json`) — `property_overrides[uid]`
2. **Global config** (`EngineConfig` / `.env`)
3. **Hard defaults** in each strategy class

---

## Global Config (EngineConfig / .env)

| Parameter | Default | Description |
|---|---|---|
| `DEFAULT_BASE_PRICE` | `100.0` | Base nightly price (USD) |
| `DEFAULT_MIN_PRICE` | `50.0` | Floor price |
| `DEFAULT_MAX_PRICE` | `2000.0` | Ceiling price |
| `DEFAULT_QUALITY_SCORE` | `0.85` | Property quality vs market (0–1) |
| `STRATEGY_WEIGHTS` | demand:0.40, event:0.30, competitor:0.20, yield:0.10 | Strategy weights (must sum to 1.0) |
| `PRICING_WINDOW_DAYS` | `90` | Days ahead to compute prices |

---

## Per-Property Config (`config/properties/{uid}.json`)

```json
{
  "property_uid": "731418607849470882",
  "base_price": 200.0,
  "min_price": 100.0,
  "max_price": 800.0,
  "quality_score": 0.85,
  "strategy_weights": {
    "demand": 0.40,
    "event": 0.30,
    "competitor": 0.20,
    "yield": 0.10
  },
  "availability": {
    "min_stay": { "default": 2, "overrides": [] },
    "checkin_days": { "blocked": [] },
    "checkout_days": { "blocked": [] }
  }
}
```

---

## Strategy 1: Demand (weight default: 40%)

**What it does:** Adjusts price based on recent booking occupancy rate + booking velocity.

**Formula:**
```
occupancy_rate = booked_nights_in_window / window_days
bookings_per_day = recent_bookings / velocity_window
multiplier = 1.0 + (occupancy_rate × occupancy_factor) + (bookings_per_day × velocity_factor)
price = base_price × multiplier
```

**Config knobs (add to per-property JSON or global config):**

| Parameter | Default | Description |
|---|---|---|
| `demand_window_days` | `14` | Trailing window for occupancy calculation |
| `velocity_window_days` | `7` | Window for booking velocity |
| `velocity_factor` | `0.15` | How much velocity affects price |
| `occupancy_factor` | `0.30` | How much occupancy rate affects price |
| `far_future.window_days` | `60` | Days-out threshold for far-future discount |
| `far_future.discount` | `0.90` | Multiplier applied when days-out > window |
| `last_minute.window_days` | `7` | Last-minute window (days-out) |
| `last_minute.discount` | `0.92` | Discount applied when low occupancy in LM window |
| `last_minute.threshold_occupancy` | `0.5` | Occupancy below this triggers discount |

**Example — increase demand sensitivity:**
```json
{
  "demand_config": {
    "velocity_factor": 0.25,
    "occupancy_factor": 0.40,
    "last_minute": { "window_days": 10, "discount": 0.88, "threshold_occupancy": 0.6 }
  }
}
```

---

## Strategy 2: Event / Seasonal (weight default: 30%)

**What it does:** Adjusts price for holidays, seasonal peaks, and day-of-week premiums.

**Config knobs:**

| Parameter | Default | Description |
|---|---|---|
| `seasonal_multipliers` | Built-in table | Dict of `"MM-DD"` → multiplier (e.g. `"12-25": 1.60`) |
| `dow_multipliers` | mon/tue/wed/thu/sun=1.0, fri/sat=1.15 | Day-of-week multiplier |
| `weekend_multiplier` | `1.15` | Friday/Saturday extra premium (overrides DOW if DOW=1.0) |
| `far_future.window_days` | `60` | Far-future discount threshold |
| `far_future.discount` | `0.90` | Discount for dates beyond window |

**Default seasonal multipliers (built-in):**
- Christmas Eve/Day: 1.40–1.60×
- New Year's: 1.40–1.50×
- July 4th: 1.30×
- Thanksgiving: 1.25–1.35×
- Summer peak (Jun-Aug): 1.10–1.25×

**Add a custom holiday:**
```json
{
  "seasonal_multipliers": {
    "10-31": 1.20,
    "11-27": 1.35
  }
}
```

**Change DOW behavior:**
```json
{
  "dow_multipliers": {
    "mon": 1.0, "tue": 1.0, "wed": 1.0, "thu": 1.0,
    "fri": 1.20,
    "sat": 1.20,
    "sun": 0.95
  }
}
```

---

## Strategy 3: Competitor (weight default: 20%)

**What it does:** Adjusts price relative to market median, weighted by property quality score.

**Requires:** `market_rates` dict in config — `{date: market_median_price}` — or no effect (falls back to base, confidence=0).

**Config knobs:**

| Parameter | Default | Description |
|---|---|---|
| `market_rates` | `{}` | Dict of `"YYYY-MM-DD": median_price` or `{property_uid: price}` |
| `market_avg_quality` | `0.80` | Average quality of competitors |
| `default_quality_score` | `0.85` | This property's quality (can override per-property) |

**Formula:**
```
adjustment = quality_score / market_avg_quality
price = market_median × adjustment
```

**Current status:** No market data source wired — returns base price with confidence=0.

**To wire Wheelhouse:**
1. Get a Wheelhouse Lite API key from `tech-support@usewheelhouse.com`
2. Set `WHEELHOUSE_API_KEY` in `.env`
3. Use the `WheelhouseFetcher` class in `wheelhouse_fetcher.py` to populate `market_rates`

**To disable:** Set `competitor` weight to `0` in `strategy_weights`:
```json
{
  "strategy_weights": {
    "demand": 0.45,
    "event": 0.35,
    "competitor": 0.00,
    "yield": 0.20
  }
}
```

---

## Strategy 4: Yield / Opportunity Cost (weight default: 10%)

**What it does:** Prices based on lead-time, recent booking volume, and churn risk.

**Lead-time buckets:**
- `>30 days out`: `advance_lead_factor` (default 1.05)
- `14–30 days`: `mid_lead_factor` (default 1.10)
- `7–14 days`: `short_lead_factor` (default 1.15)
- `<7 days`: `last_minute_lead_factor` (default 1.20)

**Churn logic:** If current price is significantly above base price, increase churn probability (risk of cancellation).

**Config knobs:**

| Parameter | Default | Description |
|---|---|---|
| `advance_lead_factor` | `1.05` | Far-out multiplier |
| `mid_lead_factor` | `1.10` | Mid-range multiplier |
| `short_lead_factor` | `1.15` | 1-2 week multiplier |
| `last_minute_lead_factor` | `1.20` | Last-minute multiplier |
| `opportunity_threshold_nights` | `7` | Recent booking nights below this → aggressive pricing |
| `low_opportunity_factor` | `1.18` | Multiplier when booking history is low |
| `high_opportunity_factor` | `1.05` | Multiplier when booking history is strong |
| `base_churn_probability` | `0.10` | Starting churn probability |

**Formula:**
```
if recent_nights_booked < opportunity_threshold:
    opportunity_factor = low_opportunity_factor
else:
    opportunity_factor = high_opportunity_factor

multiplier = lead_factor × opportunity_factor × (1 + churn_prob)
price = base_price × multiplier
```

---

## Availability Rules

Controlled by `AvailabilityStrategy` — runs separately from pricing but affects final output.

| Setting | Description |
|---|---|
| `min_stay.default` | Minimum nights for all dates |
| `min_stay.overrides` | Per-DOW or per-month min-stay rules |
| `checkin_days.blocked` | DOWs where checkin is blocked (e.g. `["sun", "mon"]`) |
| `checkout_days.blocked` | DOWs where checkout is blocked |
| `gap_handling.auto_block_gaps` | Block isolated nights between bookings |

---

## Adding a New Strategy Parameter

**Step 1:** Add to `EngineConfig` in `src/pricing_engine/config.py`:
```python
my_new_param: float = Field(default=1.5)
```

**Step 2:** The parameter flows into strategies via the `config` dict in `compute_price()` calls.

**Step 3:** Access in any strategy via `config.get("my_new_param", 1.5)`.

**Step 4:** Document it in this file.

---

## Adding a New Strategy

1. Create `src/pricing_engine/strategies/mystrategy.py` extending `PricingStrategy`
2. Add to `engine.py` `PricingEngine.__init__` strategies list
3. Set default weight in `EngineConfig.default_strategy_weights`
4. Document parameters in this file

---

## Adding/Modifying Strategy Weights Per-Property

In `config/properties/{uid}.json`:
```json
{
  "strategy_weights": {
    "demand": 0.50,
    "event": 0.25,
    "competitor": 0.00,
    "yield": 0.25
  }
}
```

Weights must sum to 1.0 (or the engine normalizes them).

---

## CLI Quick Reference

```bash
# See current vs recommended prices
igms-pricing status --env .env

# Dry run (no writes)
igms-pricing push --dry-run --env .env

# Push all properties
igms-pricing push --env .env

# Per-property config run
igms-pricing run-config --property 731418607849470882 --days 30 --env .env

# Check availability
igms-pricing availability --property 731418607849470882 --days 14 --env .env
```