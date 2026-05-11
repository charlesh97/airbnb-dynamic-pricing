"""Event, seasonal, holiday, and temporal pricing strategy."""

from __future__ import annotations

from datetime import date as DateClass
from datetime import datetime, timedelta
from typing import Any

from ..percent import multiplier_to_pct, pct_to_multiplier, pct_to_ratio
from .base import PriceRecommendation, PricingStrategy
from .demand import get_pricing_adjustments_config


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value[:19], fmt)
        except ValueError:
            pass
    return None


def get_holiday_events(year: int, state: str | None = None) -> list[dict[str, Any]]:
    """Return official holidays for a given year as event dicts."""
    import holidays

    if state:
        try:
            hols = holidays.US(subdiv=state, years=year)
        except Exception:
            hols = holidays.US(years=year)
    else:
        hols = holidays.US(years=year)

    events: list[dict[str, Any]] = []
    for d, name in sorted(hols.items()):
        events.append({
            "name": name,
            "date": d.strftime("%Y-%m-%d"),
            "mm-dd": d.strftime("%m-%d"),
        })
    return events


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


def _resolve_event_date(evt: dict[str, Any], target_year: int) -> DateClass | None:
    """Parse an event's date field — YYYY-MM-DD or MM-DD format."""
    raw = evt.get("date", "")
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%m-%d"):
        try:
            if fmt == "%m-%d":
                return datetime.strptime(f"{target_year}-{raw}", "%Y-%m-%d").date()
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    return None


def _holiday_info(
    target: datetime,
    *,
    state: str,
    adjustments: dict[str, Any],
) -> dict[str, Any]:
    """Return best holiday or local-event match for target date."""
    def _is_stronger_effect(candidate: float, current: float) -> bool:
        return abs(candidate - 1.0) > abs(current - 1.0)

    target_ord = target.date().toordinal()
    target_year = target.year

    global_buf_days = int(adjustments.get("holiday_buffer_days", 0) or 0)
    global_buf_slope_pct = float(adjustments.get("holiday_buffer_slope_pct", 0.0) or 0.0)
    global_buf_slope_ratio = pct_to_ratio(global_buf_slope_pct)

    holiday_pct_map = adjustments.get("holiday_multipliers_pct", {}) or {}
    default_holiday_pct = float(adjustments.get("holiday_default_pct", 0.0) or 0.0)

    best: dict[str, Any] = {
        "match": False,
        "is_holiday": False,
        "name": None,
        "effective_multiplier": 1.0,
        "effective_pct": 0.0,
        "event_multiplier": 1.0,
        "event_pct": 0.0,
        "buffer_applied": False,
        "buffer_days": global_buf_days,
        "buffer_slope_pct": global_buf_slope_pct,
        "day_offset": 0,
        "source": None,
    }

    # 1) Curated local events.
    for evt in adjustments.get("local_events", []):
        if not isinstance(evt, dict):
            continue
        evt_date = _resolve_event_date(evt, target_year)
        if evt_date is None:
            continue

        evt_ord = evt_date.toordinal()
        diff = abs(target_ord - evt_ord)

        factor_pct = float(evt.get("factor_pct", 0.0) or 0.0)
        factor_mult = pct_to_multiplier(factor_pct)

        buf_days = int(evt.get("buffer_days", global_buf_days) or 0)
        buf_slope_pct = float(evt.get("buffer_slope_pct", global_buf_slope_pct) or 0.0)
        buf_slope_ratio = pct_to_ratio(buf_slope_pct)

        if diff == 0:
            return {
                "match": True,
                "is_holiday": True,
                "name": evt.get("name"),
                "effective_multiplier": factor_mult,
                "effective_pct": factor_pct,
                "event_multiplier": factor_mult,
                "event_pct": factor_pct,
                "buffer_applied": False,
                "buffer_days": buf_days,
                "buffer_slope_pct": buf_slope_pct,
                "day_offset": 0,
                "source": "local",
            }

        if buf_days > 0 and diff <= buf_days:
            buffered = factor_mult * (1.0 - (buf_slope_ratio * diff))
            if _is_stronger_effect(buffered, float(best["effective_multiplier"])):
                best = {
                    "match": True,
                    "is_holiday": True,
                    "name": evt.get("name"),
                    "effective_multiplier": float(round(buffered, 4)),
                    "effective_pct": float(round(multiplier_to_pct(buffered), 3)),
                    "event_multiplier": factor_mult,
                    "event_pct": factor_pct,
                    "buffer_applied": True,
                    "buffer_days": buf_days,
                    "buffer_slope_pct": buf_slope_pct,
                    "day_offset": int(diff),
                    "source": "local",
                }

    if best["match"]:
        return best

    # 2) Auto holidays.
    auto_holidays = get_holiday_events(target_year, state)
    for evt in auto_holidays:
        evt_date = datetime.strptime(evt["date"], "%Y-%m-%d").date()
        diff = abs(target_ord - evt_date.toordinal())

        name = evt["name"]
        holiday_pct = float(holiday_pct_map.get(name, default_holiday_pct))
        holiday_mult = pct_to_multiplier(holiday_pct)

        if diff == 0:
            return {
                "match": True,
                "is_holiday": True,
                "name": name,
                "effective_multiplier": holiday_mult,
                "effective_pct": holiday_pct,
                "event_multiplier": holiday_mult,
                "event_pct": holiday_pct,
                "buffer_applied": False,
                "buffer_days": global_buf_days,
                "buffer_slope_pct": global_buf_slope_pct,
                "day_offset": 0,
                "source": "auto",
            }

        if global_buf_days > 0 and diff <= global_buf_days:
            buffered = holiday_mult * (1.0 - (global_buf_slope_ratio * diff))
            if _is_stronger_effect(buffered, float(best["effective_multiplier"])):
                best = {
                    "match": True,
                    "is_holiday": True,
                    "name": name,
                    "effective_multiplier": float(round(buffered, 4)),
                    "effective_pct": float(round(multiplier_to_pct(buffered), 3)),
                    "event_multiplier": holiday_mult,
                    "event_pct": holiday_pct,
                    "buffer_applied": True,
                    "buffer_days": global_buf_days,
                    "buffer_slope_pct": global_buf_slope_pct,
                    "day_offset": int(diff),
                    "source": "auto",
                }

    return best


