"""POST /api/pricing/run — re-run pricing for a month."""

from fastapi import APIRouter

from ..engine_proxy import compute_month, get_calendar_with_live_prices
from ..models import CalendarResponse, DayResponse, RunPricingRequest

router = APIRouter(prefix="/api", tags=["pricing"])


@router.post("/pricing/run", response_model=CalendarResponse)
async def run_pricing(body: RunPricingRequest):
    """
    Re-run pricing for a specific month.

    Used by the "Run Pricing" button in the UI.
    Does NOT push prices to Airbnb/iGMS (future feature).
    """
    airbnb_prices = body.airbnb_prices
    if not airbnb_prices:
        from calendar import monthrange
        _, n_days = monthrange(body.year, body.month)
        from_date = f"{body.year:04d}-{body.month:02d}-01"
        to_date = f"{body.year:04d}-{body.month:02d}-{n_days:02d}"
        airbnb_prices = get_calendar_with_live_prices(body.property_uid, from_date, to_date)

    days = compute_month(
        property_uid=body.property_uid,
        year=body.year,
        month=body.month,
        airbnb_prices=airbnb_prices,
    )

    return CalendarResponse(
        year=body.year,
        month=body.month,
        property_uid=body.property_uid,
        days=[DayResponse(**d) for d in days],
    )


@router.post("/pricing/push")
async def push_pricing(body: RunPricingRequest):
    """
    Compute prices for a month and push them to iGMS.
    Requires iGMS credentials configured in .env.
    """
    import logging
    from calendar import monthrange
    from datetime import datetime

    _, n_days = monthrange(body.year, body.month)
    from_date = f"{body.year:04d}-{body.month:02d}-01"
    to_date = f"{body.year:04d}-{body.month:02d}-{n_days:02d}"

    # Compute prices
    airbnb_prices = get_calendar_with_live_prices(body.property_uid, from_date, to_date)
    from ..engine_proxy import _fetch_bookings_for_window
    bookings = _fetch_bookings_for_window(body.property_uid, from_date, to_date)
    days = compute_month(
        property_uid=body.property_uid,
        year=body.year,
        month=body.month,
        airbnb_prices=airbnb_prices,
        bookings_in_window=bookings,
    )

    # Push to iGMS
    try:
        from ..engine_proxy import _get_pricing_client
        client = _get_pricing_client()
        from src.pricing_engine.client import PricingClient
        updates = []
        for day in days:
            if not day.get("is_available", True):
                continue
            listing_uid = body.property_uid + "_airbnb_209713065"
            updates.append({
                "listing_uid": listing_uid,
                "property_uid": body.property_uid,
                "date": day["date"],
                "price": day["final_price"],
                "currency": "USD",
                "min_stay": day.get("min_stay", 2),
            })
        results = client.bulk_update_prices(updates)
        pushed = sum(1 for r in results if hasattr(r, 'status_code') and r.status_code in (200, 201, 204))
        return {"pushed": pushed, "total": len(updates), "results": str(results[:3])}
    except Exception as e:
        logging.error(f"Push failed: {e}")
        return {"error": str(e), "pushed": 0, "total": len(updates)}
