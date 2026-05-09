"""Competitor-based pricing strategy.

Requires external market rate data. Sources can be:
- Scraped Airbnb public listing rates
- PriceLabs / Wheelhouse / Beyond Pricing API
- Airdna / AirDNA data
- Manual competitor set

This strategy module accepts market data passed via the `external_market_data`
config block. When disabled (default), it returns confidence=0.0 with a
"disabled" status rather than falling back to base price.

External market data schema (in config):
{
    "external_market_data": {
        "enabled": False,          # Keep False unless data source is connected
        "source": None,           # "pricelabs"|"wheelhouse"|"beyond_pricing"|"airdna"|"manual"|"scraped"
        "api_key": None,
        "last_pull_timestamp": None,
        "comp_set_definition": {},  # filter params: bedrooms, location, property_type
        "raw_data": {},           # keyed by date: {"YYYY-MM-DD": {market_rates, occupancy, booking_pace}}
        "confidence": 0.0,
        "num_comps_used": 0,
    }
}
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
        # ─── Check if external market data is enabled ────────────────────
        ext = config.get("external_market_data", {})
        if not ext.get("enabled", False):
            return PriceRecommendation(
                strategy_name=self.name,
                suggested_price=0.0,
                confidence=0.0,
                factors={
                    "status": "disabled",
                    "note": "external_market_data is disabled — returning zero confidence",
                },
            )

        # ─── Parse and validate market data ─────────────────────────────
        base = self._base_price(config, property_uid)
        market_info = self.parse_market_data(ext.get("raw_data", {}), date)
        if market_info is None:
            return PriceRecommendation(
                strategy_name=self.name,
                suggested_price=0.0,
                confidence=0.0,
                factors={
                    "status": "no_data",
                    "note": "no market data for this date",
                },
            )

        market_median = market_info["market_median"]
        market_avg_quality = market_info.get("market_avg_quality", 0.80)
        quality_score = config.get("quality_scores", {}).get(
            property_uid, config.get("default_quality_score", 0.85)
        )

        # ─── Compute market adjustment ───────────────────────────────────
        adjustment = self.compute_market_adjustment(market_median, quality_score, market_avg_quality)
        raw_price = market_median * adjustment
        price = self._clamp(raw_price, config, property_uid)

        return PriceRecommendation(
            strategy_name=self.name,
            suggested_price=round(price, 2),
            confidence=ext.get("confidence", 0.75),
            factors={
                "status": "ok",
                "base_price": base,
                "market_median": market_median,
                "quality_score": quality_score,
                "market_avg_quality": market_avg_quality,
                "adjustment_factor": round(adjustment, 3),
                "suggested_price": round(raw_price, 2),
                "source": ext.get("source"),
                "num_comps_used": ext.get("num_comps_used", 0),
                "last_pull_timestamp": ext.get("last_pull_timestamp"),
            },
        )

    # ─── Public parsing / adjustment methods ───────────────────────────────

    def parse_market_data(self, raw_data: dict, date: str) -> dict | None:
        """Parse market data for a given date.

        Expected raw_data structure (per date):
        {
            "YYYY-MM-DD": {
                "market_rates": [<float>, ...],   # list of comp nightly rates
                "occupancy": <float>,              # market occupancy 0-1
                "booking_pace": <float>,           # bookings per day in window
                "avg_quality": <float>,            # average quality of comps
            }
        }

        Returns a flat dict with market_median and market_avg_quality,
        or None if no data available for this date.
        """
        date_entry = raw_data.get(date)
        if not date_entry:
            return None

        market_rates = date_entry.get("market_rates", [])
        if not market_rates or not isinstance(market_rates, list):
            return None

        # Median of market rates
        sorted_rates = sorted(market_rates)
        n = len(sorted_rates)
        if n % 2 == 0:
            market_median = (sorted_rates[n // 2 - 1] + sorted_rates[n // 2]) / 2.0
        else:
            market_median = sorted_rates[n // 2]

        return {
            "market_median": round(market_median, 2),
            "market_avg_quality": date_entry.get("avg_quality", 0.80),
            "occupancy": date_entry.get("occupancy"),
            "booking_pace": date_entry.get("booking_pace"),
        }

    def compute_market_adjustment(
        self, market_median: float, quality_score: float, market_avg_quality: float
    ) -> float:
        """Adjust market median by relative quality vs. market average.

        adjustment = quality_score / market_avg_quality
        → above-average quality → multiplier > 1.0 (price above market median)
        → below-average quality → multiplier < 1.0 (price below market median)
        """
        if market_avg_quality <= 0:
            return 1.0
        return quality_score / market_avg_quality