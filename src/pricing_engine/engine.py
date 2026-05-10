"""Pricing engine — computes stacked pricing adjustments."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .strategies import (
    AvailabilityResult,
    AvailabilityStrategy,
    CompetitorStrategy,
    EventStrategy,
    YieldStrategy,
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
    strategy_weights: dict[str, float] = field(default_factory=dict)


@dataclass
class DatePrice:
    """Final computed price for a single date."""

    date: str
    property_uid: str
    final_price: float
    strategy_prices: dict[str, float]
    strategy_weights: dict[str, float]
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
        new_price = dataclass_replace(date_price, final_price=float(price_override))
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


class PricingEngine:
    """Runs pricing strategies and computes a stacked final price."""

    def __init__(self, default_weights: dict[str, float] | None = None) -> None:
        self.event_strategy = EventStrategy()
        self.yield_strategy = YieldStrategy()
        self.competitor_strategy = CompetitorStrategy()
        self.availability_strategy = AvailabilityStrategy()
        # Kept only for compatibility in API payloads; no longer used in pricing math.
        self._default_weights = default_weights or {
            "demand": 0.35,
            "event": 0.35,
            "competitor": 0.00,
            "yield": 0.30,
        }

    def _normalize_weights(self, weights: dict[str, float]) -> dict[str, float]:
        """Normalize strategy weights to sum to 1.0 (compat output only)."""
        total = sum(weights.values())
        if total <= 0 or total == 1.0:
            return weights
        return {k: round(v / total, 3) for k, v in weights.items()}

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
            # Eligible nights are those not blocked by booking-window closure.
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

    def _extract_starting_price(
        self,
        event_factors: dict[str, Any],
        yield_factors: dict[str, Any],
        merged_config: dict[str, Any],
    ) -> tuple[float, float, float]:
        base_price = float(event_factors.get("base_price", merged_config.get("base_price", merged_config.get("default_base_price", 200.0))))
        event_multiplier = float(event_factors.get("seasonal_multiplier", 1.0)) * float(event_factors.get("dow_multiplier", 1.0))
        yield_multiplier = float(yield_factors.get("final_multiplier", 1.0))
        return base_price, event_multiplier, yield_multiplier

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
        """Compute the stacked price for a single property/date."""
        props_config = config.get("property_overrides", {}).get(property_uid, {})
        prop = property_override or PropertyConfig(property_uid=property_uid, base_price=100.0)
        merged_config = {**config, **props_config}
        target = datetime.strptime(date, "%Y-%m-%d")

        # Compatibility-only output: no longer used for final price math.
        weights = (
            property_override.strategy_weights
            if property_override and property_override.strategy_weights
            else props_config.get("strategy_weights")
            if props_config.get("strategy_weights")
            else config.get("strategy_weights")
            if config.get("strategy_weights")
            else self._default_weights
        )
        weights = self._normalize_weights(weights)

        event_rec = self.event_strategy.compute(
            property_uid=property_uid,
            date=date,
            calendar_entry=calendar_entry,
            bookings_in_window=bookings_in_window,
            config=merged_config,
        )
        yield_rec = self.yield_strategy.compute(
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

        base_price, event_multiplier, yield_multiplier = self._extract_starting_price(
            event_rec.factors,
            yield_rec.factors,
            merged_config,
        )
        starting_price = base_price * event_multiplier * yield_multiplier

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
            **occ_cfg,
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
            **vel_cfg,
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

        price_after_occupancy = starting_price * occupancy_multiplier
        price_after_velocity = price_after_occupancy * velocity_multiplier

        competitor_multiplier = 1.0
        if competitor_rec.confidence > 0 and competitor_rec.suggested_price > 0 and price_after_velocity > 0:
            competitor_multiplier = clamp(
                competitor_rec.suggested_price / price_after_velocity,
                MODULE_MULTIPLIER_FLOOR,
                MODULE_MULTIPLIER_CEILING,
            )
        price_after_competitor = price_after_velocity * competitor_multiplier

        price_adjust = float(merged_config.get("price_adjust", 0.0) or 0.0)
        raw_adjusted_price = price_after_competitor * (1.0 + price_adjust)

        min_p, max_p = self._price_bounds(config, props_config, prop)
        final_price = max(min_p, min(max_p, raw_adjusted_price))

        demand_multiplier = occupancy_multiplier * velocity_multiplier
        strategy_prices = {
            "event": round(event_rec.suggested_price, 2),
            "yield": round(yield_rec.suggested_price, 2),
            "demand": round(starting_price * demand_multiplier, 2),
        }
        if competitor_rec.confidence > 0:
            strategy_prices["competitor"] = round(competitor_rec.suggested_price, 2)

        confidence_parts = [event_rec.confidence, yield_rec.confidence, 0.8, 0.8]
        if competitor_rec.confidence > 0:
            confidence_parts.append(competitor_rec.confidence)
        confidence = round(sum(confidence_parts) / len(confidence_parts), 3)

        all_factors: dict[str, Any] = {
            "event": {**event_rec.factors, "event_multiplier": round(event_multiplier, 4)},
            "yield": {**yield_rec.factors, "yield_multiplier": round(yield_multiplier, 4)},
            "competitor": {
                **competitor_rec.factors,
                "enabled": bool((merged_config.get("external_market_data") or {}).get("enabled", False)),
                "multiplier": round(competitor_multiplier, 4),
            },
            "demand": {
                "demand_multiplier": round(demand_multiplier, 4),
                "occupancy_pacing": occupancy_pacing,
                "booking_velocity": booking_velocity,
                "occupancy_multiplier": round(occupancy_multiplier, 4),
                "velocity_multiplier": round(velocity_multiplier, 4),
                "booked_nights": booked_nights,
                "available_nights": available_nights,
                "recent_bookings": recent_bookings,
                "baseline_bookings": baseline_bookings,
            },
            "explanation": {
                "base_price": round(base_price, 2),
                "starting_price": round(starting_price, 2),
                "event_multiplier": round(event_multiplier, 4),
                "yield_multiplier": round(yield_multiplier, 4),
                "occupancy_multiplier": round(occupancy_multiplier, 4),
                "velocity_multiplier": round(velocity_multiplier, 4),
                "competitor_multiplier": round(competitor_multiplier, 4),
                "price_adjust": price_adjust,
                "price_after_occupancy": round(price_after_occupancy, 2),
                "price_after_velocity": round(price_after_velocity, 2),
                "price_after_competitor": round(price_after_competitor, 2),
                "raw_adjusted_price": round(raw_adjusted_price, 2),
                "min_price": round(min_p, 2),
                "max_price": round(max_p, 2),
                "final_price": round(final_price, 2),
            },
        }

        return DatePrice(
            date=date,
            property_uid=property_uid,
            final_price=round(final_price, 2),
            strategy_prices=strategy_prices,
            strategy_weights={k: round(v, 3) for k, v in weights.items()},
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
