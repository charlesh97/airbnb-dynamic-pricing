"""Yield / opportunity cost pricing strategy.

This is the most sophisticated strategy. It considers:
- How many nights have recently been booked (revenue baseline)
- Lead time to target date (greater uncertainty = wider price range)
- Churn probability (if we raise price, will the booking stick?)
- Trade-off: shorter high-rate stay vs longer low-rate stay
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .base import PriceRecommendation, PricingStrategy


class YieldStrategy(PricingStrategy):
    """Revenue optimization via opportunity cost and lead-time pricing."""

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
        days_out = (target - datetime.now()).days

        # --- Lead-time buckets ---
        if days_out > 30:
            lead_bucket = "advance"
            lead_factor = config.get("advance_lead_factor", 1.05)
        elif days_out > 14:
            lead_bucket = "mid"
            lead_factor = config.get("mid_lead_factor", 1.10)
        elif days_out > 7:
            lead_bucket = "short"
            lead_factor = config.get("short_lead_factor", 1.15)
        else:
            lead_bucket = "last_minute"
            lead_factor = config.get("last_minute_lead_factor", 1.20)

        # --- Recent nights booked ---
        recent_nights = sum(
            1
            for b in bookings_in_window
            if _parse_date(b.get("checkout", "")) is not None
            and (datetime.now() - _parse_date(b.get("checkout", ""))).days < 30
        )

        # --- Churn probability ---
        # If price has been raised recently, churn risk increases
        current_price = calendar_entry.get("price") if calendar_entry else None
        churn_prob = config.get("base_churn_probability", 0.10)

        if current_price and base > 0:
            price_ratio = current_price / base
            if price_ratio > 1.25:
                churn_prob = min(churn_prob + 0.15, 0.90)
            elif price_ratio > 1.10:
                churn_prob = min(churn_prob + 0.08, 0.90)
            elif price_ratio < 0.90:
                churn_prob = max(churn_prob - 0.05, 0.01)

        # --- Opportunity cost: revenue lost by not having the night booked ---
        opportunity_threshold = config.get("opportunity_threshold_nights", 7)
        if recent_nights < opportunity_threshold:
            # Low booking history — opportunity cost is low; price more aggressively
            opportunity_factor = config.get("low_opportunity_factor", 1.18)
        else:
            # High bookings — be cautious; preserve occupancy
            opportunity_factor = config.get("high_opportunity_factor", 1.05)

        # --- Compute final multiplier ---
        multiplier = lead_factor * opportunity_factor * (1 + churn_prob)

        raw_price = base * multiplier
        price = self._clamp(raw_price, config, property_uid)

        return PriceRecommendation(
            strategy_name=self.name,
            suggested_price=round(price, 2),
            confidence=round(1.0 - churn_prob, 3),
            factors={
                "base_price": base,
                "lead_bucket": lead_bucket,
                "lead_factor": round(lead_factor, 3),
                "recent_nights_booked": recent_nights,
                "churn_probability": round(churn_prob, 3),
                "opportunity_factor": round(opportunity_factor, 3),
                "final_multiplier": round(multiplier, 3),
                "days_out": days_out,
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
