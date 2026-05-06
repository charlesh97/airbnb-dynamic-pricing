# iGMS Dynamic Pricing — Web App Plan

**Status:** Draft — not yet implemented
**Focus:** Build the core pricing engine first; web UI is Phase 2.

---

## Overview

A Flask web application that provides a GUI for managing property-level pricing configurations, viewing computed recommendations, and pushing prices to iGMS. The JSON config files remain the source of truth; the web UI is a form-backed editor on top of them.

---

## File Structure

```
igms-dynamic-pricing/
├── web/
│   ├── app.py                    # Flask app + routes
│   ├── config_store.py            # Read/write property JSON configs
│   ├── wheelhouse_fetcher.py      # Wheelhouse API client
│   ├── templates/
│   │   ├── base.html              # Shared layout
│   │   ├── dashboard.html          # All properties overview
│   │   ├── property_editor.html   # Tabbed editor for one property
│   │   └── pricing_preview.html   # 90-day price preview table
│   └── static/
│       ├── style.css
│       └── app.js                 # Form wiring, tab switching, fetch helpers
├── config/
│   └── properties/
│       ├── 6925833560458409984.json   # Freedom Place config
│       └── 731418607849470882.json    # Frosty Pines config
└── docs/
    └── web-app-plan/
        └── README.md              # This file
```

---

## Routes

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Dashboard — property cards with availability + last sync |
| `GET` | `/property/<uid>` | Tabbed editor for a property's full config |
| `POST` | `/property/<uid>` | Save config JSON (all tabs merged) |
| `GET` | `/property/<uid>/preview` | Computed prices for next 90 days (JSON) |
| `GET` | `/property/<uid>/wheelhouse-coverage` | Call Wheelhouse `/in_market` + report |
| `POST` | `/property/<uid>/wheelhouse-fetch` | Fetch Wheelhouse recommendations → save to config |
| `POST` | `/property/<uid>/push` | Push computed prices to iGMS |

---

## Editor Tabs (per property)

### Tab 1 — Pricing
Fields:
- `base_price` (number)
- `min_price` (number)
- `max_price` (number)
- Seasonal multipliers: table of month → multiplier (high/shoulder/low or custom)
- DOW multipliers: table of Mon–Sun → multiplier
- Last-minute: window_days, discount, threshold_occupancy
- Far-future: window_days, discount

### Tab 2 — Availability
Fields:
- Default min stay (number)
- Min stay overrides: table with condition (DOW list OR month list OR event name) + min_nights
- Blocked check-in days (multi-select: Mon–Sun checkboxes)
- Blocked checkout days (multi-select)
- Same-day checkin: allowed (bool) + weekend exception with min_price
- Same-day checkout: allowed (bool)
- Gap handling: auto_block_gaps (bool), min_gap_nights (number), unblock_stale_days (number)
- Turn-day preferences: preferred_checkin / preferred_checkout (multi-select)

### Tab 3 — Wheelhouse
Fields:
- `enabled` (bool toggle)
- `room_type` (dropdown: house, cabin, apartment, etc.)
- `bedrooms`, `baths`, `sleeps` (number)
- `amenities` (multi-select/checkboxes from full amenity list)
- `min_price_floor` (number)
- `avg_booking_price` (number)
- `booking_price_certainty` (0.0–1.0 slider)

Also: "Test Coverage" button → calls `/in_market` → shows covered / not covered.
And: "Fetch Recommendations" button → calls `/recommendations` → shows sample prices.

### Tab 4 — Strategy Weights
Sliders for:
- `demand` (0.00–1.00)
- `event` (0.00–1.00)
- `competitor` (0.00–1.00)
- `yield` (0.00–1.00)

Auto-normalizes on save.

### Tab 5 — Manual Overrides
Table: date | price_override | availability_override | notes
- Add/edit/remove rows
- Overrides take precedence over computed prices

---

## Data Flow

```
Property JSON (config/properties/<uid>.json)
        ↑
   config_store.py
        ↑
   Flask app.py (POST /property/<uid>)

   Engine (engine.py) ← reads from config
        ↑
   Pricing preview (GET /property/<uid>/preview) → JSON table
        ↓
   iGMS client (client.py)
        ↓
   iGMS API (set_calendar_batch or propose_calendar_batch)

   Wheelhouse API ← wheelhouse_fetcher.py → saved into config as market_rates
```

---

## Wheelhouse Integration

When Wheelhouse is enabled for a property:

1. `GET /in_market?latitude=&longitude=` — check coverage first
2. If covered, `GET /recommendations` with property specs + `avg_booking_price` + `booking_price_certainty`
3. Response: array of `{ date, base_price, seasonality_dollar_value, local_event_dollar_value, temporality_dollar_value, restriction_dollar_value, total_price }`
4. Store `market_rates[date] = total_price` in the property config (or as a separate `market_data/` file keyed by date range)
5. Engine reads `market_rates` → feeds `CompetitorStrategy`

---

## Key Implementation Notes

- **Config persistence:** `config/properties/<uid>.json` is the source of truth. Web app reads/writes these files directly. No database needed.
- **Engine integration:** The `PricingEngine` class from `src/pricing_engine/engine.py` is imported and reused — no duplication.
- **Auth:** For now, web app is local-only (`localhost:8050`). No auth layer in Phase 1.
- **Min stay / blocked days:** These are applied as post-processing filters — the engine computes the price, then availability rules are checked and can override availability status independently.
- **Strategy weight normalization:** On save, weights are divided by their sum so they sum to 1.0.

---

## Phase Order

1. **Config schema** — define the full JSON structure in `docs/` (done in this plan)
2. **Property JSON files** — create `config/properties/` with one JSON per property
3. **config_store.py** — file read/write utilities for property configs
4. **Core pricing engine** — continue building/shipping this; it's the priority
5. **wheelhouse_fetcher.py** — Wheelhouse API client
6. **app.py + templates** — Flask app + HTML editor

Steps 1–5 are the core pricing tool. Step 6 (UI) is the bonus layer on top.