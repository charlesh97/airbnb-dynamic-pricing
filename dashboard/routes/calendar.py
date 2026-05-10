from __future__ import annotations

from datetime import datetime, timezone
import logging
from fastapi import APIRouter, Query

from pathlib import Path
from ..engine_proxy import compute_month, get_properties, _get_pricing_client, _fetch_bookings_for_window, _normalize_igms_properties, _build_default_property_config
from ..models import CalendarResponse, DayResponse, IgmsSync

router = APIRouter(prefix="/api", tags=["calendar"])
logger = logging.getLogger(__name__)


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

        label = (
            b.get("guest_name")
            or b.get("reservation_code")
            or b.get("booking_id", "?")
        )[:20]

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
):
    """
    Compute pricing for every day in the requested month.

    Live Airbnb prices are fetched automatically from iGMS.
    Bookings are loaded from iGMS to support availability rules
    (e.g. block day before/after check-in).

    Query params:
    - property_uid: property to price (default Frosty Pines)
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
        bookings_in_window = _fetch_bookings_for_window(property_uid, from_date, to_date)
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
    for d in days:
        live_avail = live_is_available_by_date.get(d.get("date", ""))
        if live_avail is False:
            d["is_available"] = False
            if not d.get("blocked_reason"):
                d["blocked_reason"] = "igms_unavailable"
            d["has_proposed_change"] = False
        if d.get("blocked_reason") == "booking_window_closed":
            d["live_price_status"] = "closed"
            d["has_proposed_change"] = False
        elif not igms_pull_success and d.get("current_airbnb_price") is None:
            d["live_price_status"] = "error"
            d["has_proposed_change"] = False

    confirmed_bookings = [b for b in bookings_in_window if b.get("booking_status") in ("accepted", "confirmed")]
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