class EventStrategy(PricingStrategy):
    """Seasonal, holiday, local-event, and temporal pricing components."""

    name = "event"

    def compute(  # noqa: C901
        self,
        *,
        property_uid: str,
        date: str,
        calendar_entry: dict[str, Any] | None,
        bookings_in_window: list[dict[str, Any]],
        config: dict[str, Any],
    ) -> PriceRecommendation:
        target = datetime.strptime(date, "%Y-%m-%d")
        base = self._seasonal_base_price(config, property_uid, target)
        month_key = target.strftime("%m")
        dow = target.strftime("%a").lower()

        adj = get_pricing_adjustments_config(config)

        seasonality_pct = float(adj.get("seasonal_months_pct", {}).get(month_key, 0.0))
        seasonality_multiplier = pct_to_multiplier(seasonality_pct)

        state = str(config.get("state", "CA") or "CA")
        holiday_info = _holiday_info(target, state=state, adjustments=adj)
        is_holiday = bool(holiday_info.get("is_holiday"))
        holiday_name = holiday_info.get("name")
        holiday_source = holiday_info.get("source")
        holiday_effective_multiplier = float(holiday_info.get("effective_multiplier", 1.0))
        holiday_effective_pct = float(holiday_info.get("effective_pct", 0.0))
        holiday_component_multiplier = 1.0
        holiday_component_pct = 0.0

        if is_holiday and holiday_source == "auto":
            if abs(seasonality_multiplier) > 1e-9:
                holiday_component_multiplier = holiday_effective_multiplier / seasonality_multiplier
            else:
                holiday_component_multiplier = holiday_effective_multiplier
            holiday_component_pct = multiplier_to_pct(holiday_component_multiplier)
        elif is_holiday and holiday_source == "local":
            holiday_component_multiplier = holiday_effective_multiplier
            holiday_component_pct = holiday_effective_pct

        far_future_cfg = adj.get("far_future", {}) or {}
        ff_window_days = int(far_future_cfg.get("window_days", 0) or 0)
        ff_discount_pct = float(far_future_cfg.get("discount_pct", 0.0) or 0.0)
        far_future_multiplier = 1.0
        far_future_applied = False
        days_out = (target.date() - datetime.now().date()).days
        if days_out > ff_window_days:
            far_future_multiplier = pct_to_multiplier(ff_discount_pct)
            far_future_applied = True

        last_min_cfg = adj.get("last_minute", {}) or {}
        lm_window_days = max(1, int(last_min_cfg.get("window_days", 1) or 1))
        lm_discount_pct = float(last_min_cfg.get("discount_pct", 0.0) or 0.0)
        lm_threshold_occupancy_pct = float(last_min_cfg.get("threshold_occupancy_pct", 100.0) or 100.0)
        lm_threshold_ratio = pct_to_ratio(lm_threshold_occupancy_pct)

        last_minute_multiplier = 1.0
        last_minute_applied = False
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        close_in_days_out = max(0, (target - today).days)
        if close_in_days_out <= lm_window_days:
            window_start = target - timedelta(days=lm_window_days)
            occupancy_rate = _occupancy_rate_for_window(
                bookings_in_window=bookings_in_window,
                window_start=window_start,
                window_end=target,
            )
            if occupancy_rate <= lm_threshold_ratio:
                last_minute_multiplier = pct_to_multiplier(lm_discount_pct)
                last_minute_applied = True

        dow_pct = float(adj.get("dow_pct", {}).get(dow, 0.0))
        dow_multiplier = pct_to_multiplier(dow_pct)

        event_multiplier = (
            seasonality_multiplier
            * holiday_component_multiplier
            * far_future_multiplier
            * last_minute_multiplier
        )
        full_multiplier = event_multiplier * dow_multiplier

        raw_price = base * full_multiplier
        price = self._clamp(raw_price, config, property_uid)

        factors: dict[str, Any] = {
            "base_price": round(base, 2),
            "month": month_key,
            "dow": dow,
            "seasonal_source": "pricing_adjustments",
            "seasonality_pct": round(seasonality_pct, 3),
            "seasonality_multiplier": round(seasonality_multiplier, 4),
            "holiday_component_pct": round(holiday_component_pct, 3),
            "holiday_component_multiplier": round(holiday_component_multiplier, 4),
            "holiday_effective_pct": round(holiday_effective_pct, 3),
            "holiday_effective_multiplier": round(holiday_effective_multiplier, 4),
            "is_holiday": is_holiday,
            "holiday_name": holiday_name,
            "holiday_source": holiday_source,
            "holiday_buffer_applied": bool(holiday_info.get("buffer_applied", False)),
            "holiday_buffer_days": int(holiday_info.get("buffer_days", 0)),
            "holiday_buffer_slope_pct": float(holiday_info.get("buffer_slope_pct", 0.0)),
            "holiday_buffer_day_offset": int(holiday_info.get("day_offset", 0)),
            "far_future_multiplier": round(far_future_multiplier, 4),
            "far_future_discount_pct": round(ff_discount_pct, 3),
            "far_future_window_days": ff_window_days,
            "far_future_applied": far_future_applied,
            "last_minute_multiplier": round(last_minute_multiplier, 4),
            "last_minute_discount_pct": round(lm_discount_pct, 3),
            "last_minute_window_days": lm_window_days,
            "last_minute_threshold_occupancy_pct": round(lm_threshold_occupancy_pct, 3),
            "last_minute_applied": last_minute_applied,
            "dow_pct": round(dow_pct, 3),
            "dow_multiplier": round(dow_multiplier, 4),
            "event_multiplier": round(event_multiplier, 4),
            "full_multiplier": round(full_multiplier, 4),
            "local_event_applied": holiday_name if holiday_source == "local" else None,
        }

        confidence = 0.85 if abs(full_multiplier - 1.0) >= 0.001 else 0.50
        return PriceRecommendation(
            strategy_name=self.name,
            suggested_price=round(price, 2),
            confidence=confidence,
            factors=factors,
        )
