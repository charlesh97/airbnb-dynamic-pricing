# iGMS Dynamic Pricing — Spec

## API Reference

<https://www.igms.com/docs/airgms-api/index.html>

## iGMS API State (Confirmed)

### Base
- `https://www.igms.com`
- Auth: `access_token` as query param (not Bearer)

### Key Endpoints

| Method | Endpoint | Scope | Description |
|--------|----------|-------|-------------|
| `get_calendar` | `GET /api/v1/get-calendar-data` | calendar-control | Read calendar |
| `set_calendar_data` | `POST /api/v1/set-calendar-data` | calendar-control | Write date range (price, availability) |
| `set_calendar_batch` | `POST /api/v1/set-calendar-batch` | calendar-control | Write per-day prices/availability |
| `propose_calendar_data` | `POST /api/v1/calendar` | pricing-management | Propose prices (owner review step) |
| `propose_calendar_batch` | `POST /api/v1/calendar-batch` | pricing-management | Propose per-day prices |
| `set_property_calendar_control` | `POST /api/v2/set-property-calendar-control` | pricing-management | Enable/disable pricing control |
| `get_request_status` | `GET /api/v1/get-request-status` | listings or calendar-control | Poll async request status |

### Live Properties (confirmed via API)

- `6925833560458409984` — Cozy Modern Single Family 5br (Falls Church, VA)
  - Airbnb listing: `645841896772032198_airbnb_209713065`
  - VRBO listing: `VL6BKKLZ9M_vrbo_VADO73KPBN`
  - AirGMS listing: `I7DLGDUOVA8VO5NQ_airgms_I7DLGDUFGF659GTH`
- `731418607849470882` — Frosty Pines Cabin 2br (Soda Springs, CA)
  - Airbnb listing: `1221946578233906682_airbnb_209713065`
  - VRBO listing: `VL0TXDREZ3_vrbo_VADO73KPBN`
  - AirGMS listing: `HSYKDBO4GC8TPI6Z_airgms_HSYKDBNYMPF1BD2N`

### Current Scopes (confirmed)
- `tasks,messaging,listings,calendar-control,direct-bookings`
- Missing: `pricing-management` (needed for propose_* methods)

### Confirmed Working Write (no pricing-management needed!)
- `POST /api/v1/set-calendar-batch` — works with `calendar-control` scope
- Returns: `{"data": {"request_uids": [1]}}`

### Scope vs Method Mapping
- `calendar-control` → `set_calendar_data`, `set_calendar_batch` (direct write)
- `pricing-management` → `propose_calendar_data`, `propose_calendar_batch` (proposed, owner review)

### OAuth helper
- `scripts/get_pricing_scope.py` — run this to get a token with `pricing-management` scope added
- Add `pricing-management` to the iGMS app settings to enable propose_* methods

## Architecture

- `src/pricing_engine/engine.py` — pricing algorithm (computes recommended prices)
- `src/pricing_engine/client.py` — IGMS API client (extends igms_wrapper)
- `src/pricing_engine/cli.py` — CLI entry point
- `src/pricing_engine/config.py` — environment config

## CLI

```bash
igms-pricing status         # Show current vs recommended prices
igms-pricing run            # Compute prices (no push)
igms-pricing dry-run        # Alias for run
igms-pricing push --dry-run # Preview pushes without committing
igms-pricing push          # Push computed prices to iGMS
```
---

## External Data Sources

### Wheelhouse Lite API

**Spec:** `docs/wheelhouse-openapi.json`

**Base URL:** `https://app.usewheelhouse.com/api/v2/`
**Auth:** `token` query parameter (Lite API key — contact tech-support@usewheelhouse.com)
**Coverage check:** `GET /in_market?latitude=&longitude=&country=&postal_code=`

**Recommendations endpoint:** `GET /recommendations`

| Parameter | Required | Description |
|---|---|---|
| `latitude` | ✅ | Decimal degrees |
| `longitude` | ✅ | Decimal degrees |
| `bedrooms` | ✅ | Number |
| `baths` | ✅ | Number |
| `sleeps` | ✅ | Max guests |
| `country_code` | | ISO 3166-1 alpha-2 (e.g. `US`) |
| `room_type` | | `house`, `cabin`, `apartment`, etc. |
| `cleaning_fee` | | Per-stay cleaning fee |
| `security_deposit` | | Per-stay security deposit |
| `amenities` | | Comma-separated amenity flags |
| `min_price` | | Floor — Wheelhouse won't recommend below this |
| `avg_booking_price` | | Historical avg nightly price (with `booking_price_certainty`) |
| `no_temporality` | | Set `true` to disable lead-time adjustments |

