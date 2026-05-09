# iGMS Dynamic Pricing Dashboard — Architecture Plan

> **Status:** Planned  
> **Author:** Atlas (Codex planning pass)  
> **Output:** `DASHBOARD_PLAN.md`

---

## 1. Framework Decision: FastAPI over Flask

**Choice: FastAPI**

| Criterion | Flask | FastAPI |
|---|---|---|
| Native async | No (extensions needed) | Yes |
| Pydantic integration | None | Built-in — natural fit for `DatePrice` dataclass |
| OpenAPI/Swagger auto-docs | Manual via Flask-RESTX | Auto from route signatures |
| Type safety | Duck-typed | Full type-checking with mypy/Pyright |
| Concurrency | WSGI (one request/response) | ASGI (async-native, better for I/O) |
| Learning curve | Slightly lower | Slightly higher — but well-documented |
| Python 3.10+ dataclass support | Works | Works (no difference) |

**Rationale:** FastAPI's native Pydantic models align directly with the existing `DatePrice` dataclass structure. The dashboard is I/O-bound (file reads, strategy computation) and will benefit from async. Auto-generated Swagger docs at `/docs` are a bonus for future extensibility. The one tradeoff (slightly steeper learning curve) is irrelevant for a greenfield local app with a single developer.

**Run command:** `uvicorn dashboard.main:app --reload --port 5050`  
**URL:** `http://localhost:5050`

---

## 2. File Structure

The dashboard lives in a self-contained subdirectory inside the repo:

```
airbnb-dynamic-pricing/
├── dashboard/
│   ├── __init__.py
│   ├── main.py              # FastAPI app, CORS, lifespan startup
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── calendar.py      # GET /api/calendar/{year}/{month}
│   │   ├── day_detail.py    # GET /api/days/{date}
│   │   ├── config.py        # GET/PUT /api/config/{property_uid}
│   │   └── pricing.py       # POST /api/pricing/run
│   ├── engine_proxy.py     # Thin wrapper — imports PricingEngine + PropertyConfigStore
│   ├── models.py            # Pydantic models for API request/response schemas
│   ├── templates/
│   │   ├── base.html        # Shared layout, CSS, nav tabs
│   │   ├── calendar.html   # Calendar view template
│   │   └── config_editor.html # Config editor template
│   └── static/
│       ├── css/
│       │   └── dashboard.css
│       └── js/
│           ├── calendar.js     # Month grid rendering, color coding, prev/next nav
│           ├── day_panel.js     # Slide-out factor breakdown panel
│           ├── config_editor.js # All config field bindings, save logic
│           └── api.js          # Shared fetch wrapper with error handling
├── config/
│   └── properties/
│       └── 731418607849470882.json  # Frosty Pines (unchanged)
├── src/
│   └── pricing_engine/           # Imported directly by engine_proxy.py
└── pyproject.toml               # Add fastapi, uvicorn dependencies
```

