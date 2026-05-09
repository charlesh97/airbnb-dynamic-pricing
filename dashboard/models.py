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


class PropertyInfo(BaseModel):
    property_uid: str
    name: str


class CalendarResponse(BaseModel):
    year: int
    month: int
    property_uid: str
    days: List[DayResponse]


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
    seasonal: dict
    demand: dict
    event: dict
    yield_: dict = Field(alias="yield")
    competitor: dict
    strategy_weights: dict
    strategy_prices: dict
    raw_factors: dict