**Response — `daily_recommendation[]`:**
- `base_price` — recommended base nightly price
- `temporality_dollar_value` — adjustment for lead time
- `seasonality_dollar_value` — seasonal premium/discount
- `local_event_dollar_value` — event-based adjustment
- `restriction_dollar_value` — minimum stay / restriction adjustment
- `total_price` — final recommended price

**Note:** API is in beta. Coverage check (`/in_market`) returns 404 if location isn't supported.

### Using Wheelhouse as market data for CompetitorStrategy

The `CompetitorStrategy` accepts `market_rates` via config. Wheelhouse can fill that role:

1. Call `GET /in_market` with property lat/lon → verify coverage
2. Call `GET /recommendations` with property specs + `avg_booking_price` + `booking_price_certainty`
3. Extract `total_price` per date → inject as `market_rates[date]` into engine config
4. Wheelhouse's `local_event_dollar_value` and `seasonality_dollar_value` map to the event/seasonal strategies already in the engine

**Property coordinates needed:** Pull lat/lon from iGMS listing data or Google Maps for both live properties:
- Freedom Place (3335 Freedom Pl, Falls Church, VA)
- Frosty Pines Cabin (10042 Rusty Ln, Soda Springs, CA)

---

## Availability Strategy

The `AvailabilityStrategy` (in `src/pricing_engine/strategies/availability.py`) evaluates whether a date is bookable.

### `AvailabilityResult` dataclass

```python
@dataclass
class AvailabilityResult:
    is_available: bool        # False if blocked
    blocked_reason: str | None
    factors: dict[str, Any]
```

### Rules evaluated (in order)

1. **Blocked checkin days** — dates falling on a configured `checkin_days.blocked` DOW (e.g. `["wed"]`) are unavailable for arrival
2. **Blocked checkout days** — dates falling on a configured `checkout_days.blocked` DOW are unavailable for departure
3. **Same-day checkin** — blocked unless `same_day_checkin.allowed: true` or DOW is in `same_day_checkin.exception.dow`
4. **Gap nights** — if `gap_handling.auto_block_gaps: true`, isolated single nights between bookings are auto-blocked
5. **Booking window** — dates outside configured `booking_window_days` are unavailable

### Configuration (property JSON)

```json
{
  "availability": {
    "booking_window_days": 120,
    "checkin_days": {"blocked": []},
    "checkout_days": {"blocked": []},
    "same_day_checkin": {"allowed": false, "exception": {"dow": []}},
    "gap_handling": {"auto_block_gaps": false, "min_gap_nights": 1}
  }
}
```

### `DatePrice` fields (engine output)

```python
@dataclass
class DatePrice:
    date: str
    property_uid: str
    final_price: float
    strategy_prices: dict[str, float]
    strategy_weights: dict[str, float]
    confidence: float
    all_factors: dict[str, Any]
    is_available: bool = True        # ← new
    blocked_reason: str | None = None  # ← new
```

### Manual overrides

`apply_manual_overrides(date_price, uid, date, config)` inspects `config["manual_overrides"][date]` for `price_override` and `availability` fields and applies them after price computation.

### CLI commands (Loop 8)

```bash
igms-pricing --env .env run-config --property <uid> [--days N]
igms-pricing --env .env availability --property <uid> [--days 90]
igms-pricing --env .env wheelhouse-check --property <uid> [--days 90]
igms-pricing --env .env push-config --property <uid> [--dry-run] [--force]
```

### Property Config Store

`PropertyConfigStore` (`src/pricing_engine/config_store.py`) loads/saves per-property JSON configs from `config/properties/<uid>.json`.

---

## Property Config Store

`src/pricing_engine/config_store.py` — `PropertyConfigStore` class.

- `load(uid)` — read `config/properties/<uid>.json`
- `save(uid, config)` — write config to disk
- `list_properties()` — list all UIDs with configs on disk
- `merge_with_env_defaults(uid, env_config)` — overlay property JSON with environment-based engine defaults (env wins for top-level keys where property config is absent)

---

## Wheelhouse Fetcher

`src/pricing_engine/wheelhouse_fetcher.py` — `WheelhouseFetcher` class.

- `check_coverage(latitude, longitude, country, postal_code)` → `{in_market, market_name}`
- `fetch_recommendations(...)` → `list[dict]` (filtered to `from_date`/`to_date`)
- `build_market_rates(recommendations)` → `dict[date_str, float]` for `market_rates` injection

