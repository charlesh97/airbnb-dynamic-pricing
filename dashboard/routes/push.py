"""POST /api/calendar/push — push computed prices to iGMS for a date window."""

from __future__ import annotations

import datetime
import logging
from calendar import monthrange
from datetime import datetime, timedelta

from fastapi import APIRouter
from pydantic import BaseModel

from ..engine_proxy import compute_month, _get_pricing_client, _fetch_bookings_for_window, _CONFIG_STORE

router = APIRouter(prefix="/api", tags=["calendar"])

LOG = logging.getLogger(__name__)


class PushRequest(BaseModel):
    property_uid: str
    year: int
    month: int


class PushResponse(BaseModel):
    success: bool
    price_updates_sent: int = 0
    availability_updates_sent: int = 0
    dates_priced: int = 0
    errors: list[str] = []


def _parse_date(value: str) -> datetime.date | None:
    """Parse date from booking checkin/checkout fields."""
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value[:19], fmt).date()
        except ValueError:
            pass
    return None


def _coerce_is_available(entry: dict) -> bool | None:
    """Coerce is_available from iGMS calendar entry."""
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


def _build_live_day_map(entries: list[dict]) -> dict[str, dict]:
    """Build {date: {price, min_stay, is_available}} from iGMS calendar entries."""
    result = {}
    for entry in entries:
        date_str = entry.get("date", "")
        if not date_str:
            continue
        result[date_str] = {
            "price": entry.get("price"),
            "min_stay": entry.get("min_stay"),
            "is_available": _coerce_is_available(entry),
        }
    return result


def _build_booked_nights_set(bookings: list[dict]) -> set[str]:
    """Build set of booked night date strings [checkin, checkout)."""
    booked = set()
    for b in bookings:
        if b.get("booking_status") not in ("accepted", "confirmed"):
            continue
        checkin = _parse_date(b.get("checkin") or b.get("local_checkin_dttm", "")[:10])
        checkout = _parse_date(b.get("checkout") or b.get("local_checkout_dttm", "")[:10])
        if not checkin or not checkout:
            continue
        cur = checkin
        while cur < checkout:
            booked.add(cur.isoformat())
            cur += timedelta(days=1)
    return booked


def _iter_months_in_range(from_date: datetime.date, to_date: datetime.date):
    """Yield (year, month) pairs for each month in the date range."""
    cur = datetime(from_date.year, from_date.month, 1)
    end = datetime(to_date.year, to_date.month, 1)
    while cur <= end:
        yield cur.year, cur.month
        m = cur.month + 1
        y = cur.year
        if m > 12:
            m = 1
            y += 1
        cur = datetime(y, m, 1)


def _group_consecutive_avail_writes(writes: list[tuple[str, str, bool]]) -> list[tuple[str, str, bool]]:
    """Group consecutive same-availability dates into range writes."""
    if not writes:
        return []
    sorted_writes = sorted(writes, key=lambda x: x[0])
    grouped = []
    start_d, end_d, avail = sorted_writes[0]
    for i in range(1, len(sorted_writes)):
        d_start, d_end, d_avail = sorted_writes[i]
        d_start_date = datetime.strptime(d_start, "%Y-%m-%d").date()
        prev_end_date = datetime.strptime(end_d, "%Y-%m-%d").date()
        if d_start_date == prev_end_date + timedelta(days=1) and d_avail == avail:
            end_d = d_end
        else:
            grouped.append((start_d, end_d, avail))
            start_d, end_d, avail = d_start, d_end, d_avail
    grouped.append((start_d, end_d, avail))
    return grouped


def _booking_overlaps_month(booking: dict, year: int, month: int) -> bool:
    """Return True if booking overlaps the given month."""
    checkin = _parse_date(booking.get("checkin") or booking.get("local_checkin_dttm", "")[:10])
    checkout = _parse_date(booking.get("checkout") or booking.get("local_checkout_dttm", "")[:10])
    if not checkin or not checkout:
        return False
    month_start = datetime(year, month, 1).date()
    month_end = datetime(year, month, monthrange(year, month)[1]).date()
    return checkin <= month_end and checkout > month_start


def _get_listing_uid_for_date(
    client,
    property_uid: str,
    from_date: str,
    to_date: str,
) -> str:
    """Fetch the listing UID for a property by looking at live calendar entries."""
    try:
        raw = client.get_calendar(
            property_uid=property_uid,
            from_date=from_date,
            to_date=to_date,
        )
        entries = raw if isinstance(raw, list) else raw.get("data", [])
        if entries:
            for e in entries:
                uid = e.get("listing_uid")
                if uid:
                    return uid
    except Exception:
        pass
    return property_uid + "_airbnb_209713065"