**Import strategy:** `dashboard/engine_proxy.py` adds the repo's `src/` to `sys.path` at startup, then does a normal Python import:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pricing_engine.engine import PricingEngine
from pricing_engine.config_store import PropertyConfigStore
from pricing_engine.config import EngineConfig
```

No CLI forking. No subprocess. The engine runs in the same Python process as the web server.

---

## 3. New Python Dependencies

```toml
# pyproject.toml additions
[dependencies]
fastapi = ">=0.115"
uvicorn = { version = ">=0.32", extras = ["standard"] }
# Existing dependencies unchanged
```

Install: `pip install fastapi "uvicorn[standard]"`

No database needed. No Redis. No Celery. State lives in the property JSON files.

---

## 4. API Endpoints

### `GET /api/properties`
Returns the list of configured properties (for future multi-property, but needed now).

**Response:**
```json
{
  "properties": [
    {
      "property_uid": "731418607849470882",
      "name": "Frosty Pines Cabin: 2br Retreat"
    }
  ]
}
```

---

### `GET /api/calendar/{year}/{month}`
Computes pricing for every day in the requested month and returns a grid-ready array.

**Query params:**
- `property_uid` (required) — currently always `731418607849470882`
- `airbnb_prices` (optional) — JSON map of `{date: price}` with current Airbnb nightly prices for comparison. If omitted, the `airbnb_*` fields in the response are null.

**Response:**
```json
{
  "year": 2026,
  "month": 5,
  "property_uid": "731418607849470882",
  "days": [
    {
      "date": "2026-05-01",
      "final_price": 172.50,
      "current_airbnb_price": 185.0,
      "price_delta": -12.50,
      "price_delta_pct": -0.068,
      "match_status": "undersell",     // "close" | "undersell" | "oversell"
      "is_available": true,
      "min_stay": 2,
      "blocked_reason": null,
      "confidence": 0.82
    }
  ]
}
```

**Match status thresholds (configurable):**
- `close` — |delta_pct| ≤ 10%
- `undersell` — delta_pct < -10%
- `oversell` — delta_pct > 10%

**How computation works:**
1. Backend loads property config via `PropertyConfigStore`
2. Builds `calendar_data` (empty list for now — future: from iGMS API stub or cache)
3. Calls `PricingEngine.compute_range(property_uid, from_date, to_date, calendar_data, [], config)`
4. Maps results to the response shape above

---

### `GET /api/days/{date}`
Returns full factor breakdown for a single day (powers the slide-out panel).

**Query params:**
- `property_uid` (required)
- `airbnb_price` (optional) — current Airbnb price to show in comparison

**Response:**
```json
{
  "date": "2026-05-15",
  "property_uid": "731418607849470882",
  "final_price": 198.00,
  "current_airbnb_price": 185.0,
  "confidence": 0.85,
  "is_available": true,
  "min_stay": 2,
  "blocked_reason": null,
  "booking_window_days": 120,
  "match_status": "oversell",

  "base_rate": 200.0,

  "seasonal": {
    "rule": "local_event",
    "detail": "Soda Springs Ski Day (2026-05-15)",
    "multiplier": 1.10,
    "dow": "fri",
    "dow_multiplier": 1.15,
    "raw_seasonal_multiplier": 1.0,
    "effective_seasonal": 1.265
  },

  "demand": {
    "multiplier": 1.05,
    "occupancy": {
      "value": 0.72,
      "window_days": 14,
      "factor": 0.30,
      "contribution": "DemandStrategy (occupancy demand high)"
    },
    "velocity": {
      "value": 0.45,
      "window_days": 7,
      "factor": 0.15,
      "contribution": "DemandStrategy (booking velocity)"
    },
    "far_future": {
      "discount": 0.90,
      "window_days": 60,
      "active": true
    },
    "last_minute": {
      "discount": 0.92,
      "window_days": 7,
      "threshold_occupancy": 0.5,
      "active": false
    }
  },

  "event": {
    "suggested_price": 220.00,
    "factors": {
      "local_event": "Soda Springs Ski Day (2026-05-15)",
      "event_factor": 1.10
    }
  },

  "yield": {
    "suggested_price": 190.00,
    "factors": {
      "yield_score": 0.78,
      "recent_booking_value": 185.0
    }
  },

  "competitor": {
    "suggested_price": null,
    "factors": {},
    "note": "competitor strategy disabled (weight = 0.0)"
  },

  "strategy_weights": {
    "demand": 0.50,
    "event": 0.375,
    "competitor": 0.0,
    "yield": 0.125,
    "weather": 0.0
  },

  "strategy_prices": {
    "demand": 215.00,
    "event": 220.00,
    "competitor": null,
    "yield": 190.00
  },

  "raw_factors": {
    "demand": { /* full PriceRecommendation.factors from DemandStrategy */ },
    "event": { /* full PriceRecommendation.factors from EventStrategy */ },
    "yield": { /* full PriceRecommendation.factors from YieldStrategy */ },
    "competitor": { /* full PriceRecommendation.factors from CompetitorStrategy */ }
  }
}
```

**Note on `raw_factors`:** The `all_factors` dict from `DatePrice` can be deeply nested. `raw_factors` exposes the complete internal factors dict for debugging. The top-level structured keys (`seasonal`, `demand`, `event`, `yield`, `competitor`) are extracted for UI presentation.

---

### `GET /api/config/{property_uid}`
Returns the raw property config JSON.

**Response:** The full JSON of `config/properties/{property_uid}.json`.

---

### `PUT /api/config/{property_uid}`
Saves an edited property config. Validates the payload before writing.

**Request body:** Full property config JSON (same shape as `GET` response).  
**Validation:**
- `base_price`, `min_price`, `max_price` must be numbers; `min_price ≤ base_price ≤ max_price`
- `strategy_weights` values must be non-negative and sum to ≤ 1.0 (normalized on save)
- `seasonal_multipliers` values must be positive numbers
- `dow_multipliers` keys must be valid day names

**Success response:** `200 OK` with the saved config  
**Error response:** `422 Unprocessable Entity` with validation error details

---

### `POST /api/pricing/run`
Re-runs pricing for a specific month. Used by the "Run Pricing" button.

**Request body:**
```json
{
  "property_uid": "731418607849470882",
  "year": 2026,
  "month": 5,
  "airbnb_prices": { "2026-05-01": 185.0, "2026-05-02": 185.0 }
}
```

**Response:** Same shape as `GET /api/calendar/{year}/{month}`.

**What it does:** Loads the current config, runs `compute_range`, returns the computed grid. Does NOT push prices to Airbnb or iGMS (future feature).

---

## 5. Factor Breakdown Data Structure

The `all_factors` dict from `DatePrice.all_factors` maps strategy name → strategy's internal `factors` dict. Each strategy's factors are opaque from the engine's perspective, but for Frosty Pines we know the structure:

```python
# all_factors keys and expected inner shapes (documented for the dashboard):

