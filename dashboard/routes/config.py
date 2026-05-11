"""GET/PUT config and adjustment explanations."""

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator

from ..engine_proxy import get_property_config, save_property_config
from pricing_engine.strategies.demand import (
    calculate_booking_velocity_multiplier,
    calculate_occupancy_pacing_multiplier,
    get_pricing_adjustments_config,
)

router = APIRouter(prefix="/api", tags=["config"])

LEGACY_YIELD_ONLY_KEYS = (
    "advance_lead_factor",
    "mid_lead_factor",
    "short_lead_factor",
    "last_minute_lead_factor",
    "base_churn_probability",
    "opportunity_threshold_nights",
    "low_opportunity_factor",
    "high_opportunity_factor",
)

LEGACY_SCHEMA_KEYS = (
    "demand_config",
    "seasonal_months",
    "dow_multipliers",
    "price_adjust",
    "holiday_buffer_slope",
    "holiday_multipliers",
    "holiday_default_multiplier",
    "seasonal_months_pct",
    "dow_pct",
    "price_adjust_pct",
    "holiday_multipliers_pct",
    "holiday_default_pct",
    "holiday_buffer_slope_pct",
    "holiday_buffer_days",
    "local_events",
    "local_events_config",
)


class ConfigPutRequest(BaseModel):
    """Validated property config for PUT."""

    base_price: Optional[float] = None
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    pricing_adjustments: Optional[dict[str, Any]] = None
    availability: Optional[dict[str, Any]] = None
    seasonal_base_prices: Optional[dict[str, float]] = None

    model_config = ConfigDict(extra="allow")

    @field_validator("min_price", "max_price")
    @classmethod
    def price_must_be_positive(cls, v):
        if v is not None and v < 0:
            raise ValueError("price must be non-negative")
        return v


class ExplainAdjustmentsRequest(BaseModel):
    """Payload for generating config-based explanation examples."""

    config: Optional[dict[str, Any]] = None
    property_uid: Optional[str] = None


def _strip_legacy_yield_fields(cfg: dict[str, Any]) -> None:
    """Remove stale yield-only knobs that are no longer part of active config."""
    for key in LEGACY_YIELD_ONLY_KEYS:
        cfg.pop(key, None)
    for key in LEGACY_SCHEMA_KEYS:
        cfg.pop(key, None)

    # Old availability pricing keys now live in pricing_adjustments.
    availability = cfg.get("availability", {})
    if isinstance(availability, dict):
        availability.pop("far_future", None)
        availability.pop("last_minute", None)
        availability.pop("min_stay", None)
        availability.pop("enforce_min_stay", None)

    # Old grouped pricing sections are removed; canonical schema is flat keys.
    pricing_adjustments = cfg.get("pricing_adjustments", {})
    if isinstance(pricing_adjustments, dict):
        pricing_adjustments.pop("occupancy_pacing", None)
        pricing_adjustments.pop("booking_velocity", None)


@router.get("/config/{property_uid}")
async def get_config(property_uid: str):
    """Return the raw property config JSON."""
    config = get_property_config(property_uid)
    if not config:
        raise HTTPException(status_code=404, detail=f"No config found for {property_uid}")
    return config


@router.put("/config/{property_uid}")
async def put_config(property_uid: str, body: ConfigPutRequest):
    """Save an edited property config after validating."""
    # Load current config to preserve structure and server-side fields
    current = get_property_config(property_uid)
    if not current:
        raise HTTPException(status_code=404, detail=f"No config found for {property_uid}")

    # Merge only fields that were explicitly sent (exclude_unset)
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is None:
            continue
        current[field] = value

    _strip_legacy_yield_fields(current)

    save_property_config(property_uid, current)
    return current


def _fmt_pct(value: float) -> str:
    sign = "+" if value >= 0 else "-"
    return f"{sign}{abs(value) * 100:.1f}%"


def _fmt_float(value: float, digits: int = 3) -> str:
    return f"{value:.{digits}f}"


