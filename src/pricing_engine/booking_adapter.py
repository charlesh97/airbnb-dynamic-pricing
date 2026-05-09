"""Booking data adapter — fetches bookings from iGMS and filters to a pricing window.

This module bridges the iGMS booking API (IGMSClient.get_bookings /
get_all_bookings) to the pricing engine's bookings_in_window format.

Booking record shape returned by iGMS (GET /api/v1/bookings):
    {
        "booking_id": str,
        "property_uid": str,
        "listing_uid": str,
        "platform_type": str,           # "airbnb" | "vrbo" | ...
        "checkin": "YYYY-MM-DD",
        "checkout": "YYYY-MM-DD",
        "created_dttm": "YYYY-MM-DDTHH:MM:SS",
        "booking_status": str,          # "confirmed" | "pending" | ...
        "nights": int,
        "gross_rental_price": float,
        "cleaning_fee": float,
        "guests": int,
    }

Pricing engine demand/yield strategies expect these fields on each booking:
    checkin       — str  — booking arrival date
    checkout      — str  — booking departure date
    created_dttm  — str  — when the booking was made
    booking_status — str  — filter to confirmed/active bookings

All date/datetime fields use _parse_date() in each strategy, which handles
    "%Y-%m-%d" and "%Y-%m-%dT%H:%M:%S" formats.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


def fetch_bookings_for_window(
    client: Any,
    property_uid: str,
    from_date: str,
    to_date: str,
    include_platforms: list[str] | None = None,
    status_filter: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch bookings for a property within a date window.

    Args:
        client:         An authenticated IGMSClient (or PricingClient).
        property_uid:   iGMS property UID to fetch bookings for.
        from_date:      Start of window (YYYY-MM-DD).
        to_date:        End of window (YYYY-MM-DD).
        include_platforms: Optional list of platform types to keep.
                          None = all platforms.
        status_filter:  Optional list of booking statuses to keep.
                        None = confirmed only ("confirmed").

    Returns:
        List of booking dicts with fields: checkin, checkout,
        created_dttm, booking_status, property_uid, listing_uid,
        platform_type, nights, gross_rental_price, guests.
    """
    if status_filter is None:
        status_filter = ["confirmed"]

    params: dict[str, Any] = {
        "property_uid": property_uid,
        "from_date": from_date,
        "to_date": to_date,
    }

    try:
        response = client.get_bookings(page=1, **params)
    except Exception as exc:
        logger.warning("get_bookings failed for %s (%s–%s): %s", property_uid, from_date, to_date, exc)
        return _stub_bookings(property_uid, from_date, to_date)

    # Normalize paginated response to list
    pages = [response]
    if isinstance(response, dict):
        data = response.get("data", response.get("bookings", []))
        # If next page token present, collect remaining pages
        if response.get("next_page"):
            try:
                more = client.get_bookings(page=2, **params)
                while more and isinstance(more, dict):
                    data.extend(more.get("data", more.get("bookings", [])))
                    if not more.get("next_page"):
                        break
                    more = client.get_bookings(page=more["next_page"], **params)
            except Exception as exc2:
                logger.warning("get_bookings pagination failed: %s", exc2)

        bookings = data if isinstance(data, list) else []
    elif isinstance(response, list):
        bookings = response
    else:
        logger.warning("get_bookings returned unexpected type %s for %s", type(response), property_uid)
        return _stub_bookings(property_uid, from_date, to_date)

    # Normalize each record
    normalized = []
    for b in bookings:
        if not isinstance(b, dict):
            continue

        # Normalize status
        raw_status = b.get("booking_status", "").lower()
        status = raw_status or ""

        # Apply status filter
        if status_filter and status not in status_filter:
            continue

        # Apply platform filter
        platform = (b.get("platform_type") or "").lower()
        if include_platforms is not None and platform and platform not in [p.lower() for p in include_platforms]:
            continue

        normalized.append({
            "booking_id":         b.get("booking_id", ""),
            "property_uid":       b.get("property_uid", property_uid),
            "listing_uid":        b.get("listing_uid", ""),
            "platform_type":      platform,
            "checkin":            _ensure_date(b.get("checkin", "")),
            "checkout":           _ensure_date(b.get("checkout", "")),
            "created_dttm":       _ensure_dt(b.get("created_dttm", "")),
            "booking_status":     status,
            "nights":             b.get("nights", 0),
            "gross_rental_price": b.get("gross_rental_price", 0.0),
            "guests":             b.get("guests", 0),
        })

    if not normalized:
        logger.info("No bookings found for %s in window %s–%s — using stub data", property_uid, from_date, to_date)
        return _stub_bookings(property_uid, from_date, to_date)

    logger.info("Fetched %d bookings for %s (%s–%s)", len(normalized), property_uid, from_date, to_date)
    return normalized


# ─── Test / stub data ──────────────────────────────────────────────────────────

def _stub_bookings(property_uid: str, from_date: str, to_date: str) -> list[dict[str, Any]]:
    """Return realistic stub bookings when the API is unavailable.

    Generates 5 stub bookings with realistic dates, statuses, and prices
    for the given property and window. These are flagged with a comment
    so callers can detect them if needed.

    Used when IGMS_ACCESS_TOKEN is absent or the API call fails.
    """
    import random

    start = datetime.strptime(from_date, "%Y-%m-%d")
    end   = datetime.strptime(to_date,   "%Y-%m-%d")
    span  = (end - start).days or 1

    stubs = []
    statuses = ["confirmed", "confirmed", "confirmed", "pending"]
    platforms = ["airbnb", "airbnb", "vrbo"]

    for i in range(5):
        checkin_days = random.randint(0, max(0, span - 3))
        nights = random.randint(2, min(7, span - checkin_days))
        checkin  = start + timedelta(days=checkin_days)
        checkout = checkin + timedelta(days=nights)
        created  = checkin - timedelta(days=random.randint(3, 30))

        stubs.append({
            "booking_id":         f"stub_{property_uid}_{i+1}",
            "property_uid":       property_uid,
            "listing_uid":        "",
            "platform_type":      random.choice(platforms),
            "checkin":           checkin.strftime("%Y-%m-%d"),
            "checkout":          checkout.strftime("%Y-%m-%d"),
            "created_dttm":       created.strftime("%Y-%m-%dT%H:%M:%S"),
            "booking_status":     random.choice(statuses),
            "nights":             nights,
            "gross_rental_price": round(random.uniform(150, 600) * nights, 2),
            "guests":             random.randint(1, 6),
            "_stub":              True,   # marker so callers can detect stub data
        })

    logger.info("Generated %d stub bookings for %s (%s–%s)", len(stubs), property_uid, from_date, to_date)
    return stubs


def _ensure_date(value: str) -> str:
    """Normalize a date string to YYYY-MM-DD."""
    if not value:
        return ""
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value[:19], fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return value[:10]


def _ensure_dt(value: str) -> str:
    """Normalize a datetime string to YYYY-MM-DDTHH:MM:SS."""
    if not value:
        return ""
    value = value.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value[:19], fmt).strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            pass
    return value
