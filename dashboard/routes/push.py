"""POST /api/calendar/push — push computed prices to iGMS via shared pipeline."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

# Ensure src/ is importable (mirrors engine_proxy.py)
_REPO_ROOT = Path(__file__).parent.parent.parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from pricing_engine.push_pipeline import PushPipelineRequest, run_push_pipeline  # noqa: E402

router = APIRouter(prefix="/api", tags=["calendar"])

LOG = logging.getLogger(__name__)


class PushRequest(BaseModel):
    property_uid: str
    year: int = 0          # ignored — kept for API compatibility
    month: int = 0         # ignored — kept for API compatibility


class PushResponse(BaseModel):
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


@router.post("/calendar/push", response_model=PushResponse)
async def push_prices(body: PushRequest):
    """Push computed prices to iGMS for the effective booking window.

    Delegates entirely to ``run_push_pipeline()`` — the single source of
    truth for push behaviour.
    """
    req = PushPipelineRequest(property_uid=body.property_uid)
    result = run_push_pipeline(req)

    return PushResponse(
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
