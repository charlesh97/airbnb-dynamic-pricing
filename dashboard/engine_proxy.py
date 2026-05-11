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
from pricing_engine.strategies.demand import get_pricing_adjustments_config

logger = logging.getLogger(__name__)


def _has_effective_price_change(proposed: float | None, live: float | None) -> bool:
    """True when proposed and live differ by at least one cent."""
    if proposed is None or live is None:
        return False
    try:
        return abs(round((float(proposed) - float(live)) * 100.0)) >= 1
    except (TypeError, ValueError):
        return False

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
_BOOKINGS_CACHE: dict[str, dict[str, Any]] = {}


def clear_bookings_cache(property_uid: str | None = None) -> None:
    """Clear cached booking payloads.

    If property_uid is provided, clears only that property cache entry.
    Otherwise clears the full cache.
    """
    if property_uid:
        _BOOKINGS_CACHE.pop(str(property_uid), None)
        logger.info("bookings cache invalidated property_uid=%s", property_uid)
        return
    _BOOKINGS_CACHE.clear()
    logger.info("bookings cache invalidated all properties")


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
    config = _CONFIG_STORE.merge_with_env_defaults(property_uid, env_cfg.__dict__)
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
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """Fetch accepted bookings from iGMS for a property and date window.

    Delegates to pricing_engine.booking_adapter for normalized, paginated,
    property-filtered booking ingestion.
    """
    try:
        requested_from = datetime.strptime(from_date, "%Y-%m-%d")
        requested_to = datetime.strptime(to_date, "%Y-%m-%d")
    except ValueError:
        requested_from = datetime.now()
        requested_to = datetime.now()

    # Velocity needs booking creation activity across the configured booking window,
    # not only the visible month range. We widen the stay-date fetch window so recent
    # bookings for later stays are available for created_dttm counting.
    env_cfg = EngineConfig.from_env(_REPO_ROOT / ".env")
    merged = _CONFIG_STORE.merge_with_env_defaults(property_uid, env_cfg.__dict__)
    availability_cfg = merged.get("availability", {}) or {}
    booking_window_days = int(availability_cfg.get("booking_window_days", 120) or 120)
    booking_window_days = max(1, booking_window_days)

    adjustments_cfg = get_pricing_adjustments_config(merged)
    occ_window_days = int(adjustments_cfg.get("occupancy_pacing", {}).get("window_days", 14) or 14)
    last_minute_cfg = adjustments_cfg.get("last_minute", {}) or {}
    last_minute_window_days = int(last_minute_cfg.get("window_days", 7) or 7)
    lookback_days = max(occ_window_days, last_minute_window_days, 14)

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    booking_window_end = today + timedelta(days=booking_window_days)
    widened_from_dt = min(requested_from, today - timedelta(days=lookback_days))
    widened_to_dt = max(requested_to, booking_window_end)
    widened_from = widened_from_dt.strftime("%Y-%m-%d")
    widened_to = widened_to_dt.strftime("%Y-%m-%d")

    try:
        client = _get_pricing_client()
        cache_key = str(property_uid)
        cache_entry = _BOOKINGS_CACHE.get(cache_key)
        if not force_refresh and cache_entry:
            cached_from = str(cache_entry.get("from_date", ""))
            cached_to = str(cache_entry.get("to_date", ""))
            if cached_from and cached_to and cached_from <= widened_from and cached_to >= widened_to:
                cached_rows = cache_entry.get("bookings", []) or []
                logger.info(
                    "bookings cache hit property_uid=%s requested=%s–%s widened=%s–%s cached=%s–%s count=%d",
                    property_uid,
                    from_date,
                    to_date,
                    widened_from,
                    widened_to,
                    cached_from,
                    cached_to,
                    len(cached_rows),
                )
                return cached_rows

        bookings = _adapter_fetch(client, property_uid, widened_from, widened_to)
        _BOOKINGS_CACHE[cache_key] = {
            "from_date": widened_from,
            "to_date": widened_to,
            "bookings": bookings or [],
            "cached_at": datetime.now().isoformat(),
        }
        logger.info(
            "bookings fetch property_uid=%s requested=%s–%s widened=%s–%s "
            "booking_window_days=%d lookback_days=%d count=%d source=booking_adapter force_refresh=%s",
            property_uid,
            from_date,
            to_date,
            widened_from,
            widened_to,
            booking_window_days,
            lookback_days,
            len(bookings),
            force_refresh,
        )
        return bookings or []
    except Exception:
        logger.exception(
            "bookings fetch failed property_uid=%s requested_window=%s–%s",
            property_uid,
            from_date,
            to_date,
        )
        return []


