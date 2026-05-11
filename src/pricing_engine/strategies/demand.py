"""Demand helpers and canonical pricing-adjustments config parsing."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ..percent import pct_to_ratio
from .base import PriceRecommendation, PricingStrategy

MODULE_MULTIPLIER_FLOOR = 0.50
MODULE_MULTIPLIER_CEILING = 1.50
CONFIG_SCHEMA_VERSION_CANONICAL = 4

# All price modifiers live under pricing_adjustments.
DEFAULT_SEASONAL_MONTHS_PCT: dict[str, float] = {
    "01": 35.0,
    "02": 30.0,
    "03": 15.0,
    "04": -20.0,
    "05": -25.0,
    "06": 0.0,
    "07": 20.0,
    "08": 15.0,
    "09": -10.0,
    "10": -20.0,
    "11": -15.0,
    "12": 40.0,
}

DEFAULT_DOW_PCT: dict[str, float] = {
    "mon": 0.0,
    "tue": 0.0,
    "wed": 0.0,
    "thu": 0.0,
    "fri": 15.0,
    "sat": 15.0,
    "sun": 0.0,
}

DEFAULT_HOLIDAY_MULTIPLIERS_PCT: dict[str, float] = {
    "Christmas Day": 60.0,
    "New Year's Day": 50.0,
    "Independence Day": 40.0,
    "Thanksgiving Day": 45.0,
    "Day After Thanksgiving": 35.0,
    "Memorial Day": 20.0,
    "Labor Day": 20.0,
    "Martin Luther King Jr. Day": 25.0,
    "Presidents' Day": 25.0,
    "Washington's Birthday": 25.0,
    "Veterans Day": 15.0,
    "Juneteenth National Independence Day": 15.0,
}

DEFAULT_HOLIDAY_DEFAULT_PCT = 10.0
DEFAULT_HOLIDAY_BUFFER_DAYS = 3
DEFAULT_HOLIDAY_BUFFER_SLOPE_PCT = 5.0
DEFAULT_PRICE_ADJUST_PCT = 0.0

DEFAULT_FAR_FUTURE_WINDOW_DAYS = 60
DEFAULT_FAR_FUTURE_DISCOUNT_PCT = -10.0
DEFAULT_LAST_MINUTE_WINDOW_DAYS = 7
DEFAULT_LAST_MINUTE_DISCOUNT_PCT = -8.0
DEFAULT_LAST_MINUTE_THRESHOLD_OCCUPANCY_PCT = 50.0

_OCC_DEFAULTS = {
    "enabled": True,
    "window_days": 14,
    "target_occupancy_pct": 25.0,
    "sensitivity_pct": 20.0,
    "max_discount_pct": 10.0,
    "max_increase_pct": 10.0,
    "min_available_nights": 5,
}

_VEL_DEFAULTS = {
    "enabled": True,
    "recent_window_days": 7,
    "baseline_window_days": 60,
    "sensitivity_pct": 8.0,
    "max_discount_pct": 0.0,
    "max_increase_pct": 15.0,
    "min_recent_bookings": 2,
    "min_baseline_bookings": 3,
}


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value[:19], fmt)
        except ValueError:
            pass
    return None


def _count_booked_nights(
    bookings: list[dict[str, Any]],
    window_start: datetime,
    window_end: datetime,
) -> int:
    nights = 0
    for booking in bookings:
        b_start = _parse_date(booking.get("checkin", ""))
        b_end = _parse_date(booking.get("checkout", ""))
        if not b_start or not b_end:
            continue
        overlap_start = max(b_start, window_start)
        overlap_end = min(b_end, window_end)
        if overlap_end <= overlap_start:
            continue
        nights += (overlap_end - overlap_start).days
    return max(0, nights)


def _count_bookings_created_between(
    bookings: list[dict[str, Any]],
    start: datetime,
    end: datetime,
) -> int:
    count = 0
    for booking in bookings:
        created = _parse_date(booking.get("created_dttm", ""))
        if created and start <= created < end:
            count += 1
    return count


def calculate_occupancy_pacing_multiplier(
    *,
    enabled: bool,
    window_days: int,
    target_occupancy: float,
    sensitivity: float,
    max_discount: float,
    max_increase: float,
    min_available_nights: int,
    booked_nights: int,
    available_nights: int,
) -> dict[str, Any]:
    """Calculate occupancy pacing multiplier as bounded adjustment around 1.0."""
    inputs = {
        "enabled": bool(enabled),
        "window_days": int(window_days),
        "target_occupancy": float(target_occupancy),
        "target_occupancy_pct": round(float(target_occupancy) * 100.0, 3),
        "sensitivity": float(sensitivity),
        "sensitivity_pct": round(float(sensitivity) * 100.0, 3),
        "max_discount": float(max_discount),
        "max_discount_pct": round(float(max_discount) * 100.0, 3),
        "max_increase": float(max_increase),
        "max_increase_pct": round(float(max_increase) * 100.0, 3),
        "min_available_nights": int(min_available_nights),
        "booked_nights": int(booked_nights),
        "available_nights": int(available_nights),
    }

    if not enabled:
        return {
            "multiplier": 1.0,
            "reason": "disabled",
            "inputs": inputs,
            "computed": {
                "actual_occupancy": 0.0,
                "delta": 0.0,
                "raw_adjustment": 0.0,
                "capped_adjustment": 0.0,
            },
        }
    if available_nights < min_available_nights or available_nights <= 0:
        return {
            "multiplier": 1.0,
            "reason": "insufficient_available_nights",
            "inputs": inputs,
            "computed": {
                "actual_occupancy": 0.0,
                "delta": 0.0,
                "raw_adjustment": 0.0,
                "capped_adjustment": 0.0,
            },
        }

    actual_occupancy = booked_nights / available_nights
    delta = actual_occupancy - target_occupancy
    raw_adjustment = delta * sensitivity
    capped_adjustment = clamp(raw_adjustment, -max_discount, max_increase)
    multiplier = clamp(
        1.0 + capped_adjustment,
        MODULE_MULTIPLIER_FLOOR,
        MODULE_MULTIPLIER_CEILING,
    )

    return {
        "multiplier": multiplier,
        "reason": "ok",
        "inputs": inputs,
        "computed": {
            "actual_occupancy": actual_occupancy,
            "delta": delta,
            "raw_adjustment": raw_adjustment,
            "capped_adjustment": capped_adjustment,
        },
    }


def calculate_booking_velocity_multiplier(
    *,
    enabled: bool,
    recent_window_days: int,
    baseline_window_days: int,
    sensitivity: float,
    max_discount: float,
    max_increase: float,
    min_recent_bookings: int,
    min_baseline_bookings: int,
    recent_bookings: int,
    baseline_bookings: int,
) -> dict[str, Any]:
    """Calculate booking velocity multiplier as bounded adjustment around 1.0."""
    inputs = {
        "enabled": bool(enabled),
        "recent_window_days": int(recent_window_days),
        "baseline_window_days": int(baseline_window_days),
        "sensitivity": float(sensitivity),
        "sensitivity_pct": round(float(sensitivity) * 100.0, 3),
        "max_discount": float(max_discount),
        "max_discount_pct": round(float(max_discount) * 100.0, 3),
        "max_increase": float(max_increase),
        "max_increase_pct": round(float(max_increase) * 100.0, 3),
        "min_recent_bookings": int(min_recent_bookings),
        "min_baseline_bookings": int(min_baseline_bookings),
        "recent_bookings": int(recent_bookings),
        "baseline_bookings": int(baseline_bookings),
    }

    if not enabled:
        return {
            "multiplier": 1.0,
            "reason": "disabled",
            "inputs": inputs,
            "computed": {
                "recent_bpd": 0.0,
                "baseline_bpd": 0.0,
                "velocity_ratio": 1.0,
                "velocity_delta": 0.0,
                "raw_adjustment": 0.0,
                "capped_adjustment": 0.0,
            },
        }
    if recent_bookings < min_recent_bookings:
        return {
            "multiplier": 1.0,
            "reason": "insufficient_recent_bookings",
            "inputs": inputs,
            "computed": {
                "recent_bpd": 0.0,
                "baseline_bpd": 0.0,
                "velocity_ratio": 1.0,
                "velocity_delta": 0.0,
                "raw_adjustment": 0.0,
                "capped_adjustment": 0.0,
            },
        }
    if baseline_bookings < min_baseline_bookings:
        return {
            "multiplier": 1.0,
            "reason": "insufficient_baseline_bookings",
            "inputs": inputs,
            "computed": {
                "recent_bpd": 0.0,
                "baseline_bpd": 0.0,
                "velocity_ratio": 1.0,
                "velocity_delta": 0.0,
                "raw_adjustment": 0.0,
                "capped_adjustment": 0.0,
            },
        }

    recent_bpd = recent_bookings / max(recent_window_days, 1)
    baseline_bpd = baseline_bookings / max(baseline_window_days, 1)
    if baseline_bpd <= 0:
        return {
            "multiplier": 1.0,
            "reason": "baseline_bpd_zero",
            "inputs": inputs,
            "computed": {
                "recent_bpd": recent_bpd,
                "baseline_bpd": baseline_bpd,
                "velocity_ratio": 1.0,
                "velocity_delta": 0.0,
                "raw_adjustment": 0.0,
                "capped_adjustment": 0.0,
            },
        }

    velocity_ratio = recent_bpd / baseline_bpd
    velocity_delta = velocity_ratio - 1.0
    raw_adjustment = velocity_delta * sensitivity
    capped_adjustment = clamp(raw_adjustment, -max_discount, max_increase)
    multiplier = clamp(
        1.0 + capped_adjustment,
        MODULE_MULTIPLIER_FLOOR,
        MODULE_MULTIPLIER_CEILING,
    )

    return {
        "multiplier": multiplier,
        "reason": "ok",
        "inputs": inputs,
        "computed": {
            "recent_bpd": recent_bpd,
            "baseline_bpd": baseline_bpd,
            "velocity_ratio": velocity_ratio,
            "velocity_delta": velocity_delta,
            "raw_adjustment": raw_adjustment,
            "capped_adjustment": capped_adjustment,
        },
    }


def _seasonal_months_pct_cfg(adjustments: dict[str, Any]) -> dict[str, float]:
    out = dict(DEFAULT_SEASONAL_MONTHS_PCT)
    raw = adjustments.get("seasonal_months_pct")
    if isinstance(raw, dict):
        for month_key, value in raw.items():
            key = str(month_key).zfill(2)
            if key in out:
                out[key] = float(value)
    return out


def _dow_pct_cfg(adjustments: dict[str, Any]) -> dict[str, float]:
    out = dict(DEFAULT_DOW_PCT)
    raw = adjustments.get("dow_pct")
    if isinstance(raw, dict):
        for dow_key, value in raw.items():
            key = str(dow_key).lower()
            if key in out:
                out[key] = float(value)
    return out


def _holiday_multipliers_pct_cfg(adjustments: dict[str, Any]) -> dict[str, float]:
    out = dict(DEFAULT_HOLIDAY_MULTIPLIERS_PCT)
    raw = adjustments.get("holiday_multipliers_pct")
    if isinstance(raw, dict):
        for name, value in raw.items():
            out[str(name)] = float(value)
    return out


def _local_events_cfg(adjustments: dict[str, Any]) -> list[dict[str, Any]]:
    raw = adjustments.get("local_events")
    if not isinstance(raw, list):
        return []

    events: list[dict[str, Any]] = []
    for evt in raw:
        if not isinstance(evt, dict):
            continue
        name = str(evt.get("name", "")).strip()
        date = str(evt.get("date", "")).strip()
        if not name or not date:
            continue
        item: dict[str, Any] = {
            "name": name,
            "date": date,
            "factor_pct": float(evt.get("factor_pct", 0.0) or 0.0),
        }
        if "buffer_days" in evt:
            item["buffer_days"] = int(evt.get("buffer_days") or 0)
        if "buffer_slope_pct" in evt:
            item["buffer_slope_pct"] = float(evt.get("buffer_slope_pct") or 0.0)
        events.append(item)
    return events


def _occupancy_cfg(adjustments: dict[str, Any]) -> dict[str, Any]:
    target_occupancy_pct = float(
        adjustments.get(
            "occupancy_pacing_target_occupancy_pct",
            _OCC_DEFAULTS["target_occupancy_pct"],
        )
    )
    sensitivity_pct = float(
        adjustments.get("occupancy_pacing_sensitivity_pct", _OCC_DEFAULTS["sensitivity_pct"])
    )
    max_discount_pct = float(
        adjustments.get("occupancy_pacing_max_discount_pct", _OCC_DEFAULTS["max_discount_pct"])
    )
    max_increase_pct = float(
        adjustments.get("occupancy_pacing_max_increase_pct", _OCC_DEFAULTS["max_increase_pct"])
    )

    return {
        "enabled": bool(adjustments.get("occupancy_pacing_enabled", _OCC_DEFAULTS["enabled"])),
        "window_days": int(
            adjustments.get("occupancy_pacing_window_days", _OCC_DEFAULTS["window_days"])
        ),
        "target_occupancy_pct": target_occupancy_pct,
        "target_occupancy": pct_to_ratio(target_occupancy_pct),
        "sensitivity_pct": sensitivity_pct,
        "sensitivity": pct_to_ratio(sensitivity_pct),
        "max_discount_pct": max_discount_pct,
        "max_discount": pct_to_ratio(max_discount_pct),
        "max_increase_pct": max_increase_pct,
        "max_increase": pct_to_ratio(max_increase_pct),
        "min_available_nights": int(
            adjustments.get(
                "occupancy_pacing_min_available_nights",
                _OCC_DEFAULTS["min_available_nights"],
            )
        ),
    }


def _velocity_cfg(adjustments: dict[str, Any]) -> dict[str, Any]:
    sensitivity_pct = float(
        adjustments.get("booking_velocity_sensitivity_pct", _VEL_DEFAULTS["sensitivity_pct"])
    )
    max_discount_pct = float(
        adjustments.get("booking_velocity_max_discount_pct", _VEL_DEFAULTS["max_discount_pct"])
    )
    max_increase_pct = float(
        adjustments.get("booking_velocity_max_increase_pct", _VEL_DEFAULTS["max_increase_pct"])
    )

    return {
        "enabled": bool(adjustments.get("booking_velocity_enabled", _VEL_DEFAULTS["enabled"])),
        "recent_window_days": int(
            adjustments.get(
                "booking_velocity_recent_window_days",
                _VEL_DEFAULTS["recent_window_days"],
            )
        ),
        "baseline_window_days": int(
            adjustments.get(
                "booking_velocity_baseline_window_days",
                _VEL_DEFAULTS["baseline_window_days"],
            )
        ),
        "sensitivity_pct": sensitivity_pct,
        "sensitivity": pct_to_ratio(sensitivity_pct),
        "max_discount_pct": max_discount_pct,
        "max_discount": pct_to_ratio(max_discount_pct),
        "max_increase_pct": max_increase_pct,
        "max_increase": pct_to_ratio(max_increase_pct),
        "min_recent_bookings": int(
            adjustments.get(
                "booking_velocity_min_recent_bookings",
                _VEL_DEFAULTS["min_recent_bookings"],
            )
        ),
        "min_baseline_bookings": int(
            adjustments.get(
                "booking_velocity_min_baseline_bookings",
                _VEL_DEFAULTS["min_baseline_bookings"],
            )
        ),
    }


def get_pricing_adjustments_config(config: dict[str, Any]) -> dict[str, Any]:
    """Read canonical flat pricing_adjustments config and expose typed fields."""
    schema_version = int(
        config.get("config_schema_version", CONFIG_SCHEMA_VERSION_CANONICAL)
        or CONFIG_SCHEMA_VERSION_CANONICAL
    )
    adjustments = config.get("pricing_adjustments", {}) or {}

    return {
        "config_schema_version": schema_version,
        "seasonal_months_pct": _seasonal_months_pct_cfg(adjustments),
        "dow_pct": _dow_pct_cfg(adjustments),
        "price_adjust_pct": float(adjustments.get("price_adjust_pct", DEFAULT_PRICE_ADJUST_PCT) or 0.0),
        "holiday_buffer_days": int(
            adjustments.get("holiday_buffer_days", DEFAULT_HOLIDAY_BUFFER_DAYS)
        ),
        "holiday_buffer_slope_pct": float(
            adjustments.get("holiday_buffer_slope_pct", DEFAULT_HOLIDAY_BUFFER_SLOPE_PCT)
            or 0.0
        ),
        "holiday_multipliers_pct": _holiday_multipliers_pct_cfg(adjustments),
        "holiday_default_pct": float(
            adjustments.get("holiday_default_pct", DEFAULT_HOLIDAY_DEFAULT_PCT)
            or 0.0
        ),
        "local_events": _local_events_cfg(adjustments),
        "far_future": {
            "window_days": int(
                adjustments.get("far_future_window_days", DEFAULT_FAR_FUTURE_WINDOW_DAYS)
            ),
            "discount_pct": float(
                adjustments.get("far_future_discount_pct", DEFAULT_FAR_FUTURE_DISCOUNT_PCT)
                or 0.0
            ),
        },
        "last_minute": {
            "window_days": int(
                adjustments.get("last_minute_window_days", DEFAULT_LAST_MINUTE_WINDOW_DAYS)
            ),
            "discount_pct": float(
                adjustments.get(
                    "last_minute_discount_pct",
                    DEFAULT_LAST_MINUTE_DISCOUNT_PCT,
                )
                or 0.0
            ),
            "threshold_occupancy_pct": float(
                adjustments.get(
                    "last_minute_threshold_occupancy_pct",
                    DEFAULT_LAST_MINUTE_THRESHOLD_OCCUPANCY_PCT,
                )
                or 0.0
            ),
        },
        "occupancy_pacing": _occupancy_cfg(adjustments),
        "booking_velocity": _velocity_cfg(adjustments),
    }


class DemandStrategy(PricingStrategy):
    """Compatibility strategy that surfaces pacing/velocity diagnostics."""

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
        target = datetime.strptime(date, "%Y-%m-%d")
        cfg = get_pricing_adjustments_config(config)
        occ_cfg = cfg["occupancy_pacing"]
        vel_cfg = cfg["booking_velocity"]

        occ_start = target - timedelta(days=occ_cfg["window_days"])
        booked_nights = _count_booked_nights(bookings_in_window, occ_start, target)
        available_nights = max(0, occ_cfg["window_days"] - booked_nights)

        occ = calculate_occupancy_pacing_multiplier(
            booked_nights=booked_nights,
            available_nights=available_nights,
            **{
                k: occ_cfg[k]
                for k in (
                    "enabled",
                    "window_days",
                    "target_occupancy",
                    "sensitivity",
                    "max_discount",
                    "max_increase",
                    "min_available_nights",
                )
            },
        )

        recent_start = target - timedelta(days=vel_cfg["recent_window_days"])
        baseline_start = target - timedelta(days=vel_cfg["baseline_window_days"])
        recent_bookings = _count_bookings_created_between(bookings_in_window, recent_start, target)
        baseline_bookings = _count_bookings_created_between(bookings_in_window, baseline_start, target)

        vel = calculate_booking_velocity_multiplier(
            recent_bookings=recent_bookings,
            baseline_bookings=baseline_bookings,
            **{
                k: vel_cfg[k]
                for k in (
                    "enabled",
                    "recent_window_days",
                    "baseline_window_days",
                    "sensitivity",
                    "max_discount",
                    "max_increase",
                    "min_recent_bookings",
                    "min_baseline_bookings",
                )
            },
        )

        demand_multiplier = occ["multiplier"] * vel["multiplier"]
        return PriceRecommendation(
            strategy_name=self.name,
            suggested_price=round(self._base_price(config, property_uid) * demand_multiplier, 2),
            confidence=0.75,
            factors={
                "demand_multiplier": round(demand_multiplier, 4),
                "occupancy_pacing": occ,
                "booking_velocity": vel,
            },
        )
