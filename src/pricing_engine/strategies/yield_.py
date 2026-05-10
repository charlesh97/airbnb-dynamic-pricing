"""Yield strategy reduced to explicit last-minute adjustment only.

This module intentionally maps only to availability.last_minute
(and legacy demand_config.last_minute fallback), so UI controls and
calculation line items stay aligned.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .base import PriceRecommendation, PricingStrategy


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value[:19], fmt)
        except ValueError:
            pass
    return None


def _occupancy_rate_for_window(
    *,
    bookings_in_window: list[dict[str, Any]],
    window_start: datetime,
    window_end: datetime,
) -> float:
    """Approximate occupancy in [window_start, window_end) from stay overlaps."""
    window_days = max(1, (window_end - window_start).days)
    booked_nights = 0
    for booking in bookings_in_window:
        b_start = _parse_date(booking.get("checkin", ""))
        b_end = _parse_date(booking.get("checkout", ""))
        if not b_start or not b_end:
            continue
        overlap_start = max(b_start, window_start)
        overlap_end = min(b_end, window_end)
        if overlap_end > overlap_start:
            booked_nights += (overlap_end - overlap_start).days
    return min(1.0, max(0.0, booked_nights / window_days))


class YieldStrategy(PricingStrategy):
    """Compatibility module: computes last-minute adjustment only."""

    name = "yield"

    def compute(
        self,
        *,
        property_uid: str,
        date: str,
        calendar_entry: dict[str, Any] | None,
        bookings_in_window: list[dict[str, Any]],
        config: dict[str, Any],
    ) -> PriceRecommendation:
        base = self._base_price(config, property_uid)

        target = datetime.strptime(date, "%Y-%m-%d")
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        days_out = max(0, (target - today).days)

        availability_cfg = config.get("availability", {}) or {}
        last_min_cfg = availability_cfg.get("last_minute")
        if last_min_cfg is None:
            last_min_cfg = (config.get("demand_config", {}) or {}).get("last_minute", {})
        last_min_cfg = last_min_cfg or {}

        window_days = max(1, int(last_min_cfg.get("window_days", 7) or 7))
        discount = float(last_min_cfg.get("discount", 1.0) or 1.0)
        threshold_occupancy = float(last_min_cfg.get("threshold_occupancy", 1.0) or 1.0)

        window_start = target - timedelta(days=window_days)
        occupancy_rate = _occupancy_rate_for_window(
            bookings_in_window=bookings_in_window,
            window_start=window_start,
            window_end=target,
        )

        last_minute_applied = (
            days_out <= window_days
            and occupancy_rate <= threshold_occupancy
            and discount > 0
        )
        last_minute_adjustment = discount if last_minute_applied else 1.0

        final_multiplier = last_minute_adjustment
        raw_price = base * final_multiplier
        price = self._clamp(raw_price, config, property_uid)

        if last_minute_applied:
            reasoning = "within last-minute window and low occupancy"
        elif days_out > window_days:
            reasoning = "outside last-minute window"
        else:
            reasoning = "occupancy above threshold"

        return PriceRecommendation(
            strategy_name=self.name,
            suggested_price=round(price, 2),
            confidence=0.8,
            factors={
                "base_price": base,
                "days_out": days_out,
                "final_multiplier": round(final_multiplier, 3),
                "last_minute_adjustment": round(last_minute_adjustment, 3),
                "last_minute_reasoning": reasoning,
                "last_minute_window_days": window_days,
                "last_minute_config_discount": discount,
                "last_minute_threshold_occupancy": threshold_occupancy,
                "last_minute_config_applied": last_minute_applied,
                "last_minute_window_occupancy": round(occupancy_rate, 3),
                "reasoning": reasoning,
            },
        )