def _fetch_bookings_for_display_window(
    property_uid: str,
    from_date: str,
    to_date: str,
) -> list[dict[str, Any]]:
    """Fetch bookings strictly for the requested visible window.

    This is used by calendar UI span rendering. Unlike
    ``_fetch_bookings_for_window``, it does not widen the date range because
    wider queries can be truncated by the upstream API and hide in-month stays.
    """
    try:
        client = _get_pricing_client()
        bookings = _adapter_fetch(client, property_uid, from_date, to_date)
        logger.info(
            "bookings display fetch property_uid=%s window=%s–%s count=%d source=booking_adapter",
            property_uid,
            from_date,
            to_date,
            len(bookings or []),
        )
        return bookings or []
    except Exception:
        logger.exception(
            "bookings display fetch failed property_uid=%s window=%s–%s",
            property_uid,
            from_date,
            to_date,
        )
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
    config = _CONFIG_STORE.merge_with_env_defaults(property_uid, env_cfg.__dict__)
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

    af = dp.all_factors
    event_factors = af.get("event", {}) if isinstance(af, dict) else {}
    is_holiday = bool(event_factors.get("is_holiday", False)) and not bool(
        event_factors.get("holiday_buffer_applied", False)
    )
    holiday_name = event_factors.get("holiday_name")

    is_booking_window_closed = getattr(avail, "blocked_reason", None) == "booking_window_closed"
    has_live_price = current is not None and current > 0
    has_proposed_change = has_live_price and _has_effective_price_change(final, current)
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
        "holiday_proximity": {
            "source": event_factors.get("holiday_source"),
            "holiday_name": event_factors.get("holiday_name"),
            "buffer_applied": event_factors.get("holiday_buffer_applied"),
        },
        "live_price_status": live_price_status,
        "has_proposed_change": has_proposed_change,
    }


