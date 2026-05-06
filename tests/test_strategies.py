"""Tests for individual pricing strategies."""

import unittest
import sys
from datetime import datetime, timedelta

sys.path.insert(0, "src")

from pricing_engine.strategies.demand import DemandStrategy
from pricing_engine.strategies.event import EventStrategy
from pricing_engine.strategies.yield_ import YieldStrategy
from pricing_engine.strategies.competitor import CompetitorStrategy


class TestDemandStrategy(unittest.TestCase):
    def setUp(self):
        self.strat = DemandStrategy()
        self.config = {
            "default_base_price": 150.0,
            "default_min_price": 50.0,
            "default_max_price": 2000.0,
            "demand_window_days": 14,
            "velocity_window_days": 7,
            "occupancy_factor": 0.30,
            "velocity_factor": 0.15,
        }

    def test_base_price_used_when_no_bookings(self):
        rec = self.strat.compute(
            property_uid="prop1",
            date=(datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d"),
            calendar_entry=None,
            bookings_in_window=[],
            config=self.config,
        )
        self.assertTrue(rec.is_valid())
        self.assertGreater(rec.suggested_price, 0)
        # With no bookings, multiplier should be ~1.0
        self.assertAlmostEqual(rec.suggested_price, 150.0, delta=20)

    def test_high_occupancy_increases_price(self):
        bookings = [
            {
                "checkin": (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d"),
                "checkout": (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d"),
            }
        ]
        rec = self.strat.compute(
            property_uid="prop1",
            date=(datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d"),
            calendar_entry=None,
            bookings_in_window=bookings,
            config=self.config,
        )
        self.assertTrue(rec.is_valid())
        self.assertGreater(rec.suggested_price, 150.0)

    def test_last_minute_discount_low_occupancy(self):
        # 1-3 days out, low occupancy
        rec = self.strat.compute(
            property_uid="prop1",
            date=(datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d"),
            calendar_entry=None,
            bookings_in_window=[],
            config={**self.config, "last_minute_discount": 0.92},
        )
        self.assertTrue(rec.is_valid())
        # Should apply ~0.92 discount multiplier for last-minute
        self.assertLess(rec.suggested_price, 150.0)

    def test_invalid_price_rejected(self):
        rec = self.strat.compute(
            property_uid="prop1",
            date=(datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d"),
            calendar_entry=None,
            bookings_in_window=[],
            config=self.config,
        )
        self.assertTrue(rec.is_valid())


class TestEventStrategy(unittest.TestCase):
    def setUp(self):
        self.strat = EventStrategy()
        self.config = {
            "default_base_price": 150.0,
            "default_min_price": 50.0,
            "default_max_price": 2000.0,
        }

    def test_christmas_premium(self):
        rec = self.strat.compute(
            property_uid="prop1",
            date="2026-12-25",
            calendar_entry=None,
            bookings_in_window=[],
            config=self.config,
        )
        self.assertTrue(rec.is_valid())
        # Christmas (1.60) × Friday night weekend premium (1.15) = 1.84
        # 150 × 1.84 = 276.0
        self.assertAlmostEqual(rec.suggested_price, 276.0, delta=20)

    def test_normal_day(self):
        rec = self.strat.compute(
            property_uid="prop1",
            date="2026-06-15",
            calendar_entry=None,
            bookings_in_window=[],
            config=self.config,
        )
        self.assertTrue(rec.is_valid())
        # Summer mid-month ~1.15 multiplier → ~172.5
        self.assertAlmostEqual(rec.suggested_price, 172.5, delta=20)

    def test_weekend_night_premium(self):
        # Find a Saturday
        saturday = datetime.now()
        while saturday.weekday() != 5:
            saturday += timedelta(days=1)

        rec = self.strat.compute(
            property_uid="prop1",
            date=saturday.strftime("%Y-%m-%d"),
            calendar_entry=None,
            bookings_in_window=[],
            config=self.config,
        )
        self.assertTrue(rec.is_valid())
        # Weekend gets 1.15x multiplier
        self.assertGreater(rec.suggested_price, 150.0)


class TestYieldStrategy(unittest.TestCase):
    def setUp(self):
        self.strat = YieldStrategy()
        self.config = {
            "default_base_price": 150.0,
            "default_min_price": 50.0,
            "default_max_price": 2000.0,
            "advance_lead_factor": 1.05,
            "mid_lead_factor": 1.10,
            "short_lead_factor": 1.15,
            "last_minute_lead_factor": 1.20,
            "opportunity_threshold_nights": 7,
            "low_opportunity_factor": 1.18,
            "high_opportunity_factor": 1.05,
            "base_churn_probability": 0.10,
        }

    def test_advance_booking_lower_multiplier(self):
        rec_advance = self.strat.compute(
            property_uid="prop1",
            date=(datetime.now() + timedelta(days=45)).strftime("%Y-%m-%d"),
            calendar_entry=None,
            bookings_in_window=[],
            config=self.config,
        )
        rec_last = self.strat.compute(
            property_uid="prop1",
            date=(datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d"),
            calendar_entry=None,
            bookings_in_window=[],
            config=self.config,
        )
        self.assertTrue(rec_advance.is_valid())
        self.assertTrue(rec_last.is_valid())
        # Last-minute should be higher than advance
        self.assertGreater(rec_last.suggested_price, rec_advance.suggested_price)

    def test_recent_high_bookings_reduces_aggressiveness(self):
        bookings = [
            {
                "checkout": (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d"),
            }
            for _ in range(10)
        ]
        rec = self.strat.compute(
            property_uid="prop1",
            date=(datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d"),
            calendar_entry=None,
            bookings_in_window=bookings,
            config=self.config,
        )
        self.assertTrue(rec.is_valid())


class TestCompetitorStrategy(unittest.TestCase):
    def setUp(self):
        self.strat = CompetitorStrategy()
        self.config = {
            "default_base_price": 150.0,
            "default_min_price": 50.0,
            "default_max_price": 2000.0,
            "market_rates": {
                "2026-06-15": 200.0,
                "prop1": 200.0,
            },
            "quality_scores": {"prop1": 0.90},
            "market_avg_quality": 0.80,
        }

    def test_market_data_used(self):
        rec = self.strat.compute(
            property_uid="prop1",
            date="2026-06-15",
            calendar_entry=None,
            bookings_in_window=[],
            config=self.config,
        )
        self.assertTrue(rec.is_valid())
        # Market median 200 × quality 0.90/0.80 = 225
        self.assertAlmostEqual(rec.suggested_price, 225.0, delta=5)

    def test_no_market_data_falls_back_to_base(self):
        rec = self.strat.compute(
            property_uid="prop1",
            date="2026-06-20",
            calendar_entry=None,
            bookings_in_window=[],
            config={"default_base_price": 150.0},
        )
        self.assertTrue(rec.is_valid())
        # Falls back to base price, confidence should be 0
        self.assertEqual(rec.confidence, 0.0)
        self.assertEqual(rec.suggested_price, 150.0)


if __name__ == "__main__":
    unittest.main()
