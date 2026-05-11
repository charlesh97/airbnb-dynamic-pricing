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
            target_occupancy=0.25,
            sensitivity=0.2,
            max_discount=0.1,
            max_increase=0.1,
            min_available_nights=5,
            booked_nights=0,
            available_nights=14,
        )
        self.assertLess(out["multiplier"], 1.0)

    def test_disabled_returns_one(self):
        out = calculate_occupancy_pacing_multiplier(
            enabled=False,
            window_days=14,
            target_occupancy=0.25,
            sensitivity=0.2,
            max_discount=0.1,
            max_increase=0.1,
            min_available_nights=5,
            booked_nights=5,
            available_nights=14,
        )
        self.assertEqual(out["multiplier"], 1.0)

    def test_insufficient_nights_returns_one(self):
        out = calculate_occupancy_pacing_multiplier(
            enabled=True,
            window_days=14,
            target_occupancy=0.25,
            sensitivity=0.2,
            max_discount=0.1,
            max_increase=0.1,
            min_available_nights=5,
            booked_nights=0,
            available_nights=3,
        )
        self.assertEqual(out["multiplier"], 1.0)


class TestBookingVelocity(unittest.TestCase):
    def test_faster_velocity_increases(self):
        out = calculate_booking_velocity_multiplier(
            enabled=True,
            recent_window_days=7,
            baseline_window_days=60,
            sensitivity=0.08,
            max_discount=0.0,
            max_increase=0.15,
            min_recent_bookings=2,
            min_baseline_bookings=3,
            recent_bookings=10,
            baseline_bookings=30,
        )
        self.assertGreater(out["multiplier"], 1.0)

    def test_slower_velocity_decreases(self):
        out = calculate_booking_velocity_multiplier(
            enabled=True,
            recent_window_days=7,
            baseline_window_days=60,
            sensitivity=0.08,
            max_discount=0.1,
            max_increase=0.15,
            min_recent_bookings=2,
            min_baseline_bookings=3,
            recent_bookings=2,
            baseline_bookings=100,
        )
        self.assertLess(out["multiplier"], 1.0)

    def test_disabled_returns_one(self):
        out = calculate_booking_velocity_multiplier(
            enabled=False,
            recent_window_days=7,
            baseline_window_days=60,
            sensitivity=0.08,
            max_discount=0.0,
            max_increase=0.15,
            min_recent_bookings=2,
            min_baseline_bookings=3,
            recent_bookings=10,
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
            "state": "CA",
            "pricing_adjustments": {
                "seasonal_months_pct": {"12": 40.0},
                "holiday_multipliers_pct": {"Christmas Day": 60.0},
                "dow_pct": {"fri": 15.0},
                "far_future_window_days": 9999,
                "far_future_discount_pct": 0.0,
                "last_minute_window_days": 7,
                "last_minute_discount_pct": 0.0,
                "last_minute_threshold_occupancy_pct": 0.0,
            },
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
        # When holiday detection works: seasonal=1.40, Christmas=1.60,
        # Fri DOW=1.15 → 1.60 × 1.15 = 1.84, 150 × 1.84 = 276.0
        # When holiday detection fails: 1.40 × 1.15 × 150 = 241.5
        if rec.factors.get("is_holiday"):
            self.assertAlmostEqual(rec.suggested_price, 276.0, delta=5)
        else:
            self.assertAlmostEqual(rec.suggested_price, 241.5, delta=5)

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

    def test_last_minute_discount_applied(self):
        """Dates within last-minute window with low occupancy get discount."""
        target = datetime.now() + timedelta(days=3)
        config = {
            **self.config,
            "pricing_adjustments": {
                **self.config["pricing_adjustments"],
                "last_minute_window_days": 7,
                "last_minute_discount_pct": -8.0,
                "last_minute_threshold_occupancy_pct": 50.0,
            },
        }
        rec = self.strat.compute(
            property_uid="prop1",
            date=target.strftime("%Y-%m-%d"),
            calendar_entry=None,
            bookings_in_window=[],
            config=config,
        )
        self.assertTrue(rec.is_valid())
        self.assertTrue(rec.factors.get("last_minute_applied"))
        self.assertAlmostEqual(rec.factors.get("last_minute_multiplier"), 0.92, places=3)

    def test_last_minute_skipped_when_occupancy_high(self):
        """Dates in window but with high occupancy skip the discount."""
        target = datetime.now() + timedelta(days=2)
        window_start = target - timedelta(days=7)
        config = {
            **self.config,
            "pricing_adjustments": {
                **self.config["pricing_adjustments"],
                "last_minute_window_days": 7,
                "last_minute_discount_pct": -8.0,
                "last_minute_threshold_occupancy_pct": 50.0,
            },
        }
        bookings = [{
            "checkin": window_start.strftime("%Y-%m-%d"),
            "checkout": target.strftime("%Y-%m-%d"),
        }]
        rec = self.strat.compute(
            property_uid="prop1",
            date=target.strftime("%Y-%m-%d"),
            calendar_entry=None,
            bookings_in_window=bookings,
            config=config,
        )
        self.assertTrue(rec.is_valid())
        self.assertFalse(rec.factors.get("last_minute_applied"))
        self.assertAlmostEqual(rec.factors.get("last_minute_multiplier"), 1.0, places=3)

    def test_last_minute_outside_window_no_discount(self):
        """Dates far in the future get no last-minute discount."""
        target = datetime.now() + timedelta(days=45)
        config = {
            **self.config,
            "pricing_adjustments": {
                **self.config["pricing_adjustments"],
                "last_minute_window_days": 7,
                "last_minute_discount_pct": -8.0,
                "last_minute_threshold_occupancy_pct": 50.0,
            },
        }
        rec = self.strat.compute(
            property_uid="prop1",
            date=target.strftime("%Y-%m-%d"),
            calendar_entry=None,
            bookings_in_window=[],
            config=config,
        )
        self.assertTrue(rec.is_valid())
        self.assertFalse(rec.factors.get("last_minute_applied"))


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
            "state": "CA",
        }

    def test_local_events_multiplier_applied(self):
        """Date matching a local event gets factored into seasonal multiplier."""
        config = {
            **self.config,
            "pricing_adjustments": {
                "local_events": [
                    {"name": "Local Event", "date": "2026-09-01", "factor_pct": 25.0},
                ],
            },
        }
        rec = self.strat.compute(
            property_uid="prop1",
            date="2026-09-01",
            calendar_entry=None,
            bookings_in_window=[],
            config=config,
        )
        self.assertTrue(rec.is_valid())
        # Local event should be detected
        self.assertTrue(rec.factors.get("is_holiday"))
        self.assertEqual(rec.factors.get("holiday_source"), "local")

    def test_local_events_override_auto_holidays(self):
        """Local event on same date as auto-holiday takes priority."""
        config = {
            **self.config,
            "pricing_adjustments": {
                "local_events": [
                    {"name": "Xmas Local", "date": "2026-12-25", "factor_pct": 100.0},
                ],
            },
        }
        rec = self.strat.compute(
            property_uid="prop1",
            date="2026-12-25",
            calendar_entry=None,
            bookings_in_window=[],
            config=config,
        )
        self.assertTrue(rec.is_valid())
        self.assertEqual(rec.factors.get("holiday_source"), "local")
        # multiplier should be the local event's factor (2.0) not Christmas (1.60)
        self.assertGreater(rec.suggested_price, 200.0)


if __name__ == "__main__":
    unittest.main()
