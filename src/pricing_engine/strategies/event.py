"""Event and seasonal calendar pricing strategy."""

from __future__ import annotations

from datetime import date as DateClass
from datetime import datetime, timedelta
from typing import Any

from .base import PriceRecommendation, PricingStrategy


# ─── Holiday Calendar ──────────────────────────────────────────────────────────

HOLIDAY_CALENDAR = [
    # Christmas/New Year
    {"name": "Christmas Eve",     "date": "12-24", "multiplier": 1.50, "buffer_days": 3,  "buffer_slope": 0.05},
    {"name": "Christmas Day",     "date": "12-25", "multiplier": 1.60, "buffer_days": 3,  "buffer_slope": 0.05},
    {"name": "New Year's Eve",    "date": "12-31", "multiplier": 1.60, "buffer_days": 3,  "buffer_slope": 0.05},
    {"name": "New Year's Day",    "date": "01-01", "multiplier": 1.50, "buffer_days": 3,  "buffer_slope": 0.05},
    # July 4th
    {"name": "July 4th",          "date": "07-04", "multiplier": 1.40, "buffer_days": 2,  "buffer_slope": 0.07},
    # Thanksgiving (2026)
    {"name": "Thanksgiving",       "date": "11-26", "multiplier": 1.45, "buffer_days": 4,  "buffer_slope": 0.05},
    {"name": "Thanksgiving Fri",   "date": "11-27", "multiplier": 1.35, "buffer_days": 0,  "buffer_slope": 0.0},
    {"name": "Thanksgiving Sun",   "date": "11-29", "multiplier": 1.25, "buffer_days": 0,  "buffer_slope": 0.0},
    # MLK Weekend (3rd Monday Jan)
    {"name": "MLK Weekend",        "date": "01-19", "multiplier": 1.25, "buffer_days": 2, "buffer_slope": 0.04},
    # Presidents' Day Weekend (3rd Monday Feb)
    {"name": "Presidents' Day",    "date": "02-16", "multiplier": 1.25, "buffer_days": 2,  "buffer_slope": 0.04},
    # Memorial Day (last Monday May)
    {"name": "Memorial Day",       "date": "05-25", "multiplier": 1.20, "buffer_days": 2,  "buffer_slope": 0.05},
    # Labor Day (1st Monday Sep)
    {"name": "Labor Day",          "date": "09-07", "multiplier": 1.20, "buffer_days": 2,  "buffer_slope": 0.05},
    # Ski school breaks
    {"name": "President's Week",   "date": "02-13", "multiplier": 1.30, "buffer_days": 3,  "buffer_slope": 0.04},
    {"name": "Spring Break",       "date": "03-20", "multiplier": 1.20, "buffer_days": 3,  "buffer_slope": 0.04},
]

# New monthly seasonal multipliers (updated spec)
SEASONAL_MONTHS: dict[str, float] = {
    "01": 1.35,  # Jan — peak ski
    "02": 1.30,  # Feb — peak ski
    "03": 1.15,  # Mar — ski shoulder
    "04": 0.80,  # Apr — off-season
    "05": 0.75,  # May — off-season
    "06": 1.00,  # Jun — normal
    "07": 1.20,  # Jul — summer peak
    "08": 1.15,  # Aug — summer peak
    "09": 0.90,  # Sep — off-season
    "10": 0.80,  # Oct — off-season
    "11": 0.85,  # Nov — off-season (pre-holiday)
    "12": 1.40,  # Dec — holiday peak
}

# Default DOW multipliers (applied on top of seasonal)
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

# Legacy default seasonal multiplier table (per-MM-DD, for fallback)
DEFAULT_SEASONAL: dict[str, float] = {
    "12-24": 1.40, "12-25": 1.60, "12-31": 1.50,
    "01-01": 1.40, "07-04": 1.30,
    "11-28": 1.35, "11-29": 1.25,
    "12-23": 1.30, "12-26": 1.30, "12-27": 1.30,
    "12-28": 1.30, "12-29": 1.35, "12-30": 1.40,
    "06-01": 1.10, "06-15": 1.15, "07-01": 1.20,
    "07-15": 1.25, "08-01": 1.20, "08-15": 1.15,
    "08-31": 1.10, "03-15": 1.20, "03-16": 1.25, "03-22": 1.20,
}


