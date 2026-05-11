"""Pricing engine — computes additive component pricing."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from datetime import datetime, timedelta
from typing import Any

from .percent import multiplier_to_pct, pct_to_multiplier
from .strategies import (
    AvailabilityResult,
    AvailabilityStrategy,
    CompetitorStrategy,
    EventStrategy,
)
from .strategies.demand import (
    MODULE_MULTIPLIER_CEILING,
    MODULE_MULTIPLIER_FLOOR,
    calculate_booking_velocity_multiplier,
    calculate_occupancy_pacing_multiplier,
    clamp,
    get_pricing_adjustments_config,
)


@dataclass
class PropertyConfig:
    """Per-property pricing configuration."""

    property_uid: str
    base_price: float
    min_price: float = 50.0
    max_price: float = 2000.0
    quality_score: float = 0.85


@dataclass
class DatePrice:
    """Final computed price for a single date."""

    date: str
    property_uid: str
    final_price: float
    strategy_prices: dict[str, float]
    confidence: float
    all_factors: dict[str, Any]
    is_available: bool = True
    min_stay: int = 2
    blocked_reason: str | None = None


def apply_manual_overrides(
    date_price: DatePrice,
    uid: str,
    date: str,
    config: dict[str, Any],
) -> DatePrice:
    """Apply manual price/availability overrides from config."""
    overrides = config.get("manual_overrides", {})
    entry = overrides.get(date, {})
    if not entry:
        return date_price

    price_override = entry.get("price_override")
    is_available_override = entry.get("availability")

    new_price = date_price
    if price_override is not None:
        new_price = dataclass_replace(
            date_price,
            final_price=_round_price_to_nearest_dollar(float(price_override)),
        )
    if is_available_override is not None:
        new_price = dataclass_replace(
            new_price,
            is_available=bool(is_available_override),
            blocked_reason=None if is_available_override else (new_price.blocked_reason or "manual_block"),
        )
    return new_price


def dataclass_replace(obj, **changes):
    """Minimal dataclass replace (works without dataclasses.replace)."""
    import copy

    new_obj = copy.copy(obj)
    for k, v in changes.items():
        setattr(new_obj, k, v)
    return new_obj


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value[:19], fmt)
        except ValueError:
            pass
    return None


def _round_price_to_nearest_dollar(value: float) -> float:
    """Round currency to the nearest whole dollar using half-up semantics."""
    return float(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


class PricingEngine:
    """Runs pricing strategies and computes additive final price."""

    def __init__(self) -> None:
        self.event_strategy = EventStrategy()
        self.competitor_strategy = CompetitorStrategy()
        self.availability_strategy = AvailabilityStrategy()

    def _price_bounds(
        self,
        config: dict[str, Any],
        props_config: dict[str, Any],
        prop: PropertyConfig,
    ) -> tuple[float, float]:
        min_p_raw = props_config.get("min_price")
        if min_p_raw is None:
            min_p_raw = config.get("min_price")
        if min_p_raw is None:
            min_p_raw = config.get("default_min_price", prop.min_price)

        max_p_raw = props_config.get("max_price")
        if max_p_raw is None:
            max_p_raw = config.get("max_price")
        if max_p_raw is None:
            max_p_raw = config.get("default_max_price", prop.max_price)

        return float(min_p_raw), float(max_p_raw)

    def _count_pacing_nights(
        self,
        *,
        property_uid: str,
        target: datetime,
        bookings_in_window: list[dict[str, Any]],
        merged_config: dict[str, Any],
        window_days: int,
    ) -> tuple[int, int]:
        """Return booked_nights and eligible nights for occupancy pacing."""
        window_start = target - timedelta(days=window_days)

        booked_nights = 0
        for booking in bookings_in_window:
            b_start = _parse_date(booking.get("checkin", ""))
            b_end = _parse_date(booking.get("checkout", ""))
            if not b_start or not b_end:
                continue
            overlap_start = max(window_start, b_start)
            overlap_end = min(target, b_end)
            if overlap_end > overlap_start:
                booked_nights += (overlap_end - overlap_start).days

        eligible_nights = 0
        cursor = window_start
        while cursor < target:
            avail = self.compute_availability(
                property_uid=property_uid,
                date=cursor.strftime("%Y-%m-%d"),
                calendar_entry=None,
                bookings_in_window=bookings_in_window,
                config=merged_config,
            )
            if getattr(avail, "blocked_reason", None) != "booking_window_closed":
                eligible_nights += 1
            cursor += timedelta(days=1)

        return booked_nights, eligible_nights

    def _count_velocity_bookings(
        self,
        *,
        bookings_in_window: list[dict[str, Any]],
        target: datetime,
        recent_window_days: int,
        baseline_window_days: int,
    ) -> tuple[int, int]:
        recent_start = target - timedelta(days=recent_window_days)
        baseline_start = target - timedelta(days=baseline_window_days)

        recent = 0
        baseline = 0
        for booking in bookings_in_window:
            created = _parse_date(booking.get("created_dttm", ""))
            if not created:
                continue
            if baseline_start <= created < target:
                baseline += 1
            if recent_start <= created < target:
                recent += 1
        return recent, baseline

    def compute_price(
        self,
        *,
        property_uid: str,
        date: str,
        calendar_entry: dict[str, Any] | None,
        bookings_in_window: list[dict[str, Any]],
        config: dict[str, Any],
        property_override: PropertyConfig | None = None,
    ) -> DatePrice:
        """Compute additive component price for a single property/date."""
        props_config = config.get("property_overrides", {}).get(property_uid, {})
        prop = property_override or PropertyConfig(property_uid=property_uid, base_price=100.0)
        merged_config = {**config, **props_config}
        target = datetime.strptime(date, "%Y-%m-%d")

        event_rec = self.event_strategy.compute(
            property_uid=property_uid,
            date=date,
            calendar_entry=calendar_entry,
            bookings_in_window=bookings_in_window,
            config=merged_config,
        )
        competitor_rec = self.competitor_strategy.compute(
            property_uid=property_uid,
            date=date,
            calendar_entry=calendar_entry,
            bookings_in_window=bookings_in_window,
            config=merged_config,
        )

        base_price = float(
            event_rec.factors.get(
                "base_price",
                merged_config.get("base_price", merged_config.get("default_base_price", 200.0)),
            )
        )

        adjustment_cfg = get_pricing_adjustments_config(merged_config)
        occ_cfg = adjustment_cfg["occupancy_pacing"]
        vel_cfg = adjustment_cfg["booking_velocity"]

        booked_nights, available_nights = self._count_pacing_nights(
            property_uid=property_uid,
            target=target,
            bookings_in_window=bookings_in_window,
            merged_config=merged_config,
            window_days=occ_cfg["window_days"],
        )

        occupancy_pacing = calculate_occupancy_pacing_multiplier(
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

        recent_bookings, baseline_bookings = self._count_velocity_bookings(
            bookings_in_window=bookings_in_window,
            target=target,
            recent_window_days=vel_cfg["recent_window_days"],
            baseline_window_days=vel_cfg["baseline_window_days"],
        )

        booking_velocity = calculate_booking_velocity_multiplier(
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

        occupancy_multiplier = clamp(
            float(occupancy_pacing["multiplier"]),
            MODULE_MULTIPLIER_FLOOR,
            MODULE_MULTIPLIER_CEILING,
        )
        velocity_multiplier = clamp(
            float(booking_velocity["multiplier"]),
            MODULE_MULTIPLIER_FLOOR,
            MODULE_MULTIPLIER_CEILING,
        )

        seasonality_multiplier = float(event_rec.factors.get("seasonality_multiplier", 1.0))
        holiday_multiplier = float(event_rec.factors.get("holiday_component_multiplier", 1.0))
        far_future_multiplier = float(event_rec.factors.get("far_future_multiplier", 1.0))
        last_minute_multiplier = float(event_rec.factors.get("last_minute_multiplier", 1.0))
        dow_multiplier = float(event_rec.factors.get("dow_multiplier", 1.0))

        pre_competitor_for_ratio = (
            base_price
            * seasonality_multiplier
            * holiday_multiplier
            * far_future_multiplier
            * last_minute_multiplier
            * dow_multiplier
            * occupancy_multiplier
            * velocity_multiplier
        )

        competitor_multiplier = 1.0
        if (
            competitor_rec.confidence > 0
            and competitor_rec.suggested_price > 0
            and pre_competitor_for_ratio > 0
        ):
            competitor_multiplier = clamp(
                competitor_rec.suggested_price / pre_competitor_for_ratio,
                MODULE_MULTIPLIER_FLOOR,
                MODULE_MULTIPLIER_CEILING,
            )

        components: list[dict[str, Any]] = []
        running_subtotal = base_price

        def add_component(
            *,
            key: str,
            label: str,
            multiplier: float,
            reason: str = "",
            details: dict[str, Any] | None = None,
        ) -> None:
            nonlocal running_subtotal
            amount = base_price * (multiplier - 1.0)
            running_subtotal += amount
            components.append(
                {
                    "key": key,
                    "label": label,
                    "multiplier": round(multiplier, 6),
                    "pct": round(multiplier_to_pct(multiplier), 3),
                    "amount": round(amount, 2),
                    "running_subtotal": round(running_subtotal, 2),
                    "reason": reason,
                    "details": details or {},
                }
            )

        add_component(
            key="seasonality",
            label="Seasonality",
            multiplier=seasonality_multiplier,
            reason=event_rec.factors.get("seasonal_source", ""),
            details={"seasonality_pct": event_rec.factors.get("seasonality_pct")},
        )
        add_component(
            key="holiday",
            label="Holiday",
            multiplier=holiday_multiplier,
            reason=event_rec.factors.get("holiday_name") or "none",
            details={
                "is_holiday": event_rec.factors.get("is_holiday", False),
                "holiday_source": event_rec.factors.get("holiday_source"),
                "holiday_component_pct": event_rec.factors.get("holiday_component_pct", 0.0),
            },
        )
        add_component(
            key="far_future",
            label="Far Future",
            multiplier=far_future_multiplier,
            reason="applied" if event_rec.factors.get("far_future_applied") else "not_applied",
            details={
                "window_days": event_rec.factors.get("far_future_window_days"),
                "discount_pct": event_rec.factors.get("far_future_discount_pct"),
            },
        )
        add_component(
            key="last_minute",
            label="Last Minute",
            multiplier=last_minute_multiplier,
            reason="applied" if event_rec.factors.get("last_minute_applied") else "not_applied",
            details={
                "window_days": event_rec.factors.get("last_minute_window_days"),
                "discount_pct": event_rec.factors.get("last_minute_discount_pct"),
                "threshold_occupancy_pct": event_rec.factors.get("last_minute_threshold_occupancy_pct"),
            },
        )
        add_component(
            key="day_of_week",
            label="Day of Week",
            multiplier=dow_multiplier,
            reason=str(event_rec.factors.get("dow", "")),
            details={"dow_pct": event_rec.factors.get("dow_pct")},
        )

        starting_price = running_subtotal

        add_component(
            key="occupancy_pacing",
            label="Occupancy Pacing",
            multiplier=occupancy_multiplier,
            reason=occupancy_pacing.get("reason", "n/a"),
            details=occupancy_pacing,
        )
        price_after_occupancy = running_subtotal

        add_component(
            key="booking_velocity",
            label="Booking Velocity",
            multiplier=velocity_multiplier,
            reason=booking_velocity.get("reason", "n/a"),
            details=booking_velocity,
        )
        price_after_velocity = running_subtotal

        add_component(
            key="competitor",
            label="Competitor",
            multiplier=competitor_multiplier,
            reason=(competitor_rec.factors or {}).get("status", "disabled"),
            details=competitor_rec.factors,
        )
        price_after_competitor = running_subtotal

        subtotal_before_adjust = running_subtotal

        price_adjust_pct = float(adjustment_cfg.get("price_adjust_pct", 0.0) or 0.0)
        price_adjust_multiplier = pct_to_multiplier(price_adjust_pct)
        price_adjust_amount = subtotal_before_adjust * (price_adjust_multiplier - 1.0)
        raw_adjusted_price = subtotal_before_adjust + price_adjust_amount

        min_p, max_p = self._price_bounds(config, props_config, prop)
        clamped_price = max(min_p, min(max_p, raw_adjusted_price))
        final_price = _round_price_to_nearest_dollar(clamped_price)

        event_component_multipliers = [
            seasonality_multiplier,
            holiday_multiplier,
            far_future_multiplier,
            last_minute_multiplier,
            dow_multiplier,
        ]
        event_multiplier = 1.0
        for m in event_component_multipliers:
            event_multiplier *= m

        demand_multiplier = occupancy_multiplier * velocity_multiplier

        strategy_prices = {
            "event": round(starting_price, 2),
            "demand": round(price_after_velocity, 2),
        }
        if competitor_rec.confidence > 0:
            strategy_prices["competitor"] = round(competitor_rec.suggested_price, 2)

        confidence_parts = [event_rec.confidence, 0.8, 0.8]
        if competitor_rec.confidence > 0:
            confidence_parts.append(competitor_rec.confidence)
        confidence = round(sum(confidence_parts) / len(confidence_parts), 3)

        all_factors: dict[str, Any] = {
            "event": {
                **event_rec.factors,
                "event_multiplier": round(event_multiplier, 6),
            },
            "competitor": {
                **competitor_rec.factors,
                "enabled": bool((merged_config.get("external_market_data") or {}).get("enabled", False)),
                "multiplier": round(competitor_multiplier, 6),
            },
            "demand": {
                "demand_multiplier": round(demand_multiplier, 6),
                "occupancy_pacing": occupancy_pacing,
                "booking_velocity": booking_velocity,
                "occupancy_multiplier": round(occupancy_multiplier, 6),
                "velocity_multiplier": round(velocity_multiplier, 6),
                "booked_nights": booked_nights,
                "available_nights": available_nights,
                "recent_bookings": recent_bookings,
                "baseline_bookings": baseline_bookings,
            },
            "explanation": {
                "base_price": round(base_price, 2),
                "components": components,
                "event_multiplier": round(event_multiplier, 6),
                "demand_multiplier": round(demand_multiplier, 6),
                "starting_price": round(starting_price, 2),
                "price_after_occupancy": round(price_after_occupancy, 2),
                "price_after_velocity": round(price_after_velocity, 2),
                "price_after_competitor": round(price_after_competitor, 2),
                "subtotal_before_adjust": round(subtotal_before_adjust, 2),
                "price_adjust_pct": round(price_adjust_pct, 3),
                "price_adjust_amount": round(price_adjust_amount, 2),
                "raw_adjusted_price": round(raw_adjusted_price, 2),
                "min_price": round(min_p, 2),
                "max_price": round(max_p, 2),
                "final_price": final_price,
            },
        }

        return DatePrice(
            date=date,
            property_uid=property_uid,
            final_price=final_price,
            strategy_prices=strategy_prices,
            confidence=confidence,
            all_factors=all_factors,
        )

    def compute_availability(
        self,
        *,
        property_uid: str,
        date: str,
        calendar_entry: dict[str, Any] | None,
        bookings_in_window: list[dict[str, Any]],
        config: dict[str, Any],
    ) -> AvailabilityResult:
        """Compute availability for a single date."""
        return self.availability_strategy.compute(
            property_uid=property_uid,
            date=date,
            calendar_entry=calendar_entry,
            bookings_in_window=bookings_in_window,
            config=config,
        )

    def compute_range(
        self,
        *,
        property_uid: str,
        from_date: str,
        to_date: str,
        calendar_data: list[dict[str, Any]],
        bookings_in_window: list[dict[str, Any]],
        config: dict[str, Any],
        property_override: PropertyConfig | None = None,
    ) -> list[DatePrice]:
        """Compute prices for a date range."""
        start = datetime.strptime(from_date, "%Y-%m-%d")
        end = datetime.strptime(to_date, "%Y-%m-%d")
        results: list[DatePrice] = []

        current = start
        while current <= end:
            date_str = current.strftime("%Y-%m-%d")
            entry = next((e for e in calendar_data if e.get("date") == date_str), None)
            result = self.compute_price(
                property_uid=property_uid,
                date=date_str,
                calendar_entry=entry,
                bookings_in_window=bookings_in_window,
                config=config,
                property_override=property_override,
            )
            results.append(result)
            current += timedelta(days=1)

        return results
