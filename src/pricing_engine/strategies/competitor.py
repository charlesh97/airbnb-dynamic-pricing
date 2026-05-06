"""Competitor-based pricing strategy.

Requires external market rate data. Sources can be:
- Scraped Airbnb public listing rates
- PriceLabs / Wheelhouse / Beyond Pricing API
- Custom competitor list

This strategy module is designed to accept a market_median_price
passed in via config (from a data fetcher), or fetched from an
external source if COMPETITOR_API_KEY is configured.
"""

from __future__ import annotations

from typing import Any

from .base import PriceRecommendation, PricingStrategy


class CompetitorStrategy(PricingStrategy):
    """Market-rate adjusted pricing using competitor data."""

    name = "competitor"

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

        # Market data can be pre-fetched and stored in config
        market_rates: dict[str, float] = config.get("market_rates", {})
        market_median = market_rates.get(date) or market_rates.get(property_uid)

        # Quality adjustment factor (how does our property compare to market)
        quality_score = config.get("quality_scores", {}).get(
            property_uid, config.get("default_quality_score", 0.85)
        )
        market_avg_quality = config.get("market_avg_quality", 0.80)

        if market_median is None:
            # No market data — fall back to base price with low confidence
            return PriceRecommendation(
                strategy_name=self.name,
                suggested_price=round(base, 2),
                confidence=0.0,
                factors={
                    "error": "no_market_data",
                    "base_price": base,
                },
            )

        # Adjust market median by relative quality
        adjustment = quality_score / market_avg_quality
        raw_price = market_median * adjustment
        price = self._clamp(raw_price, config, property_uid)

        return PriceRecommendation(
            strategy_name=self.name,
            suggested_price=round(price, 2),
            confidence=0.75,  # Market data has inherent uncertainty
            factors={
                "base_price": base,
                "market_median": market_median,
                "quality_score": quality_score,
                "adjustment_factor": round(adjustment, 3),
                "suggested_price": round(raw_price, 2),
            },
        )
