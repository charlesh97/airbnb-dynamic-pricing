"""Dashboard engine proxy — thin wrappers around the pricing engine + config store.

Adds repo src/ to sys.path, then exposes helper functions used by routes.
"""

from __future__ import annotations

import copy
import json
import sys
from calendar import monthrange
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Make repo src/ importable
_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from pricing_engine.engine import PricingEngine, DatePrice
from pricing_engine.config_store import PropertyConfigStore
from pricing_engine.config import EngineConfig
from pricing_engine.client import PricingClient

# ── Auto-holidays ─────────────────────────────────────────────────────────────

import holidays  # noqa: E402


def get_holiday_events(year: int, state: str = "CA") -> list[dict]:
    """Return US federal + state holidays as local_events format."""
    us = holidays.US(years=year)
    events = []
    # Try state-specific holidays (CA, VA, TX, etc.)
    try:
        state_holidays = holidays.US(state=state, years=year)
        combined = {**us, **state_holidays}
    except Exception:
        combined = {**us}
    for d, name in combined.items():
        mmdd = d.strftime("%m-%d")
        events.append({
            "name": name,
            "date": d.strftime("%Y-%m-%d"),
            "mm-dd": mmdd,
            "factor": 1.10,
            "source": "auto",
        })
    return events


def _inject_auto_holidays(config: dict, year: int) -> dict:
    """Return a deep copy of config with auto-holidays merged into local_events."""
    cfg = copy.deepcopy(config)
    state = config.get("state", "CA")
    auto_events = get_holiday_events(year, state)
    # Build key from mm-dd field, falling back to date field for existing user events
    existing = {}
    for e in cfg.get("local_events", []):
        key = e.get("mm-dd") or (e.get("date", "")[5:] if e.get("date") else "")
        if key:
            existing[key] = e
    for evt in auto_events:
        if evt["mm-dd"] not in existing:
            existing[evt["mm-dd"]] = evt
    cfg["local_events"] = list(existing.values())
    return cfg


# ── PricingClient for live iGMS prices ──────────────────────────────────────

def _get_pricing_client() -> PricingClient:
    """Return an authenticated PricingClient from env vars."""
    cfg = EngineConfig.from_env(_REPO_ROOT / ".env")
    client = PricingClient()
    client.set_access_token(cfg.igms_access_token)
    return client


# ── Live iGMS price fetching ─────────────────────────────────────────────────

_LISTING_UID = "1221946578233906682_airbnb_209713065"


def get_calendar_with_live_prices(
    property_uid: str,
    from_date: str,
    to_date: str,
) -> dict[str, float]:
    """Fetch actual Airbnb nightly prices from iGMS for a date range.

    Returns a dict mapping date strings (YYYY-MM-DD) → nightly price (float).
    Returns an empty dict on any error (non-fatal for the dashboard).

    For single-day fetches (from_date == to_date), expands to the full month
    to work around an iGMS quirk where single-day ranges return empty results.
    """
    try:
        # Single-day workaround: expand to full month
        _from = from_date
        _to = to_date
        if from_date == to_date:
            y, m, _ = from_date.split("-")
            _, last_day = monthrange(int(y), int(m))
            _from = f"{y}-{m}-01"
            _to = f"{y}-{m}-{last_day:02d}"

        client = _get_pricing_client()
        raw = client.get_calendar(
            property_uid=property_uid,
            from_date=_from,
            to_date=_to,
        )
        prices: dict[str, float] = {}
        entries = raw if isinstance(raw, list) else raw.get("data", [])
        for entry in entries:
            date = entry.get("date", "")
            price = entry.get("price")
            if date and price is not None:
                try:
                    prices[date] = float(price)
                except (TypeError, ValueError):
                    pass
        return prices
    except Exception:
        # Non-fatal: live prices are nice-to-have, not required
        return {}


# ── PricingEngine helpers ─────────────────────────────────────────────────────

_ENGINE = PricingEngine()
_CONFIG_STORE = PropertyConfigStore()


