"""Demand-based pricing strategy — driven by occupancy and booking velocity."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .base import PriceRecommendation, PricingStrategy


# ─── Occupancy bands ───────────────────────────────────────────────────────────

def _occupancy_multiplier(occupancy_rate: float) -> float:
    """Nonlinear occupancy multiplier.

    Replaces the old linear formula:
        multiplier = 1.0 + (occupancy_rate * occupancy_factor)

    Band-based approach:
        < 0.30  → 0.90  (very low demand, soft discount)
        0.30-0.60 → 1.00 to 1.15 (linear interpolation)
        0.60-0.80 → 1.15 to 1.35
        0.80-0.95 → 1.35 to 1.60
        > 0.95  → 1.60 to 1.90
    """
    if occupancy_rate < 0.30:
        return 0.90
    if occupancy_rate <= 0.60:
        # Linear from 1.00 to 1.15 across [0.30, 0.60]
        t = (occupancy_rate - 0.30) / (0.60 - 0.30)
        return 1.00 + t * 0.15
    if occupancy_rate <= 0.80:
        # Linear from 1.15 to 1.35 across [0.60, 0.80]
        t = (occupancy_rate - 0.60) / (0.80 - 0.60)
        return 1.15 + t * 0.20
    if occupancy_rate <= 0.95:
        # Linear from 1.35 to 1.60 across [0.80, 0.95]
        t = (occupancy_rate - 0.80) / (0.95 - 0.80)
        return 1.35 + t * 0.25
    # > 0.95
    t = min((occupancy_rate - 0.95) / 0.05, 1.0)  # cap spread at 5pts
    return 1.60 + t * 0.30


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
        demand_cfg = config.get("demand_config", {})
        window_days = demand_cfg.get("demand_window_days", 14)
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
        velocity_window = demand_cfg.get("velocity_window_days", 7)
        recent_bookings = [
            b
            for b in bookings_in_window
            if _parse_date(b.get("created_dttm", "")) is not None
            and target - _parse_date(b.get("created_dttm", "")) <= timedelta(days=velocity_window)
        ]
        bookings_per_day = len(recent_bookings) / velocity_window

        # --- occupancy multiplier (band-based) ---
        occupancy_multiplier = _occupancy_multiplier(occupancy_rate)
        # Scale by occupancy_factor if configured
        occupancy_factor = demand_cfg.get("occupancy_factor", 1.0)
        if occupancy_factor != 1.0:
            occupancy_multiplier *= occupancy_factor

        # --- velocity multiplier ---
        velocity_factor = demand_cfg.get("velocity_factor", 0.15)
        velocity_multiplier = 1.0 + (bookings_per_day * velocity_factor)

        # --- far-future discount ---
        far_future_cfg = demand_cfg.get("far_future", {})
        far_future_applied = False
        if far_future_cfg:
            ff_window = far_future_cfg.get("window_days", 60)
            ff_discount = far_future_cfg.get("discount", 0.90)
            days_out = (target - datetime.now()).days
            if days_out > ff_window:
                velocity_multiplier *= ff_discount
                far_future_applied = True

        # --- demand multiplier = occupancy × velocity ---
        demand_multiplier = occupancy_multiplier * velocity_multiplier

        raw_price = base * demand_multiplier
        price = self._clamp(raw_price, config, property_uid)

        return PriceRecommendation(
            strategy_name=self.name,
            suggested_price=round(price, 2),
            confidence=round(min(occupancy_rate + 0.3, 1.0), 3),
            factors={
                "base_price": base,
                "occupancy_rate": round(occupancy_rate, 3),
                "occupancy_multiplier": round(occupancy_multiplier, 3),
                "bookings_per_day": round(bookings_per_day, 3),
                "velocity_multiplier": round(velocity_multiplier, 3),
                "demand_multiplier": round(demand_multiplier, 3),
                "far_future_discount_applied": far_future_applied,
                "last_minute_override": None,  # handled by YieldStrategy
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