@router.post("/config/explain-adjustments")
async def explain_adjustments(body: ExplainAdjustmentsRequest):
    """Generate human-readable sample calculations using live backend formulas."""
    cfg = body.config or {}
    if not cfg and body.property_uid:
        cfg = get_property_config(body.property_uid)
    if not cfg:
        raise HTTPException(status_code=400, detail="config or property_uid is required")

    adj = get_pricing_adjustments_config(cfg)
    occ_cfg = adj["occupancy_pacing"]
    vel_cfg = adj["booking_velocity"]

    occ_available = max(int(occ_cfg["min_available_nights"]), int(occ_cfg["window_days"]))
    occ_target = float(occ_cfg["target_occupancy"])
    occ_example_rate = min(1.0, max(0.0, occ_target + 0.321))
    occ_booked = max(0, min(occ_available, round(occ_available * occ_example_rate)))

    occ = calculate_occupancy_pacing_multiplier(
        enabled=bool(occ_cfg["enabled"]),
        window_days=int(occ_cfg["window_days"]),
        target_occupancy=occ_target,
        sensitivity=float(occ_cfg["sensitivity"]),
        max_discount=float(occ_cfg["max_discount"]),
        max_increase=float(occ_cfg["max_increase"]),
        min_available_nights=int(occ_cfg["min_available_nights"]),
        booked_nights=occ_booked,
        available_nights=occ_available,
    )

    recent_days = max(1, int(vel_cfg["recent_window_days"]))
    baseline_days = max(1, int(vel_cfg["baseline_window_days"]))
    recent_bookings = max(int(vel_cfg["min_recent_bookings"]), 3)
    baseline_bookings = max(
        int(vel_cfg["min_baseline_bookings"]),
        round((recent_bookings / recent_days) * baseline_days / 3.0),
    )

    vel = calculate_booking_velocity_multiplier(
        enabled=bool(vel_cfg["enabled"]),
        recent_window_days=recent_days,
        baseline_window_days=baseline_days,
        sensitivity=float(vel_cfg["sensitivity"]),
        max_discount=float(vel_cfg["max_discount"]),
        max_increase=float(vel_cfg["max_increase"]),
        min_recent_bookings=int(vel_cfg["min_recent_bookings"]),
        min_baseline_bookings=int(vel_cfg["min_baseline_bookings"]),
        recent_bookings=recent_bookings,
        baseline_bookings=baseline_bookings,
    )

    occ_inputs = occ.get("inputs", {})
    occ_comp = occ.get("computed", {})
    vel_inputs = vel.get("inputs", {})
    vel_comp = vel.get("computed", {})

    occ_text = "\n".join([
        "Occupancy Pacing example (synthetic sample, exact engine formula):",
        f"1) actual_occupancy = booked_nights / available_nights = {occ_inputs.get('booked_nights', 0)} / {occ_inputs.get('available_nights', 0)} = {_fmt_float(occ_comp.get('actual_occupancy', 0.0), 4)}",
        f"2) delta = actual_occupancy - target_occupancy = {_fmt_pct(occ_comp.get('actual_occupancy', 0.0))} - {_fmt_pct(occ_inputs.get('target_occupancy', 0.0))} = {_fmt_pct(occ_comp.get('delta', 0.0))}",
        f"3) raw_adjustment = delta * sensitivity = {_fmt_pct(occ_comp.get('delta', 0.0))} * {_fmt_float(occ_inputs.get('sensitivity', 0.0), 3)} = {_fmt_pct(occ_comp.get('raw_adjustment', 0.0))}",
        f"4) capped_adjustment = clamp(raw_adjustment, -max_discount, max_increase) = clamp({_fmt_pct(occ_comp.get('raw_adjustment', 0.0))}, -{_fmt_pct(occ_inputs.get('max_discount', 0.0))}, {_fmt_pct(occ_inputs.get('max_increase', 0.0))}) = {_fmt_pct(occ_comp.get('capped_adjustment', 0.0))}",
        f"5) multiplier = 1.0 + capped_adjustment = {_fmt_float(occ.get('multiplier', 1.0), 4)}",
        f"Reason: {occ.get('reason', 'n/a')}",
    ])

    vel_text = "\n".join([
        "Booking Velocity example (synthetic sample, exact engine formula):",
        f"1) recent_bpd = recent_bookings / recent_window_days = {vel_inputs.get('recent_bookings', 0)} / {vel_inputs.get('recent_window_days', 1)} = {_fmt_float(vel_comp.get('recent_bpd', 0.0), 4)}",
        f"2) baseline_bpd = baseline_bookings / baseline_window_days = {vel_inputs.get('baseline_bookings', 0)} / {vel_inputs.get('baseline_window_days', 1)} = {_fmt_float(vel_comp.get('baseline_bpd', 0.0), 4)}",
        f"3) velocity_ratio = recent_bpd / baseline_bpd = {_fmt_float(vel_comp.get('velocity_ratio', 1.0), 4)}x",
        f"4) velocity_delta = velocity_ratio - 1.0 = {_fmt_pct(vel_comp.get('velocity_delta', 0.0))}",
        f"5) raw_adjustment = velocity_delta * sensitivity = {_fmt_pct(vel_comp.get('velocity_delta', 0.0))} * {_fmt_float(vel_inputs.get('sensitivity', 0.0), 3)} = {_fmt_pct(vel_comp.get('raw_adjustment', 0.0))}",
        f"6) capped_adjustment = clamp(raw_adjustment, -max_discount, max_increase) = clamp({_fmt_pct(vel_comp.get('raw_adjustment', 0.0))}, -{_fmt_pct(vel_inputs.get('max_discount', 0.0))}, {_fmt_pct(vel_inputs.get('max_increase', 0.0))}) = {_fmt_pct(vel_comp.get('capped_adjustment', 0.0))}",
        f"7) multiplier = 1.0 + capped_adjustment = {_fmt_float(vel.get('multiplier', 1.0), 4)}",
        f"Reason: {vel.get('reason', 'n/a')}",
    ])

    return {
        "occupancy_pacing": {"result": occ, "example_text": occ_text},
        "booking_velocity": {"result": vel, "example_text": vel_text},
    }
