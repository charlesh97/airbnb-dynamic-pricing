from __future__ import annotations

from datetime import datetime, timezone
import logging
from fastapi import APIRouter, Query

from typing import Any
from pathlib import Path
from ..engine_proxy import (
    compute_month,
    get_properties,
    _get_pricing_client,
    _fetch_bookings_for_window,
    clear_bookings_cache,
)
from ..models import CalendarResponse, DayResponse, IgmsSync
from pricing_engine.config import EngineConfig

router = APIRouter(prefix="/api", tags=["calendar"])
logger = logging.getLogger(__name__)


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
    repo_root = Path(__file__).resolve().parents[2]
    env_cfg = EngineConfig.from_env(repo_root / ".env")
    igms_property = igms_property or {}

    listings = igms_property.get("listings")
    listing_uids = listings if isinstance(listings, list) else []
    location = igms_property.get("location") or {}
    lat = igms_property.get("latitude", location.get("lat", 0)) or 0
    lng = igms_property.get("longitude", location.get("lng", 0)) or 0

    return {
        "config_schema_version": 2,
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
            "far_future": {"window_days": 60, "discount": 0.9},
            "last_minute": {"window_days": 7, "discount": 0.92, "threshold_occupancy": 0.5},
        },
        "seasonal_months": {f"{m:02d}": 1.0 for m in range(1, 13)},
        "dow_multipliers": {
            "mon": 1.0, "tue": 1.0, "wed": 1.0, "thu": 1.0, "fri": 1.1, "sat": 1.1, "sun": 1.0,
        },
        "local_events": [],
        "local_events_config": {},
        "pricing_adjustments": {
            "occupancy_pacing": {
                "enabled": True,
                "window_days": 14,
                "target_occupancy": 0.25,
                "sensitivity": 0.20,
                "max_discount": 0.10,
                "max_increase": 0.10,
                "min_available_nights": 5,
            },
            "booking_velocity": {
                "enabled": True,
                "recent_window_days": 7,
                "baseline_window_days": 60,
                "sensitivity": 0.08,
                "max_discount": 0.00,
                "max_increase": 0.15,
                "min_recent_bookings": 2,
                "min_baseline_bookings": 3,
            },
        },
        "external_market_data": {
            "enabled": False,
            "source": None,
            "api_key": None,
            "last_pull_timestamp": None,
            "comp_set_definition": {},
            "raw_data": {},
            "confidence": 0.0,
            "num_comps_used": 0,
        },
        # Legacy compatibility shadow; retained during migration.
        "demand_config": {
            "demand_window_days": 14,
            "velocity_window_days": 7,
            "velocity_factor": 0.15,
            "occupancy_factor": 0.3,
            "far_future": {"window_days": 60, "discount": 0.9},
            "last_minute": {"window_days": 7, "discount": 0.92, "threshold_occupancy": 0.5},
        },
        "holiday_buffer_days": 3,
        "state": state or "CA",
        "holiday_buffer_slope": 0.05,
    }


def _build_booking_spans(bookings: list[dict]) -> list[dict]:
    """Build BookingSpan list from raw booking records.

    Booked nights: checkin <= d < checkout (checkout night is NOT booked).
    """
    spans = []
    for b in bookings:
        if b.get("booking_status") not in ("accepted", "confirmed"):
            continue

        checkin_raw = b.get("checkin") or b.get("local_checkin_dttm", "")[:10]
        checkout_raw = b.get("checkout") or b.get("local_checkout_dttm", "")[:10]
        if not checkin_raw or not checkout_raw:
            continue

        try:
            checkin_dt = datetime.strptime(checkin_raw[:10], "%Y-%m-%d").date()
            checkout_dt = datetime.strptime(checkout_raw[:10], "%Y-%m-%d").date()
        except ValueError:
            continue

        reservation_code = str(b.get("reservation_code") or "").strip()
        label = (reservation_code or "Booked")[:32]

        spans.append({
            "booking_id": b.get("booking_id", ""),
            "label": label,
            "reservation_code": b.get("reservation_code"),
            "guest_name": b.get("guest_name"),
            "checkin": checkin_dt.isoformat(),
            "checkout": checkout_dt.isoformat(),
            "checkin_display": checkin_dt.strftime("%b %-d"),
            "checkout_display": checkout_dt.strftime("%b %-d"),
            "nights": (checkout_dt - checkin_dt).days,
        })
    return spans


