"""Dashboard engine proxy — thin wrappers around the pricing engine + config store.

Adds repo src/ to sys.path, then exposes helper functions used by routes.
"""

from __future__ import annotations

import copy
import json
import logging
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
from pricing_engine.booking_adapter import fetch_bookings_for_window as _adapter_fetch

logger = logging.getLogger(__name__)

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
    # Support both wrapper variants:
    # - legacy client exposing set_access_token(...)
    # - current wrapper storing token on access_token attribute
    if hasattr(client, "set_access_token"):
        client.set_access_token(cfg.igms_access_token)
    else:
        client.access_token = cfg.igms_access_token
    token_len = len(cfg.igms_access_token or "")
    logger.info("iGMS client initialized; token_present=%s token_len=%d", bool(cfg.igms_access_token), token_len)
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
        logger.info(
            "iGMS get_calendar request property_uid=%s from=%s to=%s expanded_from=%s expanded_to=%s",
            property_uid, from_date, to_date, _from, _to
        )
        raw = client.get_calendar(
            property_uid=property_uid,
            from_date=_from,
            to_date=_to,
        )
        prices: dict[str, float] = {}
        entries = raw if isinstance(raw, list) else raw.get("data", [])
        logger.info("iGMS get_calendar response entries=%d property_uid=%s", len(entries), property_uid)
        for entry in entries:
            date = entry.get("date", "")
            price = entry.get("price")
            if date and price is not None:
                try:
                    prices[date] = float(price)
                except (TypeError, ValueError):
                    pass
        logger.info("iGMS parsed prices=%d property_uid=%s", len(prices), property_uid)
        return prices
    except Exception:
        logger.exception(
            "iGMS get_calendar failed property_uid=%s from=%s to=%s",
            property_uid, from_date, to_date
        )
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

    Delegates to pricing_engine.booking_adapter for normalized, paginated,
    property-filtered booking ingestion.
    """
    try:
        client = _get_pricing_client()
        bookings = _adapter_fetch(client, property_uid, from_date, to_date)
        logger.info("bookings fetch property_uid=%s window=%s–%s count=%d source=booking_adapter",
                     property_uid, from_date, to_date, len(bookings))
        return bookings or []
    except Exception:
        logger.exception("bookings fetch failed property_uid=%s window=%s–%s",
                         property_uid, from_date, to_date)
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

def _normalize_igms_properties(raw: Any) -> list[dict[str, Any]]:
    """Normalize iGMS properties response to a list of dicts."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    if isinstance(raw, dict):
        data = raw.get("data")
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
    return []


