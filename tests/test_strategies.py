"""Tests for individual pricing strategies."""

import unittest
import sys
from datetime import datetime, timedelta
from unittest.mock import patch

sys.path.insert(0, "src")

from pricing_engine.strategies.demand import (
    calculate_booking_velocity_multiplier,
    calculate_occupancy_pacing_multiplier,
)
from pricing_engine.strategies.event import EventStrategy
from pricing_engine.strategies.yield_ import YieldStrategy
from pricing_engine.strategies.competitor import CompetitorStrategy


class TestOccupancyPacing(unittest.TestCase):
    def test_actual_equals_target_is_one(self):
        out = calculate_occupancy_pacing_multiplier(
            enabled=True,
            window_days=14,
            target_occupancy=0.5,
            sensitivity=0.2,
            max_discount=0.1,
            max_increase=0.1,
            min_available_nights=5,
            booked_nights=5,
            available_nights=10,
        )
        self.assertAlmostEqual(out["multiplier"], 1.0, places=6)

    def test_above_target_increases(self):
        out = calculate_occupancy_pacing_multiplier(
            enabled=True,
            window_days=14,
            target_occupancy=0.25,
            sensitivity=0.2,
            max_discount=0.1,
            max_increase=0.1,
            min_available_nights=5,
            booked_nights=8,
            available_nights=14,
        )
        self.assertGreater(out["multiplier"], 1.0)

    def test_below_target_decreases(self):
        out = calculate_occupancy_pacing_multiplier(
            enabled=True,
            window_days=14,
            target_occupancy=0.7,
            sensitivity=0.2,
            max_discount=0.1,
            max_increase=0.1,
            min_available_nights=5,
            booked_nights=1,
            available_nights=14,
        )
        self.assertLess(out["multiplier"], 1.0)

    def test_insufficient_available_returns_one(self):
        out = calculate_occupancy_pacing_multiplier(
            enabled=True,
            window_days=14,
            target_occupancy=0.25,
            sensitivity=0.2,
            max_discount=0.1,
            max_increase=0.1,
            min_available_nights=5,
            booked_nights=1,
            available_nights=2,
        )
        self.assertEqual(out["reason"], "insufficient_available_nights")
        self.assertEqual(out["multiplier"], 1.0)


class TestBookingVelocity(unittest.TestCase):
    def test_ratio_one_is_one(self):
        out = calculate_booking_velocity_multiplier(
            enabled=True,
            recent_window_days=7,
            baseline_window_days=60,
            sensitivity=0.08,
            max_discount=0.0,
            max_increase=0.15,
            min_recent_bookings=2,
            min_baseline_bookings=3,
            recent_bookings=7,
            baseline_bookings=60,
        )
        self.assertAlmostEqual(out["multiplier"], 1.0, places=6)

    def test_ratio_high_caps(self):
        out = calculate_booking_velocity_multiplier(
            enabled=True,
            recent_window_days=7,
            baseline_window_days=60,
            sensitivity=0.08,
            max_discount=0.0,
            max_increase=0.15,
            min_recent_bookings=2,
            min_baseline_bookings=3,
            recent_bookings=20,
            baseline_bookings=3,
        )
        self.assertAlmostEqual(out["multiplier"], 1.15, places=6)

    def test_low_ratio_no_discount_when_max_discount_zero(self):
        out = calculate_booking_velocity_multiplier(
            enabled=True,
            recent_window_days=7,
            baseline_window_days=60,
            sensitivity=0.08,
            max_discount=0.0,
            max_increase=0.15,
            min_recent_bookings=2,
            min_baseline_bookings=3,
            recent_bookings=2,
            baseline_bookings=300,
        )
        self.assertEqual(out["multiplier"], 1.0)

    def test_insufficient_recent_returns_one(self):
        out = calculate_booking_velocity_multiplier(
            enabled=True,
            recent_window_days=7,
            baseline_window_days=60,
            sensitivity=0.08,
            max_discount=0.0,
            max_increase=0.15,
            min_recent_bookings=2,
            min_baseline_bookings=3,
            recent_bookings=1,
            baseline_bookings=10,
        )
        self.assertEqual(out["reason"], "insufficient_recent_bookings")
        self.assertEqual(out["multiplier"], 1.0)


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
        self.assertGreater(rec.suggested_price, 0)

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
        self.assertGreater(rec.suggested_price, 0)


