"""Event and seasonal calendar pricing strategy."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .base import PriceRecommendation, PricingStrategy


# Default seasonal multiplier table (month-day) -> multiplier
# Multipliers > 1.0 = premium; < 1.0 = discount
DEFAULT_SEASONAL: dict[str, float] = {
    # Holidays
    "12-24": 1.40,  # Christmas Eve
    "12-25": 1.60,  # Christmas
    "12-31": 1.50,  # New Year's Eve
    "01-01": 1.40,  # New Year's Day
    "07-04": 1.30,  # Independence Day
    "11-28": 1.35,  # Thanksgiving
    "11-29": 1.25,  # Thanksgiving weekend
    "12-23": 1.30,  # Christmas week
    "12-26": 1.30,  # Christmas week
    "12-27": 1.30,
    "12-28": 1.30,
    "12-29": 1.35,
    "12-30": 1.40,
    # Summer peak
    "06-01": 1.10,
    "06-15": 1.15,
    "07-01": 1.20,
    "07-15": 1.25,
    "08-01": 1.20,
    "08-15": 1.15,
    "08-31": 1.10,
    # Spring break
    "03-15": 1.20,
    "03-16": 1.25,
    "03-22": 1.20,
}

# Default DOW multipliers (applied on top of seasonal)
# None means no DOW-specific adjustment (use seasonal only)
DEFAULT_DOW_MULTIPLIERS: dict[str, float] = {
    "mon": 1.0,
    "tue": 1.0,
    "wed": 1.0,
    "thu": 1.0,
    "fri": 1.15,   # Friday night premium
    "sat": 1.15,   # Saturday night premium
    "sun": 1.0,
}

DEFAULT_WEEKEND_MULTIPLIER = 1.15


class EventStrategy(PricingStrategy):
    """Seasonal and local event driven pricing."""

    name = "event"

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
        month_day = target.strftime("%m-%d")
        dow = target.strftime("%a").lower()  # 'mon', 'tue', etc.
        weekday = target.weekday()  # 0=Mon, 6=Sun

        # 1. Seasonal multiplier — from property config or default
        seasonal = config.get("seasonal_multipliers", DEFAULT_SEASONAL)
        multiplier = seasonal.get(month_day, 1.0)

        # Event overrides take precedence
        events = config.get("event_overrides", {})
        if month_day in events:
            multiplier = events[month_day]

        # 2. DOW multiplier — from property config or default
        dow_mults = config.get("dow_multipliers", DEFAULT_DOW_MULTIPLIERS)
        dow_multiplier = dow_mults.get(dow, 1.0)
        multiplier *= dow_multiplier

        # 3. Weekend premium (Fri/Sat nights) — only if not embedded in DOW multiplier
        # Use weekend_multiplier override if provided; otherwise respect DOW settings
        # If DOW has an explicit fri/sat multiplier > 1, skip the extra weekend bump
        weekend_override = config.get("weekend_multiplier")
        if weekend_override is not None:
            if dow not in dow_mults or dow_mults.get(dow, 1.0) == 1.0:
                # DOW had no explicit adjustment — apply weekend override
                if weekday in (4, 5):  # Fri, Sat
                    multiplier *= weekend_override
        else:
            # No override — use default behavior if DOW is still at 1.0
            if dow == "fri" and dow_mults.get("fri", 1.0) == 1.0:
                multiplier *= DEFAULT_WEEKEND_MULTIPLIER
            elif dow == "sat" and dow_mults.get("sat", 1.0) == 1.0:
                multiplier *= DEFAULT_WEEKEND_MULTIPLIER

        # 4. Far-future discount
        far_future_cfg = config.get("far_future", {})
        if far_future_cfg:
            window_days = far_future_cfg.get("window_days", 60)
            discount = far_future_cfg.get("discount", 0.90)
            from datetime import timedelta
            days_out = (target.date() - datetime.now().date()).days
            if days_out > window_days:
                multiplier *= discount

        raw_price = base * multiplier
        price = self._clamp(raw_price, config, property_uid)

        return PriceRecommendation(
            strategy_name=self.name,
            suggested_price=round(price, 2),
            confidence=0.85 if multiplier != 1.0 else 0.50,
            factors={
                "base_price": base,
                "seasonal_multiplier": round(multiplier / max(dow_multiplier, 1e-9), 3),
                "dow_multiplier": round(dow_multiplier, 3),
                "month_day": month_day,
                "dow": dow,
                "day_of_week": weekday,
                "is_weekend_night": weekday in (4, 5),
                "far_future_discount_applied": far_future_cfg and (target.date() - datetime.now().date()).days > far_future_cfg.get("window_days", 60),
            },
        )