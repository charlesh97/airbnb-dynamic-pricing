"""GET /api/days/{date} — full factor breakdown for a single day."""

from typing import Optional

from fastapi import APIRouter, Query

from ..engine_proxy import get_day_detail, get_calendar_with_live_prices
from ..models import DayDetailResponse

router = APIRouter(prefix="/api", tags=["days"])


@router.get("/days/{date}", response_model=DayDetailResponse)
async def get_day(
    date: str,
    property_uid: str = Query(default="731418607849470882"),
    airbnb_price: Optional[float] = Query(default=None),
):
    """
    Return full factor breakdown for a single day.

    Query params:
    - property_uid: property uid
    - airbnb_price: optional current Airbnb price for comparison
    """
    # Try to get live price if not provided
    if airbnb_price is None:
        prices = get_calendar_with_live_prices(property_uid, date, date)
        airbnb_price = prices.get(date)

    detail = get_day_detail(
        property_uid=property_uid,
        date=date,
        airbnb_price=airbnb_price,
    )

    return DayDetailResponse(**detail)
