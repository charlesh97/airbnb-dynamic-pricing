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
        """Extract base price for a property from config."""
        base = config.get("base_prices", {}).get(property_uid)
        if base is None:
            base = config.get("default_base_price", 100.0)
        return float(base)

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
