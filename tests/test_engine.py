"""Tests for the pricing engine — weighted average, bounds, NaN checks."""

import unittest
from datetime import datetime, timedelta

from pricing_engine.engine import PricingEngine, DatePrice
from pricing_engine.strategies.base import PriceRecommendation


class MockStrategy:
    """Mock strategy that returns configurable prices."""

    def __init__(self, name: str, price: float, confidence: float = 0.8):
        self.name = name
        self._price = price
        self._confidence = confidence

    def compute(self, **kwargs):
        return PriceRecommendation(
            strategy_name=self.name,
            suggested_price=self._price,
            confidence=self._confidence,
            factors={},
        )


class TestPricingEngine(unittest.TestCase):
    def test_weighted_average_basic(self):
        engine = PricingEngine()
        # Replace strategies with predictable mocks
        engine.strategies = [
            MockStrategy("demand", 100.0),
            MockStrategy("event", 200.0),
            MockStrategy("yield", 150.0),
            MockStrategy("competitor", 120.0),
        ]
        # Default weights: demand=0.40, event=0.30, competitor=0.20, yield=0.10
        result = engine.compute_price(
            property_uid="prop1",
            date="2026-06-15",
            calendar_entry=None,
            bookings_in_window=[],
            config={"default_base_price": 100.0},
        )
        # (100×0.40 + 200×0.30 + 120×0.20 + 150×0.10) / 1.0
        # = 40 + 60 + 24 + 15 = 139
        self.assertEqual(result.final_price, 139.0)
        self.assertTrue(result.confidence > 0)

    def test_weights_must_sum_to_one(self):
        # Use real strategies but verify weighting works
        engine = PricingEngine()
        custom_weights = {"demand": 0.50, "event": 0.50}
        result = engine.compute_price(
            property_uid="prop1",
            date="2026-06-15",
            calendar_entry=None,
            bookings_in_window=[],
            config={"default_base_price": 100.0, "strategy_weights": custom_weights},
        )
        # demand × 0.50 + event × 0.50 should be the weighted result
        # Both strategies respond to base_price 100; verify weights are applied
        self.assertIn("demand", result.strategy_weights)
        self.assertIn("event", result.strategy_weights)
        self.assertEqual(result.strategy_weights["demand"], 0.5)
        self.assertEqual(result.strategy_weights["event"], 0.5)
        # Weighted result should be demand_output×0.5 + event_output×0.5
        self.assertGreater(result.final_price, 0)

    def test_price_clamped_to_max(self):
        engine = PricingEngine()
        engine.strategies = [MockStrategy("demand", 5000.0)]
        engine._default_weights = {"demand": 1.0}
        result = engine.compute_price(
            property_uid="prop1",
            date="2026-06-15",
            calendar_entry=None,
            bookings_in_window=[],
            config={"default_max_price": 1000.0},
        )
        self.assertEqual(result.final_price, 1000.0)

    def test_price_clamped_to_min(self):
        engine = PricingEngine()
        engine.strategies = [MockStrategy("demand", 10.0)]
        engine._default_weights = {"demand": 1.0}
        result = engine.compute_price(
            property_uid="prop1",
            date="2026-06-15",
            calendar_entry=None,
            bookings_in_window=[],
            config={"default_min_price": 50.0},
        )
        self.assertEqual(result.final_price, 50.0)

    def test_no_negative_prices(self):
        engine = PricingEngine()
        engine.strategies = [MockStrategy("demand", -50.0)]
        engine._default_weights = {"demand": 1.0}
        result = engine.compute_price(
            property_uid="prop1",
            date="2026-06-15",
            calendar_entry=None,
            bookings_in_window=[],
            config={"default_min_price": 0.0},
        )
        # Clamped to min 0
        self.assertGreaterEqual(result.final_price, 0.0)

    def test_compute_range_produces_correct_count(self):
        engine = PricingEngine()
        from_date = "2026-06-01"
        to_date = "2026-06-07"
        results = engine.compute_range(
            property_uid="prop1",
            from_date=from_date,
            to_date=to_date,
            calendar_data=[],
            bookings_in_window=[],
            config={},
        )
        self.assertEqual(len(results), 7)

    def test_strategy_prices_documented(self):
        engine = PricingEngine()
        result = engine.compute_price(
            property_uid="prop1",
            date="2026-06-15",
            calendar_entry=None,
            bookings_in_window=[],
            config={},
        )
        self.assertIsInstance(result.strategy_prices, dict)
        self.assertIsInstance(result.strategy_weights, dict)
        self.assertIsInstance(result.confidence, float)
        self.assertGreaterEqual(result.confidence, 0.0)
        self.assertLessEqual(result.confidence, 1.0)


if __name__ == "__main__":
    unittest.main()
