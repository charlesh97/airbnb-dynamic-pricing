"""Demand-based pricing strategy — driven by occupancy and booking velocity."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .base import PriceRecommendation, PricingStrategy


class DemandStrategy(PricingStrategy):
    """Price adjustment based on recent booking velocity and occupancy rate."""

    name = "demand"

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

        # --- occupancy rate in trailing window ---
        window_days = config.get("demand_window_days", 14)
        target = datetime.strptime(date, "%Y-%m-%d")
        start = target - timedelta(days=window_days)

        # Count bookings that overlap this window
        nights_in_window = 0
        for b in bookings_in_window:
            b_start = _parse_date(b.get("checkin", ""))
            b_end = _parse_date(b.get("checkout", ""))
            if b_start and b_end:
                overlap_start = max(b_start, start)
                overlap_end = min(b_end, target)
                delta = (overlap_end - overlap_start).days
                nights_in_window = max(nights_in_window, delta)

        # Approximate occupancy as nights_booked / window_days
        occupancy_rate = min(nights_in_window / window_days, 1.0)

        # --- booking velocity ---
        velocity_window = config.get("velocity_window_days", 7)
        recent_bookings = [
            b
            for b in bookings_in_window
            if _parse_date(b.get("created_dttm", "")) is not None
            and target - _parse_date(b.get("created_dttm", "")) <= timedelta(days=velocity_window)
        ]
        bookings_per_day = len(recent_bookings) / velocity_window

        # --- demand multiplier ---
        velocity_factor = config.get("velocity_factor", 0.15)
        occupancy_factor = config.get("occupancy_factor", 0.30)

        multiplier = 1.0 + (occupancy_rate * occupancy_factor) + (
            bookings_per_day * velocity_factor
        )

        # --- far-future discount ---
        far_future_cfg = config.get("far_future", {})
        if far_future_cfg:
            ff_window = far_future_cfg.get("window_days", 60)
            ff_discount = far_future_cfg.get("discount", 0.90)
            days_out = (target - datetime.now()).days
            if days_out > ff_window:
                multiplier *= ff_discount

        # --- last-minute adjustment ---
        days_to_target = (target - datetime.now()).days

        # Configurable last-minute from property config
        last_min_cfg = config.get("last_minute", {})
        if last_min_cfg:
            lm_window = last_min_cfg.get("window_days", 7)
            lm_discount = last_min_cfg.get("discount", 0.92)
            lm_threshold = last_min_cfg.get("threshold_occupancy", 0.5)
            if 1 <= days_to_target <= lm_window:
                if occupancy_rate < lm_threshold:
                    multiplier *= lm_discount
                elif occupancy_rate > 0.8:
                    multiplier *= config.get("last_minute_premium", 1.15)
        else:
            # Legacy inline last-minute behavior
            if 1 <= days_to_target <= 3:
                if occupancy_rate < 0.5:
                    multiplier *= config.get("last_minute_discount", 0.92)
                elif occupancy_rate > 0.8:
                    multiplier *= config.get("last_minute_premium", 1.15)

        raw_price = base * multiplier
        price = self._clamp(raw_price, config, property_uid)

        return PriceRecommendation(
            strategy_name=self.name,
            suggested_price=round(price, 2),
            confidence=round(min(occupancy_rate + 0.3, 1.0), 3),
            factors={
                "base_price": base,
                "occupancy_rate": round(occupancy_rate, 3),
                "bookings_per_day": round(bookings_per_day, 3),
                "demand_multiplier": round(multiplier, 3),
                "days_to_target": days_to_target,
                "far_future_discount_applied": far_future_cfg and days_to_target > far_future_cfg.get("window_days", 60),
                "last_minute_applied": last_min_cfg and 1 <= days_to_target <= last_min_cfg.get("window_days", 7),
            },
        )


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value[:19], fmt)
        except ValueError:
            pass
    return None