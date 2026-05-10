"""Pydantic models for API request/response schemas."""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class CalendarRequest(BaseModel):
    property_uid: str = Field(default="731418607849470882")
    airbnb_prices: Optional[Dict[str, float]] = None


class RunPricingRequest(BaseModel):
    property_uid: str = Field(default="731418607849470882")
    year: int
    month: int
    airbnb_prices: Optional[Dict[str, float]] = None


class DayResponse(BaseModel):
    date: str
    final_price: float
    current_airbnb_price: Optional[float]
    price_delta: Optional[float]
    price_delta_pct: Optional[float]
    match_status: Optional[str]
    is_available: bool
    min_stay: int
    blocked_reason: Optional[str]
    confidence: float
    is_holiday: bool = False
    holiday_name: Optional[str] = None
    holiday_proximity: Optional[dict] = None
    live_price_status: str = "ok"
    has_proposed_change: bool = False


class PropertyInfo(BaseModel):
    property_uid: str
    name: str


class IgmsSync(BaseModel):
    igms_pull_success: bool = False
    igms_price_count: int = 0
    igms_bookings_count: int = 0
    igms_error: str | None = None
    pulled_at: str | None = None


class BookingSpan(BaseModel):
    booking_id: str
    label: str
    reservation_code: Optional[str] = None
    guest_name: Optional[str] = None
    checkin: str
    checkout: str
    checkin_display: str
    checkout_display: str
    nights: int


class CalendarResponse(BaseModel):
    year: int
    month: int
    property_uid: str
    days: List[DayResponse]
    sync: IgmsSync | None = None
    bookings: List[BookingSpan] = Field(default_factory=list)


class AdjustmentItem(BaseModel):
    key: str
    label: str
    amount: float
    running_total_after: float


class DayDetailResponse(BaseModel):
    date: str
    property_uid: str
    final_price: float
    current_airbnb_price: Optional[float]
    confidence: float
    is_available: bool
    min_stay: int
    blocked_reason: Optional[str]
    booking_window_days: int
    match_status: Optional[str]
    base_rate: float
    starting_price: float = 0.0
    seasonal: dict
    demand: dict
    event: dict
    competitor: dict
    strategy_weights: dict
    strategy_prices: dict
    raw_factors: dict
    adjustment_ladder: list[AdjustmentItem] = Field(default_factory=list)
    subtotal_before_blend: float = 0.0
    blend_adjustment_amount: float = 0.0
    was_price_capped: bool = False
    cap_type: Optional[str] = None
    min_price_bound: float = 0.0
    max_price_bound: float = 0.0
    raw_adjusted_price: float = 0.0
    final_recommended: float = 0.0
    current_igms_price: Optional[float] = None
    live_price_status: str = "ok"
    has_proposed_change: bool = False