def _build_adjustment_ladder(af: dict, base_price: float) -> list[dict]:
    """Build adjustment ladder from additive explanation components."""
    ex = af.get("explanation", {}) or {}
    rows: list[dict] = []
    for comp in ex.get("components", []) or []:
        if not isinstance(comp, dict):
            continue
        rows.append(
            {
                "key": str(comp.get("key", "component")),
                "label": str(comp.get("label", comp.get("key", "Component"))),
                "amount": round(float(comp.get("amount", 0.0) or 0.0), 2),
                "running_total_after": round(float(comp.get("running_subtotal", base_price) or base_price), 2),
            }
        )

    adjust_amt = float(ex.get("price_adjust_amount", 0.0) or 0.0)
    if abs(adjust_amt) >= 0.01:
        rows.append(
            {
                "key": "price_adjust",
                "label": "Global Price Adjust",
                "amount": round(adjust_amt, 2),
                "running_total_after": round(float(ex.get("raw_adjusted_price", base_price) or base_price), 2),
            }
        )
    return rows


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
    seasonal_mult = float(ev.get("seasonality_multiplier", 1.0) or 1.0)
    dow_mult = float(ev.get("dow_multiplier", 1.0) or 1.0)
    event_mult = float(ev.get("event_multiplier", seasonal_mult * dow_mult) or (seasonal_mult * dow_mult))
    if ev.get("local_event_applied"):
        seasonal_rule = "local_event"
    elif ev.get("is_holiday"):
        seasonal_rule = "holiday"
    elif abs(float(ev.get("seasonality_pct", 0.0) or 0.0)) >= 0.001:
        seasonal_rule = "seasonal"
    else:
        seasonal_rule = "base"
    seasonal_detail = ev.get("local_event_applied") or ev.get("holiday_name") or ""

    # --- demand debug ---
    dm = af.get("demand", {}) or {}
    occ_dbg = dm.get("occupancy_pacing", {}) or {}
    vel_dbg = dm.get("booking_velocity", {}) or {}

    # booking window days from availability config
    avail_cfg = config.get("availability", {})
    bwd = avail_cfg.get("booking_window_days", 120)

    ex = af.get("explanation", {}) or {}
    base_price_val = float(ex.get("base_price", config.get("base_price", 200.0)))
    starting_price = float(ex.get("starting_price", base_price_val))
    price_after_occupancy = float(ex.get("price_after_occupancy", starting_price))
    price_after_velocity = float(ex.get("price_after_velocity", price_after_occupancy))
    raw_adjusted_price = float(ex.get("raw_adjusted_price", dp.final_price))
    min_price_bound = float(ex.get("min_price", dp.final_price))
    max_price_bound = float(ex.get("max_price", dp.final_price))
    ladder = _build_adjustment_ladder(af, base_price_val)
    blend_adjustment_amount = round(dp.final_price - raw_adjusted_price, 2)
    was_price_capped = abs(blend_adjustment_amount) >= 0.01
    cap_type = None
    if was_price_capped:
        if raw_adjusted_price > max_price_bound and abs(dp.final_price - max_price_bound) < 0.01:
            cap_type = "max"
        elif raw_adjusted_price < min_price_bound and abs(dp.final_price - min_price_bound) < 0.01:
            cap_type = "min"
        else:
            cap_type = "unknown"

    is_booking_window_closed = getattr(avail, "blocked_reason", None) == "booking_window_closed"
    has_live_price = current is not None and current > 0
    has_proposed_change = has_live_price and _has_effective_price_change(final, current)
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
            "multiplier": event_mult,
            "dow": ev.get("dow", ""),
            "dow_multiplier": dow_mult,
            "raw_seasonal_multiplier": seasonal_mult,
            "effective_seasonal": round(event_mult, 3),
        },
        "starting_price": round(starting_price, 2),
        "demand": {
            "multiplier": dm.get("demand_multiplier", 1.0),
            "occupancy_pacing": {
                "multiplier": occ_dbg.get("multiplier", 1.0),
                "reason": occ_dbg.get("reason", "n/a"),
                "inputs": occ_dbg.get("inputs", {}),
                "computed": occ_dbg.get("computed", {}),
                "price_after": round(price_after_occupancy, 2),
            },
            "booking_velocity": {
                "multiplier": vel_dbg.get("multiplier", 1.0),
                "reason": vel_dbg.get("reason", "n/a"),
                "inputs": vel_dbg.get("inputs", {}),
                "computed": vel_dbg.get("computed", {}),
                "price_after": round(price_after_velocity, 2),
            },
            "price_after_occupancy": round(price_after_occupancy, 2),
            "price_after_velocity": round(price_after_velocity, 2),
        },
        "event": {
            "suggested_price": dp.strategy_prices.get("event"),
            "factors": {
                "local_event": ev.get("local_event_applied"),
                "event_factor": event_mult,
                "holiday_proximity": {
                    "source": ev.get("holiday_source"),
                    "holiday_name": ev.get("holiday_name"),
                    "buffer_applied": ev.get("holiday_buffer_applied"),
                },
            },
        },
        "competitor": {
            "suggested_price": dp.strategy_prices.get("competitor"),
            "factors": af.get("competitor", {}),
            "note": af.get("competitor", {}).get("note", ""),
        },
        "strategy_prices": dp.strategy_prices,
        "raw_factors": af,
        "adjustment_ladder": ladder,
        "subtotal_before_blend": round(float(ex.get("subtotal_before_adjust", raw_adjusted_price)), 2),
        "blend_adjustment_amount": blend_adjustment_amount,
        "was_price_capped": was_price_capped,
        "cap_type": cap_type,
        "min_price_bound": round(min_price_bound, 2),
        "max_price_bound": round(max_price_bound, 2),
        "raw_adjusted_price": round(raw_adjusted_price, 2),
        "final_recommended": dp.final_price,
        "current_igms_price": current,
        "live_price_status": live_price_status,
        "has_proposed_change": has_proposed_change,
    }