---

## Pricing Strategies

### Overview

The engine runs four pricing strategies and computes a weighted average price:

| Strategy | Default Weight | Purpose |
|---|---|---|
| `demand` | 0.35 | Occupancy + booking velocity |
| `event` | 0.35 | Seasonal, holiday, DOW multipliers |
| `yield` | 0.30 | Lead-time, churn discount, last-minute adjustment |
| `competitor` | 0.00 | External market data (disabled by default) |

Weights are normalized to sum to 1.0.

---

### DemandStrategy (`src/pricing_engine/strategies/demand.py`)

Occupancy + booking velocity driven. **Key change:** replaces the old linear occupancy formula with nonlinear bands.

**Occupancy multiplier bands:**
| Occupancy Rate | Multiplier |
|---|---|
| < 0.30 | 0.90 (soft discount — low demand) |
| 0.30–0.60 | 1.00 → 1.15 (linear interpolation) |
| 0.60–0.80 | 1.15 → 1.35 |
| 0.80–0.95 | 1.35 → 1.60 |
| > 0.95 | 1.60 → 1.90 |

**Far-future discount:** configurable via `far_future.window_days` / `far_future.discount`.

**Output factors:** `occupancy_rate`, `occupancy_multiplier`, `bookings_per_day`, `velocity_multiplier`, `demand_multiplier`, `far_future_discount_applied`, `last_minute_override` (always `None` — last-minute is handled by YieldStrategy).

---

### EventStrategy (`src/pricing_engine/strategies/event.py`)

Seasonal + holiday + DOW pricing.

**Monthly seasonal multipliers (spec default):**
```python
SEASONAL_MONTHS = {
    "01": 1.35,  # Jan — peak ski
    "02": 1.30,  # Feb — peak ski
    "03": 1.15,  # Mar — ski shoulder
    "04": 0.80,  # Apr — off-season
    "05": 0.75,  # May — off-season
    "06": 1.00,  # Jun — normal
    "07": 1.20,  # Jul — summer peak
    "08": 1.15,  # Aug — summer peak
    "09": 0.90,  # Sep — off-season
    "10": 0.80,  # Oct — off-season
    "11": 0.85,  # Nov — off-season (pre-holiday)
    "12": 1.40,  # Dec — holiday peak
}
```

**Holiday calendar with buffer logic:**

```python
HOLIDAY_CALENDAR = [
    {"name": "Christmas Eve",  "date": "12-24", "multiplier": 1.50, "buffer_days": 3,  "buffer_slope": 0.05},
    {"name": "Christmas Day",  "date": "12-25", "multiplier": 1.60, "buffer_days": 3,  "buffer_slope": 0.05},
    {"name": "New Year's Eve", "date": "12-31", "multiplier": 1.60, "buffer_days": 3,  "buffer_slope": 0.05},
    {"name": "New Year's Day", "date": "01-01", "multiplier": 1.50, "buffer_days": 3,  "buffer_slope": 0.05},
    {"name": "July 4th",       "date": "07-04", "multiplier": 1.40, "buffer_days": 2,  "buffer_slope": 0.07},
    {"name": "Thanksgiving",   "date": "11-26", "multiplier": 1.45, "buffer_days": 4,  "buffer_slope": 0.05},
    {"name": "Thanksgiving Fri","date": "11-27","multiplier": 1.35, "buffer_days": 0, "buffer_slope": 0.0},
    {"name": "Thanksgiving Sun","date": "11-29", "multiplier": 1.25, "buffer_days": 0, "buffer_slope": 0.0},
    {"name": "MLK Weekend",    "date": "01-19", "multiplier": 1.25, "buffer_days": 2,  "buffer_slope": 0.04},
    {"name": "Presidents' Day","date": "02-16", "multiplier": 1.25, "buffer_days": 2,  "buffer_slope": 0.04},
    {"name": "Memorial Day",   "date": "05-25", "multiplier": 1.20, "buffer_days": 2,  "buffer_slope": 0.05},
    {"name": "Labor Day",      "date": "09-07", "multiplier": 1.20, "buffer_days": 2,  "buffer_slope": 0.05},
    {"name": "President's Week","date": "02-13", "multiplier": 1.30, "buffer_days": 3, "buffer_slope": 0.04},
    {"name": "Spring Break",   "date": "03-20", "multiplier": 1.20, "buffer_days": 3,  "buffer_slope": 0.04},
]
```

