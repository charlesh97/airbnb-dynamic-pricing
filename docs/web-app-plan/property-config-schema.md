# Property Config Schema

**File location:** `config/properties/<property_uid>.json`
**Source of truth** for all pricing and availability rules per property.

---

## Top-Level Fields

| Field | Type | Description |
|---|---|---|
| `property_uid` | `string` | iGMS property ID — must match filename |
| `name` | `string` | Human-readable name |
| `address` | `string` | Full address |
| `igms_listings` | `object` | Mapping of platform → listing ID (airbnb, vrbo, airgms) |
| `coordinates` | `object` | `{ latitude: number, longitude: number }` — for Wheelhouse |
| `wheelhouse` | `object` | Wheelhouse API config (see § Wheelhouse) |
| `pricing` | `object` | Pricing rules (see § Pricing) |
| `availability` | `object` | Availability rules (see § Availability) |
| `strategy_weights` | `object` | Per-strategy weights (demand/event/competitor/yield) |
| `manual_overrides` | `array` | Date-specific price/availability overrides |

---

## § Wheelhouse

```json
{
  "enabled": true,
  "room_type": "house",
  "bedrooms": 5,
  "baths": 3,
  "sleeps": 12,
  "amenities": ["parking", "air_conditioning", "hot_tub", "dryer", "washer", "wifi"],
  "cleaning_fee": 150.0,
  "security_deposit": 500.0,
  "guests_included": 8,
  "min_price_floor": 150.0,
  "avg_booking_price": 242.71,
  "booking_price_certainty": 0.7,
  "no_temporality": false
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `bool` | `false` | Whether to fetch Wheelhouse recommendations |
| `room_type` | `string` | — | One of: `apartment`, `bnb`, `cabin`, `house`, `holiday_home`, `hostel`, `hotel`, `camper`, etc. |
| `bedrooms` | `int` | — | Required by Wheelhouse |
| `baths` | `float` | — | Required by Wheelhouse |
| `sleeps` | `int` | — | Required by Wheelhouse |
| `amenities` | `string[]` | `[]` | From Wheelhouse amenity list |
| `cleaning_fee` | `float` | — | Passed to Wheelhouse |
| `security_deposit` | `float` | — | Passed to Wheelhouse |
| `guests_included` | `int` | — | Included in base rent |
| `min_price_floor` | `float` | `0` | Wheelhouse won't recommend below this |
| `avg_booking_price` | `float` | — | Historical average nightly price |
| `booking_price_certainty` | `float` | `0.5` | 0.0 = no certainty, 1.0 = full certainty |
| `no_temporality` | `bool` | `false` | Disable lead-time adjustments if true |

**Fetched at runtime → stored in config:**
- `market_rates: { "2026-05-15": 285.0, "2026-05-16": 310.0, ... }` — populated by `wheelhouse_fetcher.py`, not manually edited

---

## § Pricing

```json
{
  "base_price": 220.0,
  "min_price": 100.0,
  "max_price": 1200.0,

  "seasonal_multipliers": {
    "high":    { "months": [6, 7, 8, 12],    "multiplier": 1.35 },
    "shoulder": { "months": [3, 4, 9, 10],  "multiplier": 1.10 },
    "low":    { "months": [1, 2, 11],        "multiplier": 0.85 }
  },

  "dow_multipliers": {
    "mon": 0.90, "tue": 0.85, "wed": 0.88, "thu": 0.92,
    "fri": 1.20, "sat": 1.30, "sun": 1.05
  },

  "last_minute": {
    "window_days": 7,
    "discount": 0.92,
    "threshold_occupancy": 0.5
  },

  "far_future": {
    "window_days": 60,
    "discount": 0.90
  },

  "wheelhouse_weight": 0.40,

  "strategy_weights": {
    "demand":      0.30,
    "event":       0.20,
    "competitor":  0.40,
    "yield":       0.10
  }
}
```

| Field | Type | Description |
|---|---|---|
| `base_price` | `float` | Default nightly rate (USD) |
| `min_price` | `float` | Floor — engine will never recommend below this |
| `max_price` | `float` | Ceiling — engine will never recommend above this |
| `seasonal_multipliers` | `object` | Named season → months + multiplier |
| `dow_multipliers` | `object` | Day of week → multiplier (mon/tue/wed/thu/fri/sat/sun) |
| `last_minute` | `object` | `window_days`: how many days out to trigger; `discount`: multiplier applied; `threshold_occupancy`: only apply if occupancy below this |
| `far_future` | `object` | `window_days`: how many days out to trigger; `discount`: multiplier |
| `wheelhouse_weight` | `float` | How heavily to weight Wheelhouse `total_price` in the competitor strategy |
| `strategy_weights` | `object` | Per-strategy weights (demand/event/competitor/yield); auto-normalized on save |

---

## § Availability

```json
{
  "min_stay": {
    "default": 2,
    "overrides": [
      { "when": { "dow": ["fri", "sat", "sun"] }, "min_nights": 3 },
      { "when": { "months": [6, 7, 8, 12] }, "min_nights": 4 },
      { "when": { "event_names": ["graduation", "homecoming"] }, "min_nights": 5 }
    ]
  },

  "checkin_days": {
    "allowed": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    "blocked": []
  },

  "checkout_days": {
    "allowed": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    "blocked": ["wed", "thu"],
    "rationale": "Avoid mid-week cleanings"
  },

  "same_day_checkin": {
    "allowed": false,
    "exception": {
      "dow": ["fri", "sat"],
      "min_price": 350.0
    }
  },

  "same_day_checkout": {
    "allowed": false
  },

  "gap_handling": {
    "auto_block_gaps": true,
    "min_gap_nights": 1,
    "unblock_stale_days": 14
  },

  "turn_days": {
    "preferred_checkin": ["fri", "sat"],
    "preferred_checkout": ["mon", "fri", "sat"],
    "avoid_checkin": ["sun"]
  }
}
```

| Field | Type | Description |
|---|---|---|
| `min_stay.default` | `int` | Default minimum nights for any stay |
| `min_stay.overrides[].when` | `object` | Condition: `dow` (list), `months` (list), or `event_names` (list) |
| `min_stay.overrides[].min_nights` | `int` | Minimum nights for matching dates |
| `checkin_days.blocked` | `string[]` | Days check-in is not allowed |
| `checkout_days.blocked` | `string[]` | Days checkout is not allowed (e.g. Wed/Thu to avoid mid-week cleaning) |
| `same_day_checkin.allowed` | `bool` | Whether same-day check-in is permitted |
| `same_day_checkin.exception` | `object` | Allow same-day checkin on listed DOW only if price is above `min_price` |
| `same_day_checkout.allowed` | `bool` | Whether same-day checkout is permitted |
| `gap_handling.auto_block_gaps` | `bool` | Auto-block isolated nights between bookings |
| `gap_handling.unblock_stale_days` | `int` | After N days blocked with no booking, auto-unblock |
| `turn_days.preferred_checkin` | `string[]` | Preferred check-in days for cleaning crew scheduling |
| `turn_days.preferred_checkout` | `string[]` | Preferred checkout days |

**Override conflict resolution:** Later overrides in the array take precedence over earlier ones. The most specific rule wins.

---

## § Manual Overrides

```json
[
  {
    "date": "2026-05-20",
    "price_override": 350.0,
    "availability": "blocked",
    "notes": "Owner stay"
  },
  {
    "date": "2026-06-15",
    "price_override": null,
    "availability": "available",
    "notes": "Opened after painter done"
  }
]
```

Manual overrides take absolute precedence over computed prices and availability rules. Applied after the engine computes prices, before pushing to iGMS.

---

## Example: Freedom Place Config

```json
{
  "property_uid": "6925833560458409984",
  "name": "Cozy Modern Single Family 5br",
  "address": "3335 Freedom Place, Falls Church, VA 22041",
  "coordinates": {
    "latitude": 38.8915,
    "longitude": -77.2268
  },
  "igms_listings": {
    "airbnb": "645841896772032198_airbnb_209713065",
    "vrbo": "VL6BKKLZ9M_vrbo_VADO73KPBN"
  },
  "wheelhouse": {
    "enabled": true,
    "room_type": "house",
    "bedrooms": 5,
    "baths": 3,
    "sleeps": 12,
    "amenities": ["parking", "air_conditioning", "hot_tub", "dryer", "washer", "wifi", "kitchen"],
    "cleaning_fee": 200,
    "security_deposit": 500,
    "guests_included": 8,
    "min_price_floor": 150,
    "avg_booking_price": 242.71,
    "booking_price_certainty": 0.7,
    "no_temporality": false
  },
  "pricing": {
    "base_price": 220,
    "min_price": 100,
    "max_price": 1200,
    "seasonal_multipliers": {
      "high":    { "months": [6, 7, 8, 12], "multiplier": 1.35 },
      "shoulder": { "months": [3, 4, 9, 10], "multiplier": 1.10 },
      "low":     { "months": [1, 2, 11], "multiplier": 0.85 }
    },
    "dow_multipliers": {
      "mon": 0.90, "tue": 0.85, "wed": 0.88, "thu": 0.92,
      "fri": 1.20, "sat": 1.30, "sun": 1.05
    },
    "last_minute": { "window_days": 7, "discount": 0.92, "threshold_occupancy": 0.5 },
    "far_future": { "window_days": 60, "discount": 0.90 },
    "wheelhouse_weight": 0.40,
    "strategy_weights": { "demand": 0.30, "event": 0.20, "competitor": 0.40, "yield": 0.10 }
  },
  "availability": {
    "min_stay": {
      "default": 2,
      "overrides": [
        { "when": { "dow": ["fri", "sat", "sun"] }, "min_nights": 3 },
        { "when": { "months": [6, 7, 8, 12] }, "min_nights": 4 }
      ]
    },
    "checkin_days": { "allowed": ["mon","tue","wed","thu","fri","sat","sun"], "blocked": [] },
    "checkout_days": { "allowed": ["mon","tue","wed","thu","fri","sat","sun"], "blocked": ["wed","thu"], "rationale": "Avoid mid-week cleanings" },
    "same_day_checkin": { "allowed": false, "exception": { "dow": ["fri", "sat"], "min_price": 350 } },
    "same_day_checkout": { "allowed": false },
    "gap_handling": { "auto_block_gaps": true, "min_gap_nights": 1, "unblock_stale_days": 14 },
    "turn_days": { "preferred_checkin": ["fri","sat"], "preferred_checkout": ["mon","fri","sat"], "avoid_checkin": ["sun"] }
  },
  "manual_overrides": []
}
```