def compute_month(
    property_uid: str,
    year: int,
    month: int,
    calendar_data: list[dict[str, Any]] | None = None,
    bookings_in_window: list[dict[str, Any]] | None = None,
    airbnb_prices: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Compute prices for all days in a month. Returns a list of day dicts."""
    _, n_days = monthrange(year, month)
    from_date = f"{year:04d}-{month:02d}-01"
    to_date = f"{year:04d}-{month:02d}-{n_days:02d}"

    # Use the same merged config as CLI so prices match the REPL/engine directly
    env_cfg = EngineConfig.from_env(_REPO_ROOT / ".env")
    merged = _CONFIG_STORE.merge_with_env_defaults(property_uid, env_cfg.__dict__)
    config = _inject_auto_holidays(merged, year)
    calendar_data = calendar_data or []
    # Fetch real bookings so availability rules (block day before/after, gap fill, etc.) work
    # Note: IGMS get_bookings returns bookings across ALL properties in the date range,
    # so we fetch without property_uid filter and pass through — the availability
    # strategy filters by property_uid when checking day-before/after logic.
    if bookings_in_window is None or len(bookings_in_window) == 0:
        bookings_in_window = _fetch_bookings_for_window(property_uid, from_date, to_date)
    else:
        bookings_in_window = bookings_in_window or []

    results = _ENGINE.compute_range(
        property_uid=property_uid,
        from_date=from_date,
        to_date=to_date,
        calendar_data=calendar_data,
        bookings_in_window=bookings_in_window,
        config=config,
        property_override=None,
    )

    days = []
    for dp in results:
        date_str = dp.date
        current_price = None
        if airbnb_prices and date_str in airbnb_prices:
            current_price = airbnb_prices[date_str]

        avail = _ENGINE.compute_availability(
            property_uid=property_uid,
            date=date_str,
            calendar_entry=None,
            bookings_in_window=bookings_in_window,
            config=config,
        )

        days.append(_date_price_to_dict(dp, current_price, avail, config))

    return days


def _fetch_bookings_for_window(
    property_uid: str,
    from_date: str,
    to_date: str,
) -> list[dict[str, Any]]:
    """Fetch accepted bookings from iGMS for a property and date window.
    
    Note: IGMS returns bookings across all properties when filtering by date range,
    so we fetch all and rely on property_uid filtering in the availability strategy.
    """
    try:
        client = _get_pricing_client()
        resp = client.get_bookings(
            page=1,
            start_date=from_date,
            end_date=to_date,
        )
        data = resp.get("data", []) if resp else []
        # Only include accepted/confirmed bookings
        return [b for b in data if b.get("booking_status") in ("accepted", "confirmed")]
    except Exception:
        return []


def get_day_detail(
    property_uid: str,
    date: str,
    calendar_data: list[dict[str, Any]] | None = None,
    bookings_in_window: list[dict[str, Any]] | None = None,
    airbnb_price: float | None = None,
) -> dict[str, Any]:
    """Return full factor breakdown for a single day."""
    year = int(date.split("-")[0])
    # Use the same merged config as CLI so prices match the REPL/engine directly
    env_cfg = EngineConfig.from_env(_REPO_ROOT / ".env")
    merged = _CONFIG_STORE.merge_with_env_defaults(property_uid, env_cfg.__dict__)
    config = _inject_auto_holidays(merged, year)
    calendar_data = calendar_data or []

    # Fetch real bookings so availability rules can use them
    if bookings_in_window is None:
        month_start = date[:7] + "-01"
        # Get last day of month
        _, last = monthrange(year, int(date[5:7]))
        month_end = f"{date[:7]}-{last:02d}"
        bookings_in_window = _fetch_bookings_for_window(property_uid, month_start, month_end)
    else:
        bookings_in_window = bookings_in_window or []

    dp = _ENGINE.compute_price(
        property_uid=property_uid,
        date=date,
        calendar_entry=None,
        bookings_in_window=bookings_in_window,
        config=config,
        property_override=None,
    )

    avail = _ENGINE.compute_availability(
        property_uid=property_uid,
        date=date,
        calendar_entry=None,
        bookings_in_window=bookings_in_window,
        config=config,
    )

    return _date_price_to_detail(dp, airbnb_price, avail, config)


# ── Config helpers ────────────────────────────────────────────────────────────

def get_property_config(property_uid: str) -> dict[str, Any]:
    """Load property config JSON from disk."""
    return _CONFIG_STORE.load(property_uid)


def save_property_config(property_uid: str, config: dict[str, Any]) -> None:
    """Save property config JSON to disk."""
    _CONFIG_STORE.save(property_uid, config)


# ── Properties list ────────────────────────────────────────────────────────────

def get_properties() -> list[dict]:
    """Return all property configs as a list of {property_uid, name, state} dicts."""
    store = PropertyConfigStore()
    props = []
    for fname in sorted(Path(store.config_dir).glob("*.json")):
        with open(fname) as f:
            d = json.load(f)
        props.append({
            "property_uid": d.get("property_uid"),
            "name": d.get("name", "Unnamed"),
            "state": d.get("state", "CA"),
        })
    return props


# ── Response builders ─────────────────────────────────────────────────────────

def _date_price_to_dict(
    dp: DatePrice,
    current_airbnb_price: float | None,
    avail,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Map DatePrice + availability → calendar grid day dict."""
    final = dp.final_price
    current = current_airbnb_price
    if current is not None and current > 0:
        delta = final - current
        delta_pct = delta / current
    else:
        delta = None
        delta_pct = None

    if delta_pct is not None:
        if abs(delta_pct) <= 0.10:
            match = "close"
        elif delta_pct < -0.10:
            match = "undersell"
        else:
            match = "oversell"
    else:
        match = None

    # Holiday indicator + name
    is_holiday = False
    holiday_name = None
    if config:
        date_str = dp.date
        mmdd = date_str[5:]  # "MM-DD"
        for evt in config.get("local_events", []):
            if evt.get("source") == "auto" and evt.get("mm-dd") == mmdd:
                is_holiday = True
                holiday_name = evt.get("name")
                break

    af = dp.all_factors

    return {
        "date": dp.date,
        "final_price": dp.final_price,
        "current_airbnb_price": current,
        "price_delta": delta,
        "price_delta_pct": delta_pct,
        "match_status": match,
        "is_available": avail.is_available,
        "min_stay": avail.min_stay,
        "blocked_reason": avail.blocked_reason,
        "confidence": dp.confidence,
        "is_holiday": is_holiday,
        "holiday_name": holiday_name,
        "holiday_proximity": af.get("event", {}).get("holiday_proximity") if af else None,
    }


def _date_price_to_detail(
    dp: DatePrice,
    current_airbnb_price: float | None,
    avail,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Map DatePrice + availability → full day detail dict."""
    final = dp.final_price
    current = current_airbnb_price

    if current is not None and current > 0:
        delta = final - current
        delta_pct = delta / current
    else:
        delta = None
        delta_pct = None

    if delta_pct is not None:
        if abs(delta_pct) <= 0.10:
            match = "close"
        elif delta_pct < -0.10:
            match = "undersell"
        else:
            match = "oversell"
    else:
        match = None

    af = dp.all_factors

    # --- seasonal (from event strategy factors) ---
    ev = af.get("event", {})
    seasonal_mult = ev.get("seasonal_multiplier", 1.0)
    dow_mult = ev.get("dow_multiplier", 1.0)
    seasonal_rule = ev.get("local_event_applied") and "local_event" or (
        "seasonal" if seasonal_mult != 1.0 else "base"
    )
    seasonal_detail = ev.get("local_event_applied") or ""

    # --- demand ---
    dm = af.get("demand", {})

    # --- strategy weights (from Frosty Pines config) ---
    weights = dp.strategy_weights

    # booking window days from availability config
    avail_cfg = config.get("availability", {})
    bwd = avail_cfg.get("booking_window_days", 120)

    return {
        "date": dp.date,
        "property_uid": dp.property_uid,
        "final_price": dp.final_price,
        "current_airbnb_price": current,
        "confidence": dp.confidence,
        "is_available": avail.is_available,
        "min_stay": avail.min_stay,
        "blocked_reason": avail.blocked_reason,
        "booking_window_days": bwd,
        "match_status": match,
        "base_rate": config.get("base_price", 200.0),
        "seasonal": {
            "rule": seasonal_rule,
            "detail": seasonal_detail,
            "multiplier": ev.get("seasonal_multiplier", 1.0),
            "dow": ev.get("dow", ""),
            "dow_multiplier": ev.get("dow_multiplier", 1.0),
            "raw_seasonal_multiplier": ev.get("seasonal_multiplier", 1.0),
            "effective_seasonal": round(seasonal_mult * dow_mult, 3),
        },
        "demand": {
            "multiplier": dm.get("demand_multiplier", 1.0),
            "occupancy": {
                "value": dm.get("occupancy_rate", 0.0),
                "window_days": config.get("demand_config", {}).get("demand_window_days", 14),
                "factor": config.get("demand_config", {}).get("occupancy_factor", 0.3),
                "contribution": f"Occupancy {dm.get('occupancy_rate', 0):.0%}",
            },
            "velocity": {
                "value": dm.get("bookings_per_day", 0.0),
                "window_days": config.get("demand_config", {}).get("velocity_window_days", 7),
                "factor": config.get("demand_config", {}).get("velocity_factor", 0.15),
                "contribution": f"Velocity {dm.get('bookings_per_day', 0):.2f}/day",
            },
            "far_future": {
                "discount": config.get("demand_config", {}).get("far_future", {}).get("discount", 0.9),
                "window_days": config.get("demand_config", {}).get("far_future", {}).get("window_days", 60),
                "active": dm.get("far_future_discount_applied", False),
            },
            "last_minute": {
                "discount": config.get("demand_config", {}).get("last_minute", {}).get("discount", 0.92),
                "window_days": config.get("demand_config", {}).get("last_minute", {}).get("window_days", 7),
                "threshold_occupancy": config.get("demand_config", {}).get("last_minute", {}).get("threshold_occupancy", 0.5),
                "active": dm.get("last_minute_applied", False),
            },
        },
        "event": {
            "suggested_price": dp.strategy_prices.get("event"),
            "factors": {
                "local_event": ev.get("local_event_applied"),
                "event_factor": ev.get("local_event_applied"),
                "holiday_proximity": ev.get("holiday_proximity"),
            },
        },
        "yield": {
            "suggested_price": dp.strategy_prices.get("yield"),
            "factors": {
                "yield_score": af.get("yield", {}).get("yield_score", None),
                "recent_booking_value": af.get("yield", {}).get("recent_bookings_avg", None),
            },
        },
        "competitor": {
            "suggested_price": dp.strategy_prices.get("competitor"),
            "factors": af.get("competitor", {}),
            "note": af.get("competitor", {}).get("note", ""),
        },
        "strategy_weights": {
            "demand": weights.get("demand", 0),
            "event": weights.get("event", 0),
            "competitor": weights.get("competitor", 0),
            "yield": weights.get("yield", 0),
            # weather removed from codebase
        },
        "strategy_prices": dp.strategy_prices,
        "raw_factors": af,
    }
