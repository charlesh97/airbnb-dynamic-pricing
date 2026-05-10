"""Demand helpers for occupancy pacing and booking velocity multipliers."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .base import PriceRecommendation, PricingStrategy

MODULE_MULTIPLIER_FLOOR = 0.50
MODULE_MULTIPLIER_CEILING = 1.50
CONFIG_SCHEMA_VERSION_CANONICAL = 2
LEGACY_WRITE_UNTIL_VERSION = 2
LEGACY_READ_UNTIL_VERSION = 3


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


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
        "sensitivity": float(sensitivity),
        "max_discount": float(max_discount),
        "max_increase": float(max_increase),
        "min_available_nights": int(min_available_nights),
        "booked_nights": int(booked_nights),
        "available_nights": int(available_nights),
    }

    if not enabled:
        return {
            "multiplier": 1.0,
            "reason": "disabled",
            "inputs": inputs,
            "computed": {"actual_occupancy": 0.0, "delta": 0.0, "raw_adjustment": 0.0, "capped_adjustment": 0.0},
        }
    if available_nights < min_available_nights or available_nights <= 0:
        return {
            "multiplier": 1.0,
            "reason": "insufficient_available_nights",
            "inputs": inputs,
            "computed": {"actual_occupancy": 0.0, "delta": 0.0, "raw_adjustment": 0.0, "capped_adjustment": 0.0},
        }

    actual_occupancy = booked_nights / available_nights
    delta = actual_occupancy - target_occupancy
    raw_adjustment = delta * sensitivity
    capped_adjustment = clamp(raw_adjustment, -max_discount, max_increase)
    multiplier = clamp(1.0 + capped_adjustment, MODULE_MULTIPLIER_FLOOR, MODULE_MULTIPLIER_CEILING)

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
        "max_discount": float(max_discount),
        "max_increase": float(max_increase),
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
            "computed": {"recent_bpd": 0.0, "baseline_bpd": 0.0, "velocity_ratio": 1.0, "velocity_delta": 0.0, "raw_adjustment": 0.0, "capped_adjustment": 0.0},
        }
    if recent_bookings < min_recent_bookings:
        return {
            "multiplier": 1.0,
            "reason": "insufficient_recent_bookings",
            "inputs": inputs,
            "computed": {"recent_bpd": 0.0, "baseline_bpd": 0.0, "velocity_ratio": 1.0, "velocity_delta": 0.0, "raw_adjustment": 0.0, "capped_adjustment": 0.0},
        }
    if baseline_bookings < min_baseline_bookings:
        return {
            "multiplier": 1.0,
            "reason": "insufficient_baseline_bookings",
            "inputs": inputs,
            "computed": {"recent_bpd": 0.0, "baseline_bpd": 0.0, "velocity_ratio": 1.0, "velocity_delta": 0.0, "raw_adjustment": 0.0, "capped_adjustment": 0.0},
        }

    recent_bpd = recent_bookings / max(recent_window_days, 1)
    baseline_bpd = baseline_bookings / max(baseline_window_days, 1)
    if baseline_bpd <= 0:
        return {
            "multiplier": 1.0,
            "reason": "baseline_bpd_zero",
            "inputs": inputs,
            "computed": {"recent_bpd": recent_bpd, "baseline_bpd": baseline_bpd, "velocity_ratio": 1.0, "velocity_delta": 0.0, "raw_adjustment": 0.0, "capped_adjustment": 0.0},
        }

    velocity_ratio = recent_bpd / baseline_bpd
    velocity_delta = velocity_ratio - 1.0
    raw_adjustment = velocity_delta * sensitivity
    capped_adjustment = clamp(raw_adjustment, -max_discount, max_increase)
    multiplier = clamp(1.0 + capped_adjustment, MODULE_MULTIPLIER_FLOOR, MODULE_MULTIPLIER_CEILING)

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


def get_pricing_adjustments_config(config: dict[str, Any]) -> dict[str, Any]:
    """Read canonical adjustment config with legacy demand_config fallback."""
    schema_version = int(config.get("config_schema_version", 1) or 1)
    adjustments = config.get("pricing_adjustments", {}) or {}
    demand_cfg = config.get("demand_config", {}) or {}

    occupancy = adjustments.get("occupancy_pacing") or {}
    velocity = adjustments.get("booking_velocity") or {}

    if schema_version < LEGACY_READ_UNTIL_VERSION:
        if not occupancy:
            occupancy = {
                "enabled": True,
                "window_days": demand_cfg.get("demand_window_days", 14),
                "target_occupancy": 0.25,
                "sensitivity": float(demand_cfg.get("occupancy_factor", 0.20)),
                "max_discount": 0.10,
                "max_increase": 0.10,
                "min_available_nights": 5,
            }
        if not velocity:
            velocity = {
                "enabled": True,
                "recent_window_days": demand_cfg.get("velocity_window_days", 7),
                "baseline_window_days": 60,
                "sensitivity": 0.08,
                "max_discount": 0.00,
                "max_increase": 0.15,
                "min_recent_bookings": 2,
                "min_baseline_bookings": 3,
            }

    return {
        "config_schema_version": schema_version,
        "occupancy_pacing": {
            "enabled": bool(occupancy.get("enabled", True)),
            "window_days": int(occupancy.get("window_days", 14)),
            "target_occupancy": float(occupancy.get("target_occupancy", 0.25)),
            "sensitivity": float(occupancy.get("sensitivity", 0.20)),
            "max_discount": float(occupancy.get("max_discount", 0.10)),
            "max_increase": float(occupancy.get("max_increase", 0.10)),
            "min_available_nights": int(occupancy.get("min_available_nights", 5)),
        },
        "booking_velocity": {
            "enabled": bool(velocity.get("enabled", True)),
            "recent_window_days": int(velocity.get("recent_window_days", 7)),
            "baseline_window_days": int(velocity.get("baseline_window_days", 60)),
            "sensitivity": float(velocity.get("sensitivity", 0.08)),
            "max_discount": float(velocity.get("max_discount", 0.00)),
            "max_increase": float(velocity.get("max_increase", 0.15)),
            "min_recent_bookings": int(velocity.get("min_recent_bookings", 2)),
            "min_baseline_bookings": int(velocity.get("min_baseline_bookings", 3)),
        },
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
            **occ_cfg,
        )

        recent_start = target - timedelta(days=vel_cfg["recent_window_days"])
        baseline_start = target - timedelta(days=vel_cfg["baseline_window_days"])
        recent_bookings = _count_bookings_created_between(bookings_in_window, recent_start, target)
        baseline_bookings = _count_bookings_created_between(bookings_in_window, baseline_start, target)

        vel = calculate_booking_velocity_multiplier(
            recent_bookings=recent_bookings,
            baseline_bookings=baseline_bookings,
            **vel_cfg,
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
                "legacy_compatibility_mode": True,
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
