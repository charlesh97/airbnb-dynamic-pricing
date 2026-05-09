"""Yield / opportunity cost pricing strategy.

This is the most sophisticated strategy. It considers:
- How many nights have recently been booked (revenue baseline)
- Lead time to target date (greater uncertainty = wider price range)
- Churn discount (high churn = discount, inverse of old churn_prob logic)
- Trade-off: shorter high-rate stay vs longer low-rate stay
- Conditional last-minute adjustments (context-aware, not a universal multiplier)
"""

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
    # Thanksgiving (2026 specific — adjust year to taste)
    {"name": "Thanksgiving",       "date": "11-26", "multiplier": 1.45, "buffer_days": 4,  "buffer_slope": 0.05},
    {"name": "Thanksgiving Fri",   "date": "11-27", "multiplier": 1.35, "buffer_days": 0,  "buffer_slope": 0.0},
    {"name": "Thanksgiving Sun",   "date": "11-29", "multiplier": 1.25, "buffer_days": 0,  "buffer_slope": 0.0},
    # MLK Weekend (3rd Monday Jan)
    {"name": "MLK Weekend",       "date": "01-19", "multiplier": 1.25, "buffer_days": 2,  "buffer_slope": 0.04},
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

# Seasonal month multipliers (updated)
SEASONAL_MONTHS = {
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


def _is_peak_season(target: datetime) -> bool:
    """Peak ski season: Dec-Feb weekends, Jul-Aug."""
    month = int(target.strftime("%m"))
    weekday = target.weekday()
    is_weekend = weekday in (4, 5)
    if month in (12, 1, 2) and is_weekend:
        return True
    if month in (7, 8):
        return True
    return False


def _is_off_season(target: datetime) -> bool:
    """Off-season: Apr, May, Sep, Oct, Nov."""
    month = int(target.strftime("%m"))
    return month in (4, 5, 9, 10, 11)


def _is_holiday_period(target: datetime, buffer_days: int = 3) -> tuple[bool, str | None, bool]:
    """Check if target date is a holiday or within buffer days of one.

    Returns (is_holiday, holiday_name, buffer_applied).
    """
    mmdd = target.strftime("%m-%d")
    target_ord = target.date().toordinal()

    for evt in HOLIDAY_CALENDAR:
        evt_mmdd = evt["date"]
        evt_parts = evt_mmdd.split("-")
        if len(evt_parts) != 2:
            continue
        try:
            evt_ord = DateClass(target.year, int(evt_parts[0]), int(evt_parts[1])).toordinal()
        except ValueError:
            continue
        diff = abs(target_ord - evt_ord)
        if diff == 0:
            return True, evt["name"], False
        if diff <= buffer_days:
            return True, evt["name"], True
    return False, None, False


def _is_orphan_gap(
    target_date: str,
    calendar_entry: dict[str, Any] | None,
    bookings_in_window: list[dict[str, Any]],
) -> bool:
    """Check if this night is an orphan gap — adjacent nights are booked but not this one.

    A night is an orphan if the night before OR night after is booked,
    but the target night itself is not.
    """
    from datetime import date
    try:
        target = datetime.strptime(target_date, "%Y-%m-%d").date()
    except ValueError:
        return False

    # Build a set of booked date strings
    booked_dates: set[str] = set()
    for b in bookings_in_window:
        checkin = _parse_date(b.get("checkin", ""))
        checkout = _parse_date(b.get("checkout", ""))
        if checkin and checkout:
            current = checkin
            while current < checkout:
                booked_dates.add(current.strftime("%Y-%m-%d"))
                current += timedelta(days=1)

    if target.strftime("%Y-%m-%d") in booked_dates:
        return False  # this night is actually booked, not a gap

    before = (target - timedelta(days=1)).strftime("%Y-%m-%d")
    after = (target + timedelta(days=1)).strftime("%Y-%m-%d")
    return before in booked_dates or after in booked_dates


def compute_last_minute_adjustment(
    days_out: int,
    occupancy_rate: float,
    is_weekend: bool,
    is_peak_season: bool,
    is_off_season: bool,
    is_holiday_period: bool,
    holiday_name: str | None,
    is_orphan_gap: bool,
    seasonal_multiplier: float,
) -> tuple[float, str]:
    """Compute context-aware last-minute adjustment.

    Unlike the old universal multiplier, this returns different factors
    depending on the booking context.

    Returns (adjustment_factor, reasoning_string).
    """
    low_occupancy = occupancy_rate < 0.30
    weekday = not is_weekend

    # same day
    if days_out == 0:
        if is_holiday_period and is_peak_season:
            return 1.00, "same_day: holiday/peak weekend — hold"
        if is_orphan_gap:
            return 0.70, "same_day: orphan gap — soft discount"
        return 0.75, "same_day: standard — gentle discount"

    # 1 day out
    if days_out == 1:
        if is_holiday_period:
            return 1.10, f"1_day: {holiday_name or 'holiday'} — premium"
        if is_off_season and low_occupancy:
            return 0.78, "1_day: off-season + low occupancy — discount"
        if is_off_season and weekday:
            return 0.85, "1_day: off-season weekday — soft discount"
        return 1.00, "1_day: normal — hold"

    # 2-3 days out
    if days_out in (2, 3):
        if is_holiday_period:
            return 1.10, f"2-3_day: {holiday_name or 'holiday'} — premium"
        if is_off_season and is_weekend and low_occupancy:
            return 0.92, "2-3_day: off-season weekend + low occupancy — slight discount"
        if is_off_season and weekday and low_occupancy:
            return 0.88, "2-3_day: off-season weekday + low occupancy — discount"
        if is_orphan_gap:
            return 0.80, "2-3_day: orphan gap — discount"
        return 1.00, "2-3_day: normal — hold"

    # 4-6 days out
    if 4 <= days_out <= 6:
        if is_holiday_period:
            return 1.15, f"4-6_day: {holiday_name or 'holiday'} — premium"
        if is_peak_season and is_weekend:
            return 1.05, "4-6_day: peak season weekend — slight premium"
        if is_off_season and weekday and low_occupancy:
            return 0.90, "4-6_day: off-season weekday + low occupancy — discount"
        return 1.00, "4-6_day: normal — hold"

    # 7+ days out — no last-minute adjustment
    return 1.00, "7+ days: no last-minute adjustment"


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
        days_out = max(0, (target - datetime.now()).days)

        # ─── Lead-time buckets (updated factors) ───────────────────────────
        if days_out > 30:
            lead_bucket = "advance"
            lead_factor = config.get("advance_lead_factor", 1.00)
        elif days_out > 14:
            lead_bucket = "mid"
            lead_factor = config.get("mid_lead_factor", 1.05)
        elif days_out > 7:
            lead_bucket = "short"
            lead_factor = config.get("short_lead_factor", 1.08)
        else:
            lead_bucket = "last_minute"
            lead_factor = config.get("last_minute_lead_factor", 1.05)

        # ─── Recent nights booked (opportunity cost) ────────────────────────
        recent_nights = sum(
            1
            for b in bookings_in_window
            if _parse_date(b.get("checkout", "")) is not None
            and (datetime.now() - _parse_date(b.get("checkout", ""))).days < 30
        )

        # ─── Churn discount (inverse of old churn_prob logic) ──────────────
        # Old: multiplier = lead × opp × (1 + churn_prob)   ← churn raised price
        # New: multiplier = lead × opp × (1 - churn_discount) ← high churn = discount
        churn_prob = config.get("base_churn_probability", 0.10)

        current_price_raw = calendar_entry.get("price") if calendar_entry else None
        if current_price_raw and base > 0:
            try:
                current_price = float(current_price_raw)
            except (TypeError, ValueError):
                current_price = None
        else:
            current_price = None

        if current_price is not None:
            price_ratio = current_price / base
            if price_ratio > 1.25:
                churn_prob = min(churn_prob + 0.15, 0.90)
            elif price_ratio > 1.10:
                churn_prob = min(churn_prob + 0.08, 0.90)
            elif price_ratio < 0.90:
                churn_prob = max(churn_prob - 0.05, 0.01)

        # churn_discount is derived as inverse of churn probability
        # High churn → larger discount (discourage raising prices when booking is fragile)
        churn_discount = round(churn_prob / (1.0 + churn_prob), 4)

        # ─── Opportunity cost ───────────────────────────────────────────────
        opportunity_threshold = config.get("opportunity_threshold_nights", 7)
        if recent_nights < opportunity_threshold:
            opportunity_factor = config.get("low_opportunity_factor", 1.18)
        else:
            opportunity_factor = config.get("high_opportunity_factor", 1.05)

        # ─── Core multiplier (before last-minute override) ────────────────
        multiplier = lead_factor * opportunity_factor * (1 - churn_discount)

        # ─── Conditional last-minute adjustment ───────────────────────────
        # Gather context flags
        weekday = target.weekday()
        is_weekend = weekday in (4, 5)

        is_peak = _is_peak_season(target)
        is_off = _is_off_season(target)

        # Holiday check uses default buffer of 3 from config or holiday calendar
        buffer_days = config.get("holiday_buffer_days", 3)
        is_holiday_period, holiday_name, _ = _is_holiday_period(target, buffer_days=buffer_days)

        # Check for orphan gap
        orphan = _is_orphan_gap(date, calendar_entry, bookings_in_window)

        # Seasonal month multiplier for context
        month_key = target.strftime("%m")
        seasonal_mult = SEASONAL_MONTHS.get(month_key, 1.0)

        # Occupancy rate needed for last-minute logic
        window_days = config.get("demand_window_days", 14)
        start = target - timedelta(days=window_days)
        nights_in_window = 0
        for b in bookings_in_window:
            b_start = _parse_date(b.get("checkin", ""))
            b_end = _parse_date(b.get("checkout", ""))
            if b_start and b_end:
                overlap_start = max(b_start, start)
                overlap_end = min(b_end, target)
                delta = (overlap_end - overlap_start).days
                nights_in_window = max(nights_in_window, delta)
        occupancy_rate = min(nights_in_window / window_days, 1.0)

        last_min_adj, last_min_reasoning = compute_last_minute_adjustment(
            days_out=days_out,
            occupancy_rate=occupancy_rate,
            is_weekend=is_weekend,
            is_peak_season=is_peak,
            is_off_season=is_off,
            is_holiday_period=is_holiday_period,
            holiday_name=holiday_name,
            is_orphan_gap=orphan,
            seasonal_multiplier=seasonal_mult,
        )

        # Apply last-minute adjustment to multiplier
        final_multiplier = multiplier * last_min_adj

        raw_price = base * final_multiplier
        price = self._clamp(raw_price, config, property_uid)

        return PriceRecommendation(
            strategy_name=self.name,
            suggested_price=round(price, 2),
            confidence=round(1.0 - churn_prob, 3),
            factors={
                "base_price": base,
                "lead_bucket": lead_bucket,
                "lead_factor": round(lead_factor, 3),
                "churn_discount": churn_discount,
                "opportunity_factor": round(opportunity_factor, 3),
                "final_multiplier": round(final_multiplier, 3),
                "last_minute_adjustment": round(last_min_adj, 3),
                "last_minute_reasoning": last_min_reasoning,
                "days_out": days_out,
                "reasoning": last_min_reasoning,
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