**Buffer logic:** Dates within `buffer_days` of a holiday get a gradient multiplier: `holiday_mult × (1 - slope × distance)`. Exact holiday matches (diff=0) use the full multiplier without fade. When multiple holidays affect a date (e.g. Dec 25 is in Christmas Eve's buffer), the highest multiplier wins.

**DOW multipliers (applied on top of seasonal):**
```python
DEFAULT_DOW_MULTIPLIERS = {
    "mon": 1.0, "tue": 1.0, "wed": 1.0, "thu": 1.0,
    "fri": 1.15, "sat": 1.15, "sun": 1.0,
}
```

**Local events** from property JSON multiply on top: `{ "name": "...", "date": "YYYY-MM-DD", "factor": 1.10 }`.

**Output factors:** `seasonal_multiplier`, `dow_multiplier`, `is_peak_season`, `is_holiday_period`, `holiday_name`, `holiday_buffer_applied`, `local_event_applied`, `far_future_discount_applied`.

---

### YieldStrategy (`src/pricing_engine/strategies/yield_.py`)

Revenue optimization via lead-time pricing + conditional last-minute adjustments.

**Lead-time buckets (updated):**
| Bucket | Days Out | Lead Factor |
|---|---|---|
| `advance` | > 30 | 1.00 |
| `mid` | 14–30 | 1.05 |
| `short` | 7–14 | 1.08 |
| `last_minute` | < 7 | 1.05 |

**Churn logic (inverted):** Old = `multiplier × (1 + churn_prob)` (high churn → higher price). New = `multiplier × (1 - churn_discount)` where `churn_discount = churn_prob / (1 + churn_prob)`. High churn now discounts price (booking is fragile), not premium it.

**Conditional last-minute adjustment** (`compute_last_minute_adjustment()`):

Unlike the old universal multiplier, this is context-aware:

| Days Out | Context | Adjustment |
|---|---|---|
| 0 (same day) | Holiday/peak weekend | 1.00 (hold) |
| 0 | Orphan gap | 0.70 (soft discount) |
| 0 | Standard | 0.75 (gentle discount) |
| 1 | Holiday | 1.10 (premium) |
| 1 | Off-season + low occupancy (<0.30) | 0.78 (discount) |
| 1 | Off-season weekday | 0.85 (soft discount) |
| 1 | Normal | 1.00 (hold) |
| 2–3 | Holiday | 1.10 (premium) |
| 2–3 | Off-season weekend + low occupancy | 0.92 (slight discount) |
| 2–3 | Off-season weekday + low occupancy | 0.88 (discount) |
| 2–3 | Orphan gap | 0.80 (discount) |
| 2–3 | Normal | 1.00 (hold) |
| 4–6 | Holiday | 1.15 (premium) |
| 4–6 | Peak season weekend | 1.05 (slight premium) |
| 4–6 | Off-season weekday + low occupancy | 0.90 (discount) |
| 4–6 | Normal | 1.00 (hold) |
| 7+ | Any | 1.00 (no adjustment) |

**Context flags:**
- `is_peak_season`: month in [12, 1, 2] + weekend, or month in [7, 8]
- `is_off_season`: month in [4, 5, 9, 10, 11]
- `is_holiday_period`: date is on or within buffer of a configured holiday
- `is_orphan_gap`: adjacent nights are booked but this night is not

**Output factors:** `lead_bucket`, `lead_factor`, `churn_discount`, `opportunity_factor`, `final_multiplier`, `last_minute_adjustment`, `last_minute_reasoning`, `days_out`.

---

### CompetitorStrategy (`src/pricing_engine/strategies/competitor.py`)

Market-rate adjusted pricing. **Disabled by default** (weight = 0.00).

**Configuration via `external_market_data` block in property JSON:**
```json
{
  "external_market_data": {
    "enabled": false,
    "source": null,
    "api_key": null,
    "last_pull_timestamp": null,
    "comp_set_definition": {
      "location": "Soda Springs / Donner Summit / Kingvale",
      "bedrooms": 2,
      "property_type": "cabin,home",
      "exclude_property_types": "hotel,condo,apartment"
    },
    "raw_data": {},
    "confidence": 0.0,
    "num_comps_used": 0
  }
}
```

**When disabled:** returns `confidence=0.0`, `status="disabled"`, `note="external_market_data is disabled — using fallback"`.

**When enabled:** expects `raw_data` by date:
```python
{
  "YYYY-MM-DD": {
    "market_rates": [175.0, 190.0, 210.0],  # list of comp nightly rates
    "occupancy": 0.65,
    "booking_pace": 0.8,
    "avg_quality": 0.82
  }
}
```
Uses median of `market_rates` as `market_median`, then applies quality adjustment: `price = market_median × (quality_score / market_avg_quality)`.

**Public methods:**
- `parse_market_data(raw_data, date)` → `{market_median, market_avg_quality}` or `None`
- `compute_market_adjustment(market_median, quality_score, market_avg_quality)` → adjustment float

---

## PricingEngine (`src/pricing_engine/engine.py`)

### Default Strategy Weights

```python
_default_weights = {
    "demand": 0.35,
    "event": 0.35,
    "competitor": 0.00,
    "yield": 0.30,
}
```

### Explanation Block

Each `DatePrice.all_factors` includes a top-level `explanation` key:
```python
{
    "base_price": 250.0,
    "season_applied": "off-season",
    "season_multiplier": 0.75,
    "dow_multiplier": 1.00,
    "holiday_multiplier": 1.00,
    "demand_multiplier": 0.90,
    "yield_multiplier": 0.991,
    "last_minute_adjustment": 0.88,
    "occupancy_adjustment": 0.90,
    "competitor_adjustment": "disabled",
    "weighted_price_before_caps": 218.72,
    "min_cap_applied": false,
    "max_cap_applied": false,
    "final_price": 218.72,
    "summary": "Off-season, weekday, low occupancy, last-minute discount",
    "strategy_breakdown": {
        "demand":  {"price": 225.0, "weight": 0.35, "contribution": 78.75},
        "event":    {"price": 187.5, "weight": 0.35, "contribution": 65.63},
        "yield":    {"price": 247.8, "weight": 0.30, "contribution": 74.34},
        "competitor": {"price": 250.0, "weight": 0.00, "contribution": 0.00},
    }
}
```

---

## Strategy Weight Normalization

`_normalize_weights()` in `engine.py` normalizes weights to sum to 1.0. Called in `compute_price()` before computing the weighted average. Rounds to 3 decimal places.

When weather weight is 0.00, existing strategies retain their relative proportions. When Charles sets `weather: 0.10`, all strategies scale down proportionally.

---

## Property Configuration (Frosty Pines example)

```json
{
  "property_uid": "731418607849470882",
  "name": "Frosty Pines Cabin: 2br Retreat",
  "base_price": 250.0,
  "min_price": 100.0,
  "max_price": 800.0,
  "quality_score": 0.85,
  "strategy_weights": {
    "demand": 0.35,
    "event": 0.35,
    "competitor": 0.00,
    "yield": 0.30
  },
  "seasonal_base_prices": {
    "winter_peak": 325.00,
    "winter_shoulder": 275.00,
    "spring_mud": 190.00,
    "summer_peak": 285.00,
    "fall_slow": 200.00,
    "holiday": 450.00,
    "fallback": 250.00
  },
  "seasonal_months": {
    "01": 1.35, "02": 1.30, "03": 1.15, "04": 0.80,
    "05": 0.75, "06": 1.00, "07": 1.20, "08": 1.15,
    "09": 0.90, "10": 0.80, "11": 0.85, "12": 1.40
  },
  "dow_multipliers": {
    "mon": 1.0, "tue": 1.0, "wed": 1.0, "thu": 1.0,
    "fri": 1.15, "sat": 1.15, "sun": 1.0
  },
  "external_market_data": {
    "enabled": false,
    "source": null,
    "last_pull_timestamp": null,
    "comp_set_definition": {
      "location": "Soda Springs / Donner Summit / Kingvale",
      "bedrooms": 2,
      "property_type": "cabin,home",
      "exclude_property_types": "hotel,condo,apartment"
    }
  }
}
```

---

## CLI Commands

```bash
igms-pricing status          # Show current vs recommended prices
igms-pricing run             # Compute prices (no push)
igms-pricing dry-run         # Alias for run
igms-pricing push --dry-run  # Preview pushes without committing
igms-pricing push           # Push computed prices to iGMS
igms-pricing run-config --property <uid> [--days N] [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--export-csv]
igms-pricing availability --property <uid> [--days 90]
igms-pricing wheelhouse-check --property <uid> [--days 90]
igms-pricing push-config --property <uid> [--dry-run] [--force]
igms-pricing debug-day --date YYYY-MM-DD [--property <uid>]
```

### debug-day

Factor breakdown for a single day across all properties. Shows the chain:
```
BASE → SEASONAL → DEMAND → EVENT → YIELD → COMPETITOR → FINAL
```

Uses `dashboard/engine_proxy.py → get_day_detail()` which fetches real iGMS bookings and applies the full merged config.