def _build_booked_nights_set(bookings: list[dict]) -> set[str]:
    """Build set of booked night dates: [checkin, checkout)."""
    booked: set[str] = set()
    for b in bookings:
        if b.get("booking_status") not in ("accepted", "confirmed"):
            continue
        checkin_raw = b.get("checkin") or b.get("local_checkin_dttm", "")[:10]
        checkout_raw = b.get("checkout") or b.get("local_checkout_dttm", "")[:10]
        if not checkin_raw or not checkout_raw:
            continue
        try:
            checkin_dt = datetime.strptime(checkin_raw[:10], "%Y-%m-%d").date()
            checkout_dt = datetime.strptime(checkout_raw[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        cur = checkin_dt
        while cur < checkout_dt:
            booked.add(cur.isoformat())
            cur = cur.fromordinal(cur.toordinal() + 1)
    return booked


def _coerce_is_available(entry: dict) -> bool | None:
    if "is_available" in entry:
        raw = entry.get("is_available")
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return raw != 0
        if isinstance(raw, str):
            val = raw.strip().lower()
            if val in {"1", "true", "yes", "y"}:
                return True
            if val in {"0", "false", "no", "n"}:
                return False
    status = str(entry.get("status", "")).strip().lower()
    if status in {"available", "open"}:
        return True
    if status in {"unavailable", "blocked", "booked", "closed", "reserved"}:
        return False
    return None


@router.get("/properties")
async def list_properties():
    """Return all properties as {property_uid, name} list."""
    return get_properties()


@router.get("/properties/discover")
async def discover_properties():
    """Fetch all iGMS properties and mark which ones have local configs.

    Returns list of {property_uid, name, state, has_local_config} sorted by name+uid.
    Read-only: no file writes.
    """
    from ..engine_proxy import _CONFIG_STORE

    try:
        client = _get_pricing_client()
        raw = client.get_all_properties()
        igms_props = _normalize_igms_properties(raw)
    except Exception:
        logger.exception("discover: iGMS fetch failed")
        return []

    local_uids = {p.stem for p in Path(_CONFIG_STORE.config_dir).glob("*.json")}

    result = []
    for p in igms_props:
        uid = str(p.get("property_uid") or "").strip()
        if not uid:
            continue
        location = p.get("location") or {}
        result.append({
            "property_uid": uid,
            "name": str(p.get("name") or f"Property {uid}"),
            "state": str(p.get("state") or location.get("state") or "CA"),
            "has_local_config": uid in local_uids,
        })

    return sorted(result, key=lambda x: (x.get("name", "").lower(), x.get("property_uid", "")))


@router.post("/properties/add")
async def add_property(body: dict):
    """Add a property by creating its local config from iGMS discovery data.

    Request body: {"property_uid": "<uid>"}
    Returns: {"status": "created", "property_uid": "...", "name": "..."}
            or {"status": "exists"} if already on disk
    Does NOT overwrite existing files.
    """
    from ..engine_proxy import _CONFIG_STORE

    uid = str(body.get("property_uid") or "").strip()
    if not uid:
        return {"error": "property_uid is required"}

    config_path = Path(_CONFIG_STORE.config_dir) / f"{uid}.json"
    if config_path.exists():
        return {"status": "exists", "property_uid": uid}

    try:
        client = _get_pricing_client()
        raw = client.get_all_properties()
        igms_props = _normalize_igms_properties(raw)
    except Exception:
        logger.exception("add_property: iGMS fetch failed")
        return {"error": "Failed to fetch iGMS properties"}

    igms_match = None
    for p in igms_props:
        if str(p.get("property_uid") or "").strip() == uid:
            igms_match = p
            break

    if igms_match is None:
        return {"error": f"Property {uid} not found in iGMS"}

    name = str(igms_match.get("name") or f"Property {uid}").strip()
    location = igms_match.get("location") or {}
    state = str(igms_match.get("state") or location.get("state") or "CA").strip() or "CA"

    config = _build_default_property_config(uid, name, state, igms_match)
    _CONFIG_STORE.save(uid, config)

    return {"status": "created", "property_uid": uid, "name": name}


@router.get("/calendar/{year}/{month}", response_model=CalendarResponse)
async def get_calendar(
    year: int,
    month: int,
    property_uid: str = Query(default="731418607849470882"),
    force_refresh: bool = Query(default=False),
):
    """
    Compute pricing for every day in the requested month.

    Live Airbnb prices are fetched automatically from iGMS.
    Bookings are loaded from iGMS to support availability rules
    (e.g. block day before/after check-in).

    Query params:
    - property_uid: property to price (default Frosty Pines)
    - force_refresh: when true, invalidate cached bookings and pull fresh from iGMS
    """
    from calendar import monthrange
    _, n_days = monthrange(year, month)
    from_date = f"{year:04d}-{month:02d}-01"
    to_date = f"{year:04d}-{month:02d}-{n_days:02d}"

    igms_price_count = 0
    igms_bookings_count = 0
    igms_error: str | None = None
    igms_pull_success = False

    airbnb_prices: dict[str, float] = {}
    calendar_entries: list[dict] = []
    live_is_available_by_date: dict[str, bool] = {}
    try:
        client = _get_pricing_client()
        raw = client.get_calendar(
            property_uid=property_uid,
            from_date=from_date,
            to_date=to_date,
        )
        calendar_entries = raw if isinstance(raw, list) else raw.get("data", [])
        for entry in calendar_entries:
            date = entry.get("date", "")
            if not date:
                continue
            price = entry.get("price")
            if price is not None:
                try:
                    airbnb_prices[date] = float(price)
                except (TypeError, ValueError):
                    pass
            avail = _coerce_is_available(entry)
            if avail is not None:
                live_is_available_by_date[date] = avail
        igms_price_count = len(airbnb_prices)
        igms_pull_success = True
        logger.info(
            "calendar sync pulled property_uid=%s month=%04d-%02d prices=%d entries=%d",
            property_uid, year, month, igms_price_count, len(calendar_entries)
        )
    except Exception as e:
        igms_error = str(e)
        logger.exception(
            "calendar sync prices failed property_uid=%s month=%04d-%02d",
            property_uid, year, month
        )

    bookings_in_window: list[dict] = []
    try:
        if force_refresh:
            clear_bookings_cache(property_uid)
        bookings_in_window = _fetch_bookings_for_window(
            property_uid,
            from_date,
            to_date,
            force_refresh=force_refresh,
        )
        igms_bookings_count = len(bookings_in_window)
    except Exception:
        logger.exception(
            "calendar sync bookings failed property_uid=%s month=%04d-%02d",
            property_uid, year, month
        )

    days = compute_month(
        property_uid=property_uid,
        year=year,
        month=month,
        calendar_data=calendar_entries,
        airbnb_prices=airbnb_prices,
        bookings_in_window=bookings_in_window,
    )
    confirmed_bookings = [b for b in bookings_in_window if b.get("booking_status") in ("accepted", "confirmed")]
    booked_nights = _build_booked_nights_set(confirmed_bookings)

    for d in days:
        date_key = d.get("date", "")
        live_avail = live_is_available_by_date.get(d.get("date", ""))
        if live_avail is False:
            d["is_available"] = False
            if not d.get("blocked_reason"):
                d["blocked_reason"] = "igms_unavailable"
            d["has_proposed_change"] = False
        if date_key in booked_nights:
            # Never propose prices on nights already booked.
            d["is_available"] = False
            d["blocked_reason"] = "booked"
            d["has_proposed_change"] = False
        if d.get("blocked_reason") == "booking_window_closed":
            d["live_price_status"] = "closed"
            d["has_proposed_change"] = False
        elif not igms_pull_success and d.get("current_airbnb_price") is None:
            d["live_price_status"] = "error"
            d["has_proposed_change"] = False

    spans = _build_booking_spans(confirmed_bookings)

    return CalendarResponse(
        year=year,
        month=month,
        property_uid=property_uid,
        days=[DayResponse(**d) for d in days],
        sync=IgmsSync(
            igms_pull_success=igms_pull_success,
            igms_price_count=igms_price_count,
            igms_bookings_count=igms_bookings_count,
            igms_error=igms_error,
            pulled_at=datetime.now(timezone.utc).isoformat(),
        ),
        bookings=spans,
    )