def _build_default_property_config(
    property_uid: str,
    name: str,
    state: str,
    igms_property: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a minimal config for a new property discovered from iGMS."""
    env_cfg = EngineConfig.from_env(_REPO_ROOT / ".env")
    igms_property = igms_property or {}

    listings = igms_property.get("listings")
    listing_uids = listings if isinstance(listings, list) else []
    location = igms_property.get("location") or {}
    lat = igms_property.get("latitude", location.get("lat", 0)) or 0
    lng = igms_property.get("longitude", location.get("lng", 0)) or 0

    return {
        "property_uid": property_uid,
        "name": name,
        "platforms": ["airbnb"],
        "listing_uids": listing_uids,
        "bedrooms": int(igms_property.get("bedrooms") or 0),
        "bathrooms": float(igms_property.get("bathrooms") or 0),
        "beds": int(igms_property.get("beds") or 0),
        "sleeps": int(igms_property.get("persons") or igms_property.get("sleeps") or 0),
        "latitude": float(lat),
        "longitude": float(lng),
        "base_price": env_cfg.default_base_price,
        "min_price": env_cfg.default_min_price,
        "max_price": env_cfg.default_max_price,
        "quality_score": env_cfg.default_quality_score,
        "strategy_weights": env_cfg.default_strategy_weights,
        "availability": {
            "booking_window_days": 120,
            "min_stay": {"default": 2, "overrides": []},
            "checkin_days": {"blocked": []},
            "checkout_days": {"blocked": []},
            "block_day_before": False,
            "block_day_after": False,
        },
        "seasonal_months": {f"{m:02d}": 1.0 for m in range(1, 13)},
        "dow_multipliers": {
            "mon": 1.0, "tue": 1.0, "wed": 1.0, "thu": 1.0, "fri": 1.1, "sat": 1.1, "sun": 1.0,
        },
        "local_events": [],
        "local_events_config": {},
        "demand_config": {
            "demand_window_days": 14,
            "velocity_window_days": 7,
            "velocity_factor": 0.15,
            "occupancy_factor": 0.3,
            "far_future": {"window_days": 60, "discount": 0.9},
            "last_minute": {"window_days": 7, "discount": 0.92},
        },
        "holiday_buffer_days": 3,
        "state": state or "CA",
        "holiday_buffer_slope": 0.05,
    }


def _upsert_discovered_property_config(igms_property: dict[str, Any]) -> dict[str, Any] | None:
    """Ensure a property config exists for a discovered iGMS property."""
    property_uid = str(igms_property.get("property_uid") or "").strip()
    if not property_uid:
        return None

    name = str(igms_property.get("name") or f"Property {property_uid}").strip()
    location = igms_property.get("location") or {}
    state = str(igms_property.get("state") or location.get("state") or "CA").strip() or "CA"

    existing = _CONFIG_STORE.load(property_uid)
    if not existing:
        created = _build_default_property_config(property_uid, name, state, igms_property)
        _CONFIG_STORE.save(property_uid, created)
        logger.info("Created missing property config for discovered property_uid=%s name=%s", property_uid, name)
        return created

    changed = False
    if existing.get("property_uid") != property_uid:
        existing["property_uid"] = property_uid
        changed = True
    if name and existing.get("name") != name:
        existing["name"] = name
        changed = True
    if state and existing.get("state") != state:
        existing["state"] = state
        changed = True
    if isinstance(igms_property.get("listings"), list) and existing.get("listing_uids") in (None, [], {}):
        existing["listing_uids"] = igms_property.get("listings")
        changed = True

    if changed:
        _CONFIG_STORE.save(property_uid, existing)
        logger.info("Updated property config metadata for property_uid=%s", property_uid)
    return existing


def get_properties() -> list[dict]:
    """Return all locally-managed properties as {property_uid, name, state}.

    Reads only config/properties/*.json files. Does NOT call iGMS.
    Sorts by name then uid.
    """
    discovered: dict[str, dict[str, Any]] = {}

    for fname in sorted(Path(_CONFIG_STORE.config_dir).glob("*.json")):
        try:
            d = json.loads(fname.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Skipping unreadable property config: %s", fname)
            continue
        uid = str(d.get("property_uid") or fname.stem).strip()
        if not uid:
            continue
        discovered[uid] = {
            "property_uid": uid,
            "name": str(d.get("name") or f"Property {uid}"),
            "state": str(d.get("state") or "CA"),
        }

    return sorted(discovered.values(), key=lambda p: (p.get("name", "").lower(), p.get("property_uid", "")))


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

    is_booking_window_closed = getattr(avail, "blocked_reason", None) == "booking_window_closed"
    has_live_price = current is not None and current > 0
    has_proposed_change = has_live_price and delta is not None and abs(delta) >= 0.01
    if is_booking_window_closed:
        live_price_status = "closed"
    else:
        live_price_status = "ok" if has_live_price else "missing"

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
        "live_price_status": live_price_status,
        "has_proposed_change": has_proposed_change,
    }


def _build_adjustment_ladder(af: dict, base_price: float) -> list[dict]:
    """Build adjustment ladder from all_factors dict.

    Each entry: {key, label, amount (signed delta), running_total_after}
    Ordered as applied: seasonality → dow → demand → yield → competitor.
    """
    ladder = []
    running = base_price

    ev = af.get("event", {})
    dm = af.get("demand", {})
    yt = af.get("yield", {})
    co = af.get("competitor", {})

    seasonal_mult = ev.get("seasonal_multiplier", 1.0)
    seasonal_amt = (seasonal_mult - 1.0) * base_price
    if abs(seasonal_amt) >= 0.01:
        running += seasonal_amt
        ladder.append({
            "key": "seasonality",
            "label": "Seasonality",
            "amount": round(seasonal_amt, 2),
            "running_total_after": round(running, 2),
        })

    dow_mult = ev.get("dow_multiplier", 1.0)
    dow_amt = (dow_mult - 1.0) * base_price
    if abs(dow_amt) >= 0.01:
        running += dow_amt
        ladder.append({
            "key": "dow",
            "label": "Day-of-week",
            "amount": round(dow_amt, 2),
            "running_total_after": round(running, 2),
        })

    demand_mult = dm.get("demand_multiplier", 1.0)
    demand_amt = (demand_mult - 1.0) * base_price
    if abs(demand_amt) >= 0.01:
        running += demand_amt
        ladder.append({
            "key": "demand",
            "label": "Demand",
            "amount": round(demand_amt, 2),
            "running_total_after": round(running, 2),
        })

    yt_mult = yt.get("final_multiplier", yt.get("lead_factor", 1.0))
    yield_amt = (yt_mult - 1.0) * base_price
    if abs(yield_amt) >= 0.01:
        running += yield_amt
        ladder.append({
            "key": "yield",
            "label": "Yield",
            "amount": round(yield_amt, 2),
            "running_total_after": round(running, 2),
        })

    co_mult = 1.0 if co.get("status") == "disabled" else co.get("adjustment_factor", 1.0)
    competitor_amt = (co_mult - 1.0) * base_price
    if abs(competitor_amt) >= 0.01:
        running += competitor_amt
        ladder.append({
            "key": "competitor",
            "label": "Competitor",
            "amount": round(competitor_amt, 2),
            "running_total_after": round(running, 2),
        })

    return ladder


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

    base_price_val = config.get("base_price", 200.0)
    ladder = _build_adjustment_ladder(af, base_price_val)
    subtotal_before_blend = ladder[-1]["running_total_after"] if ladder else base_price_val
    blend_adjustment_amount = round(dp.final_price - subtotal_before_blend, 2)

    is_booking_window_closed = getattr(avail, "blocked_reason", None) == "booking_window_closed"
    has_live_price = current is not None and current > 0
    has_proposed_change = has_live_price and delta is not None and abs(delta) >= 0.01
    if is_booking_window_closed:
        live_price_status = "closed"
    else:
        live_price_status = "ok" if has_live_price else "missing"

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
        "base_rate": base_price_val,
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
        },
        "strategy_prices": dp.strategy_prices,
        "raw_factors": af,
        "adjustment_ladder": ladder,
        "subtotal_before_blend": round(subtotal_before_blend, 2),
        "blend_adjustment_amount": blend_adjustment_amount,
        "final_recommended": dp.final_price,
        "current_igms_price": current,
        "live_price_status": live_price_status,
        "has_proposed_change": has_proposed_change,
    }
