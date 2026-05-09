"""Abstract base class for pricing strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class PriceRecommendation:
    """Output from a single pricing strategy."""

    strategy_name: str
    suggested_price: float
    confidence: float  # 0.0–1.0
    factors: dict[str, Any]  # diagnostic breakdown

    def is_valid(self) -> bool:
        return (
            isinstance(self.suggested_price, (int, float))
            and self.suggested_price > 0
            and 0.0 <= self.confidence <= 1.0
        )


class PricingStrategy(ABC):
    """Base class for all pricing strategies."""

    name: str = "base"

    @abstractmethod
    def compute(
        self,
        *,
        property_uid: str,
        date: str,  # YYYY-MM-DD
        calendar_entry: dict[str, Any] | None,
        bookings_in_window: list[dict[str, Any]],
        config: dict[str, Any],
    ) -> PriceRecommendation:
        """Compute a price recommendation for a single date / property."""
        ...

    def _base_price(self, config: dict[str, Any], property_uid: str) -> float:
        """Extract base price for a property from config.

        Supports three key formats:
          - base_prices[property_uid]      (per-property dict, env-config style)
          - default_base_price            (global fallback in env config)
          - base_price                    (singular key in property JSON)
        """
        base = config.get("base_prices", {}).get(property_uid)
        if base is None:
            base = config.get("default_base_price")
        if base is None:
            base = config.get("base_price")  # singular key in property JSON
        return float(base if base is not None else 100.0)

    def _seasonal_base_price(
        self, config: dict[str, Any], property_uid: str, target
    ) -> float:
        """Seasonal base price from config, falling back to _base_price.


        Config may use month names (jan, feb, ...) or month numbers (01, 02, ...).
        """
        from calendar import month_abbr, month_name
        mm = target.strftime("%m")  # "01".."12"
        month_key = mm  # try numeric first

        seasonal = config.get("seasonal_base_prices", {})
        if month_key in seasonal:
            return float(seasonal[month_key])

        # Try lowercase abbreviated name (jan, feb, ...)
        abbrev = target.strftime("%b").lower()
        if abbrev in seasonal:
            return float(seasonal[abbrev])

        return self._base_price(config, property_uid)

    def _price_bounds(
        self, config: dict[str, Any], property_uid: str
    ) -> tuple[float, float]:
        """Return (min_price, max_price) for a property."""
        props = config.get("property_overrides", {}).get(property_uid, {})
        min_p = props.get("min_price", config.get("default_min_price", 50.0))
        max_p = props.get("max_price", config.get("default_max_price", 1000.0))
        return float(min_p), float(max_p)

    def _clamp(self, price: float, config: dict[str, Any], property_uid: str) -> float:
        lo, hi = self._price_bounds(config, property_uid)
        return max(lo, min(hi, price))
