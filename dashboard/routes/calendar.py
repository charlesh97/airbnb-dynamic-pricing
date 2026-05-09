from fastapi import APIRouter, Query

from ..engine_proxy import compute_month, get_calendar_with_live_prices, get_properties, _get_pricing_client
from ..models import CalendarResponse, DayResponse

router = APIRouter(prefix="/api", tags=["calendar"])


@router.get("/properties")
async def list_properties():
    """Return all properties as {property_uid, name} list."""
    return get_properties()


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

    # Fetch live prices from iGMS
    airbnb_prices = get_calendar_with_live_prices(property_uid, from_date, to_date)

    # Fetch real bookings so availability rules (block day before/after, etc.) can use them
    bookings_in_window = []
    try:
        client = _get_pricing_client()
        # Use a wide window to capture bookings relevant to availability rules
        resp = client.get_bookings(
            page=1,
            property_uid=property_uid,
            start_date=from_date,
            end_date=to_date,
        )
        bookings_in_window = resp.get("data", []) if resp else []
    except Exception:
        pass  # non-fatal — availability works without it

    days = compute_month(
        property_uid=property_uid,
        year=year,
        month=month,
        airbnb_prices=airbnb_prices,
        bookings_in_window=bookings_in_window,
    )

    return CalendarResponse(
        year=year,
        month=month,
        property_uid=property_uid,
        days=[DayResponse(**d) for d in days],
    )
