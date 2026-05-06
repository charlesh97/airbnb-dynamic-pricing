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

The `AvailabilityStrategy` (in `src/pricing_engine/strategies/availability.py`) evaluates whether a date is bookable and what the minimum stay requirement is.

### `AvailabilityResult` dataclass

```python
@dataclass
class AvailabilityResult:
    is_available: bool        # False if blocked
    min_stay: int             # Minimum nights for this date
    blocked_reason: str | None
    factors: dict[str, Any]
```

### Rules evaluated (in order)

1. **Blocked checkin days** — dates falling on a configured `checkin_days.blocked` DOW (e.g. `["wed"]`) are unavailable for arrival
2. **Blocked checkout days** — dates falling on a configured `checkout_days.blocked` DOW are unavailable for departure
3. **Same-day checkin** — blocked unless `same_day_checkin.allowed: true` or DOW is in `same_day_checkin.exception.dow`
4. **Gap nights** — if `gap_handling.auto_block_gaps: true`, isolated single nights between bookings are auto-blocked
5. **Min stay** — computed from `min_stay.default` + `min_stay.overrides` (DOW or month conditions)

### Configuration (property JSON)

```json
{
  "availability": {
    "min_stay": {
      "default": 2,
      "overrides": [
        {"when": {"dow": ["fri", "sat", "sun"]}, "min_nights": 3},
        {"when": {"months": [7, 8]}, "min_nights": 4}
      ]
    },
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
    min_stay: int = 2               # ← new
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