def _holiday_info(target: datetime) -> dict[str, Any]:
    """Check if date is a holiday or within buffer of one.

    When a date falls on multiple holidays (e.g. Dec 25 = Christmas Eve buffer + Christmas Day),
    the highest-multiplier match wins. Exact matches (diff=0) take precedence over buffer matches.
    """
    target_ord = target.date().toordinal()
    default_buffer = 3

    best = {
        "effective_multiplier": 0.0,
        "buffer_applied": False,
        "name": None,
        "event_multiplier": 1.0,
        "buffer_days": 0,
        "buffer_slope": 0.0,
        "day_offset": 0,
    }

    for evt in HOLIDAY_CALENDAR:
        evt_parts = evt["date"].split("-")
        if len(evt_parts) != 2:
            continue
        try:
            evt_ord = DateClass(target.year, int(evt_parts[0]), int(evt_parts[1])).toordinal()
        except ValueError:
            continue

        diff = abs(target_ord - evt_ord)
        buffer_days = evt.get("buffer_days", default_buffer)

        if diff == 0:
            # Exact holiday match — takes absolute precedence
            return {
                "is_holiday": True,
                "name": evt["name"],
                "effective_multiplier": float(evt["multiplier"]),
                "event_multiplier": float(evt["multiplier"]),
                "buffer_applied": False,
                "buffer_days": int(evt.get("buffer_days", default_buffer)),
                "buffer_slope": float(evt.get("buffer_slope", 0.0)),
                "day_offset": 0,
            }

        if buffer_days > 0 and diff <= buffer_days:
            slope = evt.get("buffer_slope", 0.05)
            buffered_mult = evt["multiplier"] * (1.0 - slope * diff)
            if buffered_mult > best["effective_multiplier"]:
                best = {
                    "effective_multiplier": float(round(buffered_mult, 4)),
                    "buffer_applied": True,
                    "name": evt["name"],
                    "event_multiplier": float(evt["multiplier"]),
                    "buffer_days": int(buffer_days),
                    "buffer_slope": float(slope),
                    "day_offset": int(diff),
                }

    if best["effective_multiplier"] > 0:
        return {
            "is_holiday": True,
            "name": best["name"],
            "effective_multiplier": best["effective_multiplier"],
            "event_multiplier": best["event_multiplier"],
            "buffer_applied": best["buffer_applied"],
            "buffer_days": best["buffer_days"],
            "buffer_slope": best["buffer_slope"],
            "day_offset": best["day_offset"],
        }

    return {
        "is_holiday": False,
        "name": None,
        "effective_multiplier": 1.0,
        "event_multiplier": 1.0,
        "buffer_applied": False,
        "buffer_days": 0,
        "buffer_slope": 0.0,
        "day_offset": 0,
    }


