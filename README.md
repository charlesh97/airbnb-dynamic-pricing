# iGMS Dynamic Pricing Engine

Black-box dynamic pricing engine for short-term rental properties managed via iGMS.

## Features

- **4 pricing strategy algorithms**: demand-based, event/seasonal, competitor-adjusted, and yield/opportunity-cost
- **Weighted ensemble engine**: combine strategies with configurable per-property weights
- **iGMS API integration**: read calendar/booking data, push updated prices
- **CLI**: `status`, `run`, `dry-run`, `push`, and `schedule` commands
- **Test bench**: unit tests for every strategy and engine logic

## Quick Start

```bash
# 1. Clone / navigate
cd igms-dynamic-pricing

# 2. Install
pip install -e .

# 3. Configure — copy .env.example and fill in your IGMS credentials
cp .env.example .env

# 4. Test read access (shows current vs recommended prices)
igms-pricing status

# 5. Dry run — compute prices without writing
igms-pricing dry-run

# 6. Push prices to iGMS (requires pricing-management scope)
igms-pricing push
```

## Pricing Strategies

### 1. Demand-Based
Occupancy rate + booking velocity in trailing window → demand multiplier. Last-minute bookings with low occupancy get a small discount; high occupancy near date gets a premium.

### 2. Event / Seasonal
Date-keyed seasonal multipliers from a built-in calendar (holidays, summer peak, spring break). Fridays/Saturdays get a weekend premium. Fully configurable per date.

### 3. Competitor-Based
Takes a `market_median_price` from an external data source (scraped comps, PriceLabs, etc.) and adjusts by relative quality score.

### 4. Yield / Opportunity Cost
Most sophisticated — considers lead-time buckets (>30d, 14-30d, 7-14d, <7d), recent booking volume, and churn probability. Prices more aggressively when recent bookings are low.

## Architecture

```
igms-dynamic-pricing/
├── src/pricing_engine/
│   ├── config.py       # Pydantic config from env
│   ├── client.py       # IGMSClient extended with pricing write endpoints
│   ├── engine.py       # Weighted strategy runner
│   ├── scheduler.py   # Interval-based scheduler
│   ├── cli.py         # CLI commands
│   └── strategies/
│       ├── base.py     # Abstract PriceRecommendation + PricingStrategy
│       ├── demand.py
│       ├── event.py
│       ├── competitor.py
│       └── yield_.py
└── tests/
    ├── test_strategies.py   # Per-strategy unit tests
    ├── test_engine.py       # Engine weighted-average + bounds tests
    └── test_integration.py  # Live API (skipped if no token)
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `IGMS_ACCESS_TOKEN` | — | iGMS access token (required) |
| `IGMS_CLIENT_ID` | — | OAuth client ID |
| `IGMS_CLIENT_SECRET` | — | OAuth client secret |
| `IGMS_SCOPE` | `listings,pricing-management` | OAuth scopes |
| `PRICING_WINDOW_DAYS` | `90` | Days ahead to price |
| `SCHEDULE_INTERVAL_MINUTES` | `60` | Scheduler loop interval |
| `DEFAULT_BASE_PRICE` | `100.0` | Fallback base price (USD) |
| `DEFAULT_MIN_PRICE` | `50.0` | Floor price |
| `DEFAULT_MAX_PRICE` | `2000.0` | Ceiling price |
| `LOG_LEVEL` | `INFO` | Logging level |

Per-property overrides via env or config file:
```json
{
  "property_overrides": {
    "6925833560458409984": {
      "base_price": 180.0,
      "min_price": 75.0,
      "max_price": 1500.0,
      "quality_score": 0.90,
      "strategy_weights": {
        "demand": 0.35,
        "event": 0.30,
        "competitor": 0.25,
        "yield": 0.10
      }
    }
  }
}
```

## iGMS API Notes

- Base URL: `https://www.igms.com`
- Auth: `access_token` as query parameter (not Bearer header)
- Required scope for writes: `pricing-management`
- Calendar read: `GET /api/v1/get-calendar-data`
- Calendar write: **endpoint unconfirmed** — `PUT /api/v1/update-calendar-data` is the leading candidate; integration test will confirm

## ⚠️ Action Required from Charles

The current iGMS app is missing the `pricing-management` scope. To push prices:
1. Log into the iGMS developer portal
2. Edit your application
3. Add `pricing-management` to the scopes
4. Re-authorize / exchange a new token with the expanded scope

Without this scope, `igms-pricing push` will fail with a 401.

## Testing

```bash
# Unit tests
python -m pytest tests/ -v

# Integration test (requires live token)
IGMS_ACCESS_TOKEN=your_token python -m pytest tests/test_integration.py -v

# Test with a specific property
igms-pricing status --env .env
```