class TestYieldStrategy(unittest.TestCase):
    def setUp(self):
        self.strat = YieldStrategy()
        self.config = {
            "default_base_price": 150.0,
            "default_min_price": 50.0,
            "default_max_price": 2000.0,
            "availability": {
                "last_minute": {
                    "window_days": 7,
                    "discount": 0.92,
                    "threshold_occupancy": 0.5,
                }
            },
        }

    def test_outside_window_no_last_minute_adjustment(self):
        rec = self.strat.compute(
            property_uid="prop1",
            date=(datetime.now() + timedelta(days=45)).strftime("%Y-%m-%d"),
            calendar_entry=None,
            bookings_in_window=[],
            config=self.config,
        )
        self.assertTrue(rec.is_valid())
        self.assertEqual(rec.factors.get("last_minute_adjustment"), 1.0)

    def test_within_window_applies_discount_at_low_occupancy(self):
        rec = self.strat.compute(
            property_uid="prop1",
            date=(datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d"),
            calendar_entry=None,
            bookings_in_window=[],
            config=self.config,
        )
        self.assertTrue(rec.is_valid())
        self.assertAlmostEqual(rec.factors.get("last_minute_adjustment"), 0.92, places=3)

    def test_within_window_skips_discount_when_occupancy_high(self):
        target = datetime.now() + timedelta(days=2)
        window_start = target - timedelta(days=7)
        bookings = [{
            "checkin": window_start.strftime("%Y-%m-%d"),
            "checkout": target.strftime("%Y-%m-%d"),
        }]
        rec = self.strat.compute(
            property_uid="prop1",
            date=target.strftime("%Y-%m-%d"),
            calendar_entry=None,
            bookings_in_window=bookings,
            config=self.config,
        )
        self.assertTrue(rec.is_valid())
        self.assertEqual(rec.factors.get("last_minute_adjustment"), 1.0)


class TestCompetitorStrategy(unittest.TestCase):
    def setUp(self):
        self.strat = CompetitorStrategy()
        self.config = {
            "default_base_price": 150.0,
            "default_min_price": 50.0,
            "default_max_price": 2000.0,
            "external_market_data": {
                "enabled": True,
                "raw_data": {
                    "2026-06-15": {"market_rates": [180.0, 200.0, 220.0], "avg_quality": 0.80},
                },
                "confidence": 0.75,
            },
            "quality_scores": {"prop1": 0.90},
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
        self.assertAlmostEqual(rec.suggested_price, 225.0, delta=5)

    def test_no_market_data_falls_back_to_base(self):
        rec = self.strat.compute(
            property_uid="prop1",
            date="2026-06-20",
            calendar_entry=None,
            bookings_in_window=[],
            config={"default_base_price": 150.0, "external_market_data": {"enabled": False}},
        )
        self.assertFalse(rec.is_valid())
        self.assertEqual(rec.confidence, 0.0)
        self.assertEqual(rec.suggested_price, 0.0)


class TestEventStrategyLocalEvents(unittest.TestCase):
    def setUp(self):
        self.strat = EventStrategy()
        self.config = {
            "default_base_price": 150.0,
            "default_min_price": 50.0,
            "default_max_price": 2000.0,
        }

    def test_local_events_multiplier_applied(self):
        """Date matching a local event → multiplier applied"""
        config = {
            **self.config,
            "local_events": [
                {"date": "2026-07-04", "factor": 1.30},
                {"date": "2026-09-01", "factor": 1.25},
            ],
            "local_events_config": {"default_factor": 1.10},
        }
        rec = self.strat.compute(
            property_uid="prop1",
            date="2026-09-01",
            calendar_entry=None,
            bookings_in_window=[],
            config=config,
        )
        self.assertTrue(rec.is_valid())
        base = self.strat.compute(
            property_uid="prop1",
            date="2026-09-01",
            calendar_entry=None,
            bookings_in_window=[],
            config=self.config,
        )
        self.assertGreater(rec.suggested_price, base.suggested_price)
        self.assertEqual(rec.factors.get("local_event_applied"), "1.25")


if __name__ == "__main__":
    unittest.main()
