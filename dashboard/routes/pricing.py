"""POST /api/pricing/run — re-run pricing for a month."""

import logging
import sys
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from ..engine_proxy import compute_month, get_calendar_with_live_prices
from ..models import CalendarResponse, DayResponse, RunPricingRequest

# Ensure src/ is importable (mirrors engine_proxy.py)
_REPO_ROOT = Path(__file__).parent.parent.parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from pricing_engine.push_pipeline import PushPipelineRequest  # noqa: E402

router = APIRouter(prefix="/api", tags=["pricing"])

LOG = logging.getLogger(__name__)


class PushPricingResponse(BaseModel):
    success: bool
    from_date: str = ""
    to_date: str = ""
    base_booking_window_days: int = 0
    effective_window_days: int = 0
    dates_evaluated: int = 0
    price_updates_sent: int = 0
    availability_updates_sent: int = 0
    dates_skipped_booked: int = 0
    dates_skipped_live_blocked: int = 0
    dates_skipped_outside_window: int = 0
    skipped_live_blocked_dates: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []


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


@router.post("/pricing/push", response_model=PushPricingResponse)
async def push_pricing(body: RunPricingRequest):
    """
    Compute prices and push to iGMS via the shared pipeline.

    Delegates to ``run_push_pipeline()`` — the single source of truth.
    The ``year`` / ``month`` fields are ignored; the pipeline uses the
    effective booking window.
    """
    from pricing_engine.push_pipeline import run_push_pipeline

    req = PushPipelineRequest(property_uid=body.property_uid)
    result = run_push_pipeline(req)

    return PushPricingResponse(
        success=result.success,
        from_date=result.from_date,
        to_date=result.to_date,
        base_booking_window_days=result.base_booking_window_days,
        effective_window_days=result.effective_window_days,
        dates_evaluated=result.dates_evaluated,
        price_updates_sent=result.price_updates_sent,
        availability_updates_sent=result.availability_updates_sent,
        dates_skipped_booked=result.dates_skipped_booked,
        dates_skipped_live_blocked=result.dates_skipped_live_blocked,
        dates_skipped_outside_window=result.dates_skipped_outside_window,
        skipped_live_blocked_dates=result.skipped_live_blocked_dates,
        errors=result.errors,
        warnings=result.warnings,
    )