def _is_peak_season(target: datetime) -> bool:
    """Peak: Dec-Feb weekends or Jul-Aug."""
    month = int(target.strftime("%m"))
    weekday = target.weekday()
    is_weekend = weekday in (4, 5)
    if month in (12, 1, 2) and is_weekend:
        return True
    if month in (7, 8):
        return True
    return False


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
        target = datetime.strptime(date, "%Y-%m-%d")
        # Base price from seasonal_base_prices if available
        base = self._seasonal_base_price(config, property_uid, target)
        month_day = target.strftime("%m-%d")
        month_key = target.strftime("%m")
        dow = target.strftime("%a").lower()
        weekday = target.weekday()
        is_weekend_night = weekday in (4, 5)

        # 1. Monthly seasonal multiplier (from config or spec default)
        seasonal_months = config.get("seasonal_months")
        if seasonal_months and month_key in seasonal_months:
            seasonal_base = seasonal_months[month_key]
            seasonal_source = "config"
        elif month_key in SEASONAL_MONTHS:
            seasonal_base = SEASONAL_MONTHS[month_key]
            seasonal_source = "spec"
        else:
            seasonal_base = 1.0
            seasonal_source = "none"

        # 2. Holiday check — overrides multiplier if holiday
        holiday_info = _holiday_info(target)
        is_holiday = bool(holiday_info["is_holiday"])
        holiday_name = holiday_info["name"]
        holiday_mult = float(holiday_info["effective_multiplier"])
        buffer_applied = bool(holiday_info["buffer_applied"])

        # Split event seasonality into components so UI can show impacts separately.
        seasonal_multiplier = seasonal_base
        holiday_component_multiplier = 1.0
        if is_holiday:
            if seasonal_base == 0:
                holiday_component_multiplier = 1.0
                seasonal_multiplier = holiday_mult
            else:
                holiday_component_multiplier = holiday_mult / seasonal_base
                seasonal_multiplier = seasonal_base * holiday_component_multiplier

        # 3. DOW multiplier
        dow_mults = config.get("dow_multipliers", DEFAULT_DOW_MULTIPLIERS)
        dow_multiplier = dow_mults.get(dow, 1.0)

        # 4. Weekend premium (if DOW has no explicit fri/sat multiplier > 1.0)
        weekend_override = config.get("weekend_multiplier")
        if weekend_override is not None:
            if dow not in dow_mults or dow_mults.get(dow, 1.0) == 1.0:
                if is_weekend_night:
                    dow_multiplier *= weekend_override
        else:
            if dow == "fri" and dow_mults.get("fri", 1.0) == 1.0:
                dow_multiplier *= DEFAULT_WEEKEND_MULTIPLIER
            elif dow == "sat" and dow_mults.get("sat", 1.0) == 1.0:
                dow_multiplier *= DEFAULT_WEEKEND_MULTIPLIER

        # 5. Far-future discount (canonical: availability.far_future)
        availability_cfg = config.get("availability", {}) or {}
        far_future_cfg = availability_cfg.get("far_future")
        if far_future_cfg is None:
            far_future_cfg = config.get("demand_config", {}).get("far_future", {})
        far_future_applied = False
        far_future_multiplier = 1.0
        ff_window = None
        ff_discount = None
        if far_future_cfg:
            ff_window = int(far_future_cfg.get("window_days", 60))
            ff_discount = float(far_future_cfg.get("discount", 0.90))
            days_out = (target.date() - datetime.now().date()).days
            if days_out > ff_window:
                far_future_multiplier = ff_discount
                seasonal_multiplier *= far_future_multiplier
                far_future_applied = True

        # 6. Local events (per-date overrides from property JSON)
        # Skip auto-injected holidays to avoid double-counting with HOLIDAY_CALENDAR
        local_events = config.get("local_events", []) if not is_holiday else []
        local_event_applied: str | None = None
        local_event_multiplier = 1.0
        if local_events:
            local_events_map = {e["date"]: e["factor"] for e in local_events if e.get("date")}
            if date in local_events_map:
                local_factor = local_events_map[date]
                local_event_multiplier = float(local_factor)
                seasonal_multiplier *= local_event_multiplier
                local_event_applied = f"{local_factor}"

        # 7. Build factors dict
        is_peak = _is_peak_season(target)

        factors: dict[str, Any] = {
            "base_price": base,
            "seasonal_multiplier": round(seasonal_multiplier, 3),
            "dow_multiplier": round(dow_multiplier, 3),
            "month_day": month_day,
            "month": month_key,
            "dow": dow,
            "day_of_week": weekday,
            "is_weekend_night": is_weekend_night,
            "seasonal_source": seasonal_source,
            "seasonal_base_multiplier": round(float(seasonal_base), 4),
            "holiday_component_multiplier": round(float(holiday_component_multiplier), 4),
            "far_future_multiplier": round(float(far_future_multiplier), 4),
            "far_future_window_days": ff_window,
            "far_future_discount": ff_discount,
            "local_event_multiplier": round(float(local_event_multiplier), 4),
            "far_future_discount_applied": far_future_applied,
            "local_event_applied": local_event_applied,
            "is_peak_season": is_peak,
            "is_holiday_period": is_holiday,
            "holiday_name": holiday_name,
            "holiday_buffer_applied": buffer_applied,
            "holiday_buffer_days": int(holiday_info.get("buffer_days", 0)),
            "holiday_buffer_slope": float(holiday_info.get("buffer_slope", 0.0)),
            "holiday_buffer_day_offset": int(holiday_info.get("day_offset", 0)),
        }

        if is_holiday:
            factors["holiday_multiplier"] = round(holiday_mult, 3)

        # Apply DOW on top of seasonal
        multiplier = seasonal_multiplier * dow_multiplier

        raw_price = base * multiplier
        price = self._clamp(raw_price, config, property_uid)

        return PriceRecommendation(
            strategy_name=self.name,
            suggested_price=round(price, 2),
            confidence=0.85 if multiplier != 1.0 else 0.50,
            factors=factors,
        )