all_factors = {
    "demand": {
        # From DemandStrategy
        "occupancy": float,          # 0.0–1.0 occupancy in window
        "occupancy_demand": float,   # adjusted demand figure
        "velocity": float,           # bookings per day in velocity window
        "velocity_demand": float,    # velocity-adjusted demand
        "far_future_active": bool,
        "far_future_discount": float,
        "last_minute_active": bool,
        "last_minute_discount": float,
        "combined_multiplier": float # final demand multiplier applied to price
    },
    "event": {
        # From EventStrategy
        "seasonal_multiplier": float,   # from seasonal_multipliers table (MM-DD lookup)
        "dow_multiplier": float,       # from dow_multipliers
        "local_event_factor": float,   # from local_events list matching date
        "hit_rule": str,               # "local_event" | "seasonal" | "dow" | "base"
    },
    "yield": {
        # From YieldStrategy
        "recent_bookings_avg": float,
        "nights_booked": int,
        "yield_score": float,
    },
    "competitor": {
        # From CompetitorStrategy (empty if disabled)
        "competitor_prices": list[float],
        "competitor_avg": float,
    }
}
```

The `/api/days/{date}` endpoint maps these opaque factors into the structured `seasonal`, `demand`, `event`, `yield`, `competitor` keys shown in §4.

---

## 6. Config Editor: Field-to-Control Mapping

Every field in the Frosty Pines JSON mapped to a UI control:

| JSON path | UI control type | Notes |
|---|---|---|
| `base_price` | Number input (step=1, min=1) | Primary input |
| `min_price` | Number input (step=1, min=0) | |
| `max_price` | Number input (step=1, min=1) | |
| `strategy_weights.demand` | Range slider (0–1, step=0.01) | Label shows live value |
| `strategy_weights.event` | Range slider (0–1, step=0.01) | |
| `strategy_weights.competitor` | Range slider (0–1, step=0.01) | Usually 0.0 |
| `strategy_weights.yield` | Range slider (0–1, step=0.01) | |
| `strategy_weights.weather` | Range slider (0–1, step=0.01) | Always 0.0 (hidden or disabled) |
| `seasonal_multipliers` | Editable table: MM-DD text input + multiplier input | Sorted by date; inline edit |
| `dow_multipliers.mon` | Range slider (0.5–2.0, step=0.01) | |
| `dow_multipliers.tue` | Range slider (0.5–2.0, step=0.01) | |
| `dow_multipliers.wed` | Range slider (0.5–2.0, step=0.01) | |
| `dow_multipliers.thu` | Range slider (0.5–2.0, step=0.01) | |
| `dow_multipliers.fri` | Range slider (0.5–2.0, step=0.01) | |
| `dow_multipliers.sat` | Range slider (0.5–2.0, step=0.01) | |
| `dow_multipliers.sun` | Range slider (0.5–2.0, step=0.01) | |
| `demand_config.demand_window_days` | Number input (step=1, min=1) | |
| `demand_config.velocity_window_days` | Number input (step=1, min=1) | |
| `demand_config.velocity_factor` | Range slider (0–1, step=0.01) | |
| `demand_config.occupancy_factor` | Range slider (0–1, step=0.01) | |
| `demand_config.far_future.window_days` | Number input | |
| `demand_config.far_future.discount` | Range slider (0.5–1.0, step=0.01) | |
| `demand_config.last_minute.window_days` | Number input | |
| `demand_config.last_minute.discount` | Range slider (0.5–1.5, step=0.01) | |
| `demand_config.last_minute.threshold_occupancy` | Range slider (0–1, step=0.01) | |
| `availability.booking_window_days` | Number input (step=1, min=1) | |
| `availability.min_stay.default` | Number input (step=1, min=1) | |
| `availability.min_stay.overrides` | Sub-table: DOW + min_nights (future) | |
| `availability.checkin_days.blocked` | Multi-select or chip input | |
| `availability.checkout_days.blocked` | Multi-select or chip input | |
| `local_events` | Repeatable sub-form: name + date + factor | Add/Remove rows |

**Weight normalization:** When any strategy weight slider changes, the total is computed. If total ≠ 1.0, a "Re-normalize" button appears. On save, weights are normalized to sum to 1.0 (via `PricingEngine._normalize_weights`).

**Save behavior:**
1. Client collects all field values into a dict matching the Frosty Pines JSON structure
2. `PUT /api/config/731418607849470882` with that JSON
3. Backend validates, normalizes weights, writes to disk
4. On success: show toast "Config saved"; the calendar is NOT auto-refreshed (user clicks "Run Pricing" to see effects)

---

## 7. Calendar Fetch Strategy

**Current state:** The pricing engine does NOT have an iGMS API integration. `calendar_data` is an empty list (`[]`) passed to `compute_range`. Bookings data is also empty (`[]`).

**Dashboard approach for v1:**  
- No iGMS API calls. `calendar_data` and `bookings_in_window` are empty.
- The "current Airbnb price" shown in the calendar comes from an **optional JSON file** at `config/airbnb_prices_cache.json` that Charles can drop in manually or via a future script.
- Shape: `{ "731418607849470882": { "2026-05-01": 185.0, "2026-05-02": 190.0 } }`
- The `airbnb_prices_cache.json` is read on each `/api/calendar/` call and parsed from the `airbnb_prices` query param (which can be posted via the "Run Pricing" form).

**Future iGMS integration (out of scope for v1):**  
- Add `GET /api/calendar` endpoint that fetches from iGMS API via `igms-client`
- Cache results in `config/igms_calendar_cache.json` with a TTL
- The `airbnb_prices` field would then come from iGMS reservation data

---

## 8. Frontend Component Structure

**Framework:** Vanilla JS (no React, no Vue, no build step). Single HTML file per tab. Shared CSS. ES modules for JS.

**Base layout (`base.html`):**
```
┌─────────────────────────────────────┐
│  🏠 iGMS Dynamic Pricing Dashboard  │
│  [Calendar]  [Config Editor]        │  ← tab navigation
├─────────────────────────────────────┤
│                                     │
│   <content from active tab>         │
│                                     │
└─────────────────────────────────────┘
```

**`calendar.js` responsibilities:**
- `renderMonth(year, month)` — builds the 7×5/6 grid of day cells
- Each cell: shows `final_price` + `current_airbnb_price`, colored by `match_status`
- Prev/Next buttons navigate months; update URL hash or query param
- Click handler on day cell → calls `DayPanel.open(date)`
- "Run Pricing" button → `POST /api/pricing/run` with current month + airbnb prices, then re-renders

**`day_panel.js` responsibilities:**
- `DayPanel.open(date)` — slides in a right-side panel
- Fetches `GET /api/days/{date}?property_uid=&airbnb_price=`
- Renders factor breakdown (base rate, demand, seasonal, yield, event, availability, weights)
- Close button or click-outside → slides panel closed

**`config_editor.js` responsibilities:**
- On tab switch: `GET /api/config/731418607849470882` → populate all form fields
- Each field change → track dirty state (show "Unsaved changes" indicator)
- Weight sliders → live sum display; if sum ≠ 1.0, show normalization button
- Save button → `PUT /api/config/731418607849470882`
- Seasonal table: inline editing of multiplier cells; Add Row / Delete Row for local_events

**`api.js` responsibilities:**
- `api.get(url)` → `fetch` with error handling
- `api.post(url, body)` → `fetch` POST with JSON body
- All calls go to relative paths (proxied through FastAPI's static file serving)
- On error: show inline error toast, log to console

**CSS (`dashboard.css`):**
- CSS Grid for the calendar month layout
- Flexbox for the day cell internals
- CSS transitions for the slide-out panel (transform: translateX)
- Color variables: `--color-close`, `--color-undersell`, `--color-oversell`
- Mobile-friendly (single column on narrow screens)

---

## 9. State Management

| State | Where it lives | How it's updated |
|---|---|---|
| Property config | `config/properties/{uid}.json` on disk | Written on PUT; read on startup and on tab switch |
| Calendar computation | In-memory (computed per request) | Recomputed on `POST /api/pricing/run` |
| Airbnb price cache | `config/airbnb_prices_cache.json` on disk | Updated manually or by external script |
| Current month view | URL query params (`?year=2026&month=5`) | Updated on prev/next navigation |
| Dirty config state | In-memory in `config_editor.js` | Tracked on field change; cleared on save |

**Reload-on-save behavior:** After `PUT /api/config`, the calendar does NOT auto-refresh. The "Run Pricing" button is the explicit trigger for recomputation. This avoids accidental re-runs and keeps the UX predictable.

---

## 10. Availability Info in Day Detail

From `AvailabilityStrategy` and `AvailabilityStrategy.compute()` → `AvailabilityResult`:
- `is_available: bool`
- `min_stay: int`
- `blocked_reason: str | None` (e.g. `"manual_block"`, `"booking_window"`, `"checkin_blocked"`, etc.)
- `booking_window_days: int` (from config)

These are returned at the top level of `GET /api/days/{date}` (see §4).

---

## 11. What's NOT in Scope for v1

- Multi-property support (Frosty Pines only)
- iGMS API integration / live calendar fetch
- Price push to Airbnb (writeback)
- Authentication / multi-user
- Historical price chart
-竞争对手 comp set management
- Weather data
- Automated scheduling / cron
- Mobile-optimized layout (responsive desktop-first)

---

## 12. Build / Run

```bash
# Install dependencies
cd /Users/charlesclaw/Documents/git/airbnb-dynamic-pricing
pip install fastapi "uvicorn[standard]"

# Run locally
uvicorn dashboard.main:app --reload --port 5050

# Open in browser
open http://localhost:5050
```

Swagger docs auto-generated at `http://localhost:5050/docs`.

---

## 13. Implementation Order

1. **`dashboard/engine_proxy.py`** — import and wrap PricingEngine + PropertyConfigStore
2. **`dashboard/models.py`** — Pydantic request/response models
3. **Static files skeleton** — `dashboard/templates/base.html`, CSS, `api.js`
4. **`dashboard/routes/pricing.py`** — `POST /api/pricing/run`
5. **`dashboard/routes/calendar.py`** — `GET /api/calendar/{year}/{month}`
6. **`dashboard/routes/day_detail.py`** — `GET /api/days/{date}`
7. **`dashboard/routes/config.py`** — `GET/PUT /api/config/{property_uid}`
8. **`dashboard/main.py`** — FastAPI app wiring + static file serving
9. **`static/js/calendar.js`** + month grid UI
10. **`static/js/day_panel.js`** + slide-out panel
11. **`static/js/config_editor.js`** + all form controls + save logic
12. **Testing**: Manual smoke test of each endpoint + UI interaction