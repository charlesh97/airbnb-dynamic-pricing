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


def _pagination_next_page(response: dict[str, Any], current_page: int) -> int | None:
    """Return the next page number from iGMS bookings response metadata."""
    direct_next = response.get("next_page")
    if isinstance(direct_next, int) and direct_next > current_page:
        return direct_next

    meta = response.get("meta")
    if isinstance(meta, dict):
        meta_next = meta.get("next_page")
        if isinstance(meta_next, int) and meta_next > current_page:
            return meta_next
        if bool(meta.get("has_next_page")):
            return current_page + 1

    return None


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
                        None = active reservations ("confirmed", "accepted").

    Returns:
        List of booking dicts with fields: checkin, checkout,
        created_dttm, booking_status, property_uid, listing_uid,
        platform_type, nights, gross_rental_price, guests.
    """
    if status_filter is None:
        # iGMS commonly uses "accepted" for active reservations.
        status_filter = ["confirmed", "accepted"]

    params: dict[str, Any] = {
        "property_uid": property_uid,
        "from_date": from_date,
        "to_date": to_date,
    }

    try:
        response = client.get_bookings(page=1, **params)
    except Exception as exc:
        logger.warning("get_bookings failed for %s (%s–%s): %s", property_uid, from_date, to_date, exc)
        return []

    # Normalize paginated response to list
    if isinstance(response, dict):
        data = list(response.get("data", response.get("bookings", [])) or [])
        next_page = _pagination_next_page(response, current_page=1)
        while next_page is not None:
            try:
                more = client.get_bookings(page=next_page, **params)
            except Exception as exc2:
                logger.warning("get_bookings pagination failed: %s", exc2)
                break
            if not isinstance(more, dict):
                break
            page_rows = more.get("data", more.get("bookings", []))
            if isinstance(page_rows, list):
                data.extend(page_rows)
            current_page = next_page
            next_page = _pagination_next_page(more, current_page=current_page)

        bookings = data if isinstance(data, list) else []
    elif isinstance(response, list):
        bookings = response
    else:
        logger.warning("get_bookings returned unexpected type %s for %s", type(response), property_uid)
        return []

    # Normalize each record
    normalized = []
    for b in bookings:
        if not isinstance(b, dict):
            continue

        # Keep only the requested property. iGMS may return mixed properties
        # even when property_uid is supplied in filters.
        rec_property_uid = str(b.get("property_uid") or "").strip()
        logger.debug("iGMS booking property_uid comparison: rec=%r requested=%r match=%s booking_id=%s",
                     rec_property_uid, property_uid, rec_property_uid == property_uid, b.get("booking_id"))
        if rec_property_uid and rec_property_uid != property_uid:
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

        customer = b.get("customer") if isinstance(b.get("customer"), dict) else {}
        customer_first = str(
            customer.get("first_name")
            or customer.get("firstname")
            or customer.get("given_name")
            or ""
        ).strip()
        customer_last = str(
            customer.get("last_name")
            or customer.get("lastname")
            or customer.get("family_name")
            or ""
        ).strip()
        customer_full = str(
            customer.get("name")
            or customer.get("full_name")
            or " ".join([customer_first, customer_last]).strip()
            or ""
        ).strip()

        normalized.append({
            "booking_id":         b.get("booking_id") or b.get("id") or b.get("reservation_id") or "",
            "property_uid":       rec_property_uid or property_uid,
            "listing_uid":        b.get("listing_uid", ""),
            "platform_type":      platform,
            "checkin":            _ensure_date(
                b.get("checkin")
                or b.get("local_checkin_dttm")
                or b.get("start_date")
                or ""
            ),
            "checkout":           _ensure_date(
                b.get("checkout")
                or b.get("local_checkout_dttm")
                or b.get("end_date")
                or ""
            ),
            "created_dttm":       _ensure_dt(b.get("created_dttm", "")),
            "booking_status":     status,
            "nights":             b.get("nights", 0),
            "gross_rental_price": b.get("gross_rental_price", 0.0),
            "guests":             b.get("guests", 0),
            # Preserve guest/reservation identifiers for dashboard labeling.
            "reservation_code":   b.get("reservation_code") or b.get("confirmation_code") or "",
            "guest_name":         b.get("guest_name")
                                  or b.get("guest_full_name")
                                  or b.get("guest")
                                  or customer_full
                                  or "",
            "guest_first_name":   b.get("guest_first_name") or customer_first,
            "guest_last_name":    b.get("guest_last_name") or customer_last,
            "customer":           customer,
        })

    if not normalized:
        logger.info("No bookings found for %s in window %s–%s", property_uid, from_date, to_date)
        return []

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
