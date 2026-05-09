"""Pricing engine — runs strategies and computes weighted final prices."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .strategies import (
    AvailabilityResult,
    AvailabilityStrategy,
    CompetitorStrategy,
    DemandStrategy,
    EventStrategy,
    PriceRecommendation,
    YieldStrategy,
    PricingStrategy, )


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
    """Apply manual price/availability overrides from config.

    Looks in config["manual_overrides"] for a {date: {price_override, availability}} entry.
    """
    overrides = config.get("manual_overrides", {})
    entry = overrides.get(date, {})
    if not entry:
        return date_price

    # Override price if specified
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
    """Minimal dataclass replace (works without from __future__ import)."""
    import copy
    new_obj = copy.copy(obj)
    for k, v in changes.items():
        setattr(new_obj, k, v)
    return new_obj


class PricingEngine:
    """Runs pricing strategies and computes a weighted final price."""

    def __init__(self, default_weights: dict[str, float] | None = None) -> None:
        self.strategies: list[PricingStrategy] = [
            DemandStrategy(),
            EventStrategy(),
            YieldStrategy(),
            CompetitorStrategy(),
        ]
        self.availability_strategy = AvailabilityStrategy()
        self._default_weights = default_weights or {
            "demand": 0.35,
            "event": 0.35,
            "competitor": 0.00,
            "yield": 0.30,
        }

    def _normalize_weights(self, weights: dict[str, float]) -> dict[str, float]:
        """Normalize strategy weights to sum to 1.0.

        If total weight is 0 or already 1.0, returns weights unchanged.
        Otherwise scales all weights proportionally.
        """
        total = sum(weights.values())
        if total <= 0 or total == 1.0:
            return weights
        return {k: round(v / total, 3) for k, v in weights.items()}

    def _explain(
        self,
        base_price: float,
        weights: dict[str, float],
        strategy_prices: dict[str, float],
        all_factors: dict[str, Any],
        seasonal_multiplier: float,
        dow_multiplier: float,
        demand_multiplier: float,
        yield_multiplier: float,
        competitor_multiplier: float,
        last_minute_adjustment: float,
        occupancy_adjustment: float,
    ) -> dict[str, Any]:
        """Build an explanation dict for a DatePrice.

        This maps strategy prices back to their component multipliers
        and shows how each contributes to the final weighted price.
        """
        total_weight = sum(weights.values())
        if total_weight <= 0:
            total_weight = 1.0

        strategy_breakdown = {}
        weighted_price_before_caps = 0.0

        for strat_name in ("demand", "event", "yield", "competitor"):
            price = strategy_prices.get(strat_name, base_price)
            w = weights.get(strat_name, 0.0)
            contribution = price * (w / total_weight)
            weighted_price_before_caps += contribution
            strategy_breakdown[strat_name] = {
                "price": round(price, 2),
                "weight": round(w, 3),
                "contribution": round(contribution, 2),
            }

        # competitor disabled note
        competitor_status = all_factors.get("competitor", {}).get("status", "ok")
        if competitor_status == "disabled":
            competitor_adjustment = "disabled"
        else:
            competitor_adjustment = round(competitor_multiplier, 3)

        # Determine cap application
        min_cap_applied = False
        max_cap_applied = False

        # Summary sentence
        dow_label = "weekend" if dow_multiplier > 1.05 else "weekday"
        season_label = ""
        if seasonal_multiplier > 1.3:
            season_label = "peak season"
        elif seasonal_multiplier < 0.9:
            season_label = "off-season"

        summary_parts = []
        if season_label:
            summary_parts.append(season_label.capitalize())
        if dow_label:
            summary_parts.append(dow_label)
        if occupancy_adjustment < 0.95:
            summary_parts.append("low occupancy")
        if last_minute_adjustment < 0.95:
            summary_parts.append("last-minute discount")
        elif last_minute_adjustment > 1.05:
            summary_parts.append("last-minute premium")

        if not summary_parts:
            summary_parts = ["Normal pricing conditions"]

        return {
            "base_price": round(base_price, 2),
            "season_applied": season_label or "normal",
            "season_multiplier": round(seasonal_multiplier, 3),
            "dow_multiplier": round(dow_multiplier, 3),
            "holiday_multiplier": round(
                all_factors.get("event", {}).get("holiday_multiplier", 1.0), 3
            ),
            "demand_multiplier": round(demand_multiplier, 3),
            "yield_multiplier": round(yield_multiplier, 3),
            "last_minute_adjustment": round(last_minute_adjustment, 3),
            "occupancy_adjustment": round(occupancy_adjustment, 3),
            "competitor_adjustment": competitor_adjustment,
            "weighted_price_before_caps": round(weighted_price_before_caps, 2),
            "min_cap_applied": min_cap_applied,
            "max_cap_applied": max_cap_applied,
            "final_price": 0.0,  # filled by caller
            "summary": ", ".join(summary_parts),
            "strategy_breakdown": strategy_breakdown,
        }

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
        """Compute the weighted price for a single property/date."""

        props_config = config.get("property_overrides", {}).get(property_uid, {})
        prop = property_override or PropertyConfig(property_uid=property_uid, base_price=100.0)

        # Weights precedence: property_override > property_overrides[uid] > global config > defaults
        weights = (
            property_override.strategy_weights
            if property_override and property_override.strategy_weights
            else props_config.get("strategy_weights")
            if props_config.get("strategy_weights")
            else config.get("strategy_weights")
            if config.get("strategy_weights")
            else self._default_weights
        )

        # Normalize weights
        weights = self._normalize_weights(weights)
        total_weight = sum(weights.values())

        strategy_prices: dict[str, float] = {}
        strategy_confidences: dict[str, float] = {}
        all_factors: dict[str, Any] = {}

        for strat in self.strategies:
            rec = strat.compute(
                property_uid=property_uid,
                date=date,
                calendar_entry=calendar_entry,
                bookings_in_window=bookings_in_window,
                config={**config, **props_config},
            )
            # Skip zero-confidence strategies (inactive data sources)
            if rec.confidence == 0.0:
                continue
            strategy_prices[strat.name] = rec.suggested_price
            strategy_confidences[strat.name] = rec.confidence
            all_factors[strat.name] = rec.factors

        # Weighted average
        weighted_sum = sum(
            strategy_prices.get(name, 0.0) * (weights.get(name, 0.0) / total_weight)
            for name in set(weights.keys()) | set(strategy_prices.keys())
        )

        # Clamp to bounds — use config defaults when no property override exists
        min_p = float(props_config.get("min_price", config.get("default_min_price", prop.min_price)))
        max_p = float(props_config.get("max_price", config.get("default_max_price", prop.max_price)))
        final_price = max(min_p, min(max_p, weighted_sum))

        # Confidence: weighted average of strategy confidences
        confidence = sum(
            strategy_confidences.get(name, 0.5) * (weights.get(name, 0.0) / total_weight)
            for name in weights
        )

        # Apply global price adjustment override
        price_adjust = config.get("price_adjust", 0)
        if price_adjust != 0:
            final_price = final_price * (1 + price_adjust)

        # ─── Build explanation ───────────────────────────────────────────
        ev = all_factors.get("event", {})
        dm = all_factors.get("demand", {})
        yt = all_factors.get("yield", {})
        co = all_factors.get("competitor", {})

        seasonal_mult = ev.get("seasonal_multiplier", 1.0)
        dow_mult = ev.get("dow_multiplier", 1.0)
        demand_mult = dm.get("demand_multiplier", 1.0)
        yield_mult = yt.get("final_multiplier", yt.get("lead_factor", 1.0))
        last_min_adj = yt.get("last_minute_adjustment", 1.0)
        occupancy_adj = dm.get("occupancy_multiplier", 1.0)
        competitor_mult = 1.0 if co.get("status") == "disabled" else co.get("adjustment_factor", 1.0)

        explanation = self._explain(
            base_price=ev.get("base_price", config.get("default_base_price", 200.0)),
            weights=weights,
            strategy_prices=strategy_prices,
            all_factors=all_factors,
            seasonal_multiplier=seasonal_mult,
            dow_multiplier=dow_mult,
            demand_multiplier=demand_mult,
            yield_multiplier=yield_mult,
            competitor_multiplier=competitor_mult,
            last_minute_adjustment=last_min_adj,
            occupancy_adjustment=occupancy_adj,
        )
        explanation["final_price"] = round(final_price, 2)

        result = DatePrice(
            date=date,
            property_uid=property_uid,
            final_price=round(final_price, 2),
            strategy_prices={k: round(v, 2) for k, v in strategy_prices.items()},
            strategy_weights={k: round(v, 3) for k, v in weights.items()},
            confidence=round(confidence, 3),
            all_factors={**all_factors, "explanation": explanation},
        )

        return result

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
            # Find matching calendar entry
            entry = next(
                (e for e in calendar_data if e.get("date") == date_str), None
            )
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