@router.post("/calendar/push", response_model=PushResponse)
async def push_prices(body: PushRequest):
    property_uid = body.property_uid
    cfg = _CONFIG_STORE.load(property_uid)
    bwd = cfg.get("availability", {}).get("booking_window_days", 120)
    today = datetime.utcnow().date()
    from_date = today
    to_date = today + timedelta(days=bwd)

    client = _get_pricing_client()

    live_entries: list[dict] = []
    for y, m in _iter_months_in_range(from_date, to_date):
        _, last_day = monthrange(y, m)
        month_start = f"{y:04d}-{m:02d}-01"
        month_end = f"{y:04d}-{m:02d}-{last_day:02d}"
        try:
            raw = client.get_calendar(property_uid=property_uid, from_date=month_start, to_date=month_end)
            entries = raw if isinstance(raw, list) else raw.get("data", [])
            live_entries.extend([e for e in entries if isinstance(e, dict)])
        except Exception:
            pass

    live_day_map = _build_live_day_map(live_entries)

    all_bookings: list[dict] = []
    for y, m in _iter_months_in_range(from_date, to_date):
        _, last_day = monthrange(y, m)
        month_bookings = _fetch_bookings_for_window(
            property_uid,
            f"{y:04d}-{m:02d}-01",
            f"{y:04d}-{m:02d}-{last_day:02d}",
        )
        all_bookings.extend(month_bookings)

    booked_nights = _build_booked_nights_set(all_bookings)

    days_map: dict[str, dict] = {}
    for y, m in _iter_months_in_range(from_date, to_date):
        month_bookings = [b for b in all_bookings if _booking_overlaps_month(b, y, m)]
        month_live_entries = [e for e in live_entries if str(e.get("date", "")).startswith(f"{y:04d}-{m:02d}-")]
        for day in compute_month(property_uid=property_uid, year=y, month=m, bookings_in_window=month_bookings, calendar_data=month_live_entries):
            days_map[day["date"]] = day

    calendar_batch: list[dict] = []
    availability_writes: list[tuple[str, str, bool]] = []

    for date_str in sorted(days_map.keys()):
        if date_str in booked_nights:
            continue

        desired = days_map[date_str]
        live = live_day_map.get(date_str, {})

        desired_price = desired.get("final_price")
        desired_min_stay = desired.get("min_stay")
        desired_avail = desired.get("is_available", True)
        live_price = live.get("price")
        live_min_stay = live.get("min_stay")
        live_avail = live.get("is_available")

        price_diff = abs((desired_price or 0) - (live_price or 0))
        price_changed = desired_price is not None and desired_price > 0 and price_diff >= 0.01
        min_stay_changed = desired_min_stay is not None and desired_min_stay != live_min_stay

        if price_changed or min_stay_changed:
            calendar_batch.append({
                "date": date_str,
                "price": round(desired_price, 2) if desired_price else None,
                "currency": "USD",
                "min_stay": desired_min_stay,
            })

        if desired_avail != live_avail:
            availability_writes.append((date_str, date_str, desired_avail))

    if not calendar_batch and not availability_writes:
        return PushResponse(success=True, errors=[])

    errors: list[str] = []

    if calendar_batch:
        try:
            result = client.set_calendar_batch(property_uid=property_uid, days=calendar_batch)
            sc = getattr(result, "status_code", 200)
            if sc >= 400:
                errors.append(f"set-calendar-batch HTTP {sc}: {getattr(result, 'payload', None)}")
        except Exception as exc:
            errors.append(str(exc))
            LOG.exception("set-calendar-batch failed")

    if availability_writes:
        for start_d, end_d, is_avail in _group_consecutive_avail_writes(availability_writes):
            try:
                result = client.set_property_availability(
                    property_uid=property_uid,
                    start_date=start_d,
                    end_date=end_d,
                    is_available=is_avail,
                )
                sc = getattr(result, "status_code", 200)
                if sc >= 400:
                    errors.append(f"set-property-availability HTTP {sc}: {getattr(result, 'payload', None)}")
            except Exception as exc:
                errors.append(str(exc))
                LOG.exception("set-property-availability failed for %s", start_d)

    return PushResponse(
        success=len(errors) == 0,
        price_updates_sent=len(calendar_batch),
        availability_updates_sent=len(availability_writes),
        dates_priced=len(calendar_batch),
        errors=errors,
    )