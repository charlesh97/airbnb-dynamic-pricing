"""Tests for AvailabilityStrategy."""

import unittest
import sys
from datetime import datetime, timedelta

sys.path.insert(0, "src")

from pricing_engine.strategies.availability import (
    AvailabilityStrategy,
    AvailabilityResult,
    _compute_min_stay,
    _check_gap,
    _get_avail_config,
    GapCheckResult,
)


class TestAvailabilityStrategy(unittest.TestCase):
    def setUp(self):
        self.strat = AvailabilityStrategy()
        self.base_config = {
            "availability": {
                "min_stay": {"default": 2, "overrides": []},
                "checkin_days": {"blocked": []},
                "checkout_days": {"blocked": []},
                "same_day_checkin": {"allowed": False},
                "same_day_checkout": {"allowed": False},
                "gap_handling": {"auto_block_gaps": False, "min_gap_nights": 1},
            }
        }

    def test_default_min_stay(self):
        rec = self.strat.compute(
            property_uid="prop1",
            date=(datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d"),
            calendar_entry=None,
            bookings_in_window=[],
            config=self.base_config,
        )
        self.assertTrue(rec.is_available)
        self.assertEqual(rec.min_stay, 2)

    def test_blocked_checkin_day(self):
        config = {
            "availability": {
                "min_stay": {"default": 2, "overrides": []},
                "checkin_days": {"blocked": ["wed"]},
                "checkout_days": {"blocked": []},
                "same_day_checkin": {"allowed": False},
                "same_day_checkout": {"allowed": False},
                "gap_handling": {"auto_block_gaps": False},
            }
        }
        # Find a Wednesday
        d = datetime.now()
        while d.strftime("%a").lower() != "wed":
            d += timedelta(days=1)
        rec = self.strat.compute(
            property_uid="prop1",
            date=d.strftime("%Y-%m-%d"),
            calendar_entry=None,
            bookings_in_window=[],
            config=config,
        )
        self.assertFalse(rec.is_available)
        self.assertIn("checkin", rec.blocked_reason)

    def test_blocked_checkout_day(self):
        config = {
            "availability": {
                "min_stay": {"default": 2, "overrides": []},
                "checkin_days": {"blocked": []},
                "checkout_days": {"blocked": ["wed", "thu"]},
                "same_day_checkin": {"allowed": False},
                "same_day_checkout": {"allowed": False},
                "gap_handling": {"auto_block_gaps": False},
            }
        }
        d = datetime.now()
        while d.strftime("%a").lower() != "thu":
            d += timedelta(days=1)
        rec = self.strat.compute(
            property_uid="prop1",
            date=d.strftime("%Y-%m-%d"),
            calendar_entry=None,
            bookings_in_window=[],
            config=config,
        )
        self.assertFalse(rec.is_available)
        self.assertIn("checkout", rec.blocked_reason)

    def test_min_stay_override_dow(self):
        config = {
            "availability": {
                "min_stay": {
                    "default": 2,
                    "overrides": [
                        {"when": {"dow": ["fri", "sat", "sun"]}, "min_nights": 3}
                    ],
                },
                "checkin_days": {"blocked": []},
                "checkout_days": {"blocked": []},
                "same_day_checkin": {"allowed": False},
                "same_day_checkout": {"allowed": False},
                "gap_handling": {"auto_block_gaps": False},
            }
        }
        d = datetime.now()
        while d.strftime("%a").lower() != "fri":
            d += timedelta(days=1)
        rec = self.strat.compute(
            property_uid="prop1",
            date=d.strftime("%Y-%m-%d"),
            calendar_entry=None,
            bookings_in_window=[],
            config=config,
        )
        self.assertTrue(rec.is_available)
        self.assertEqual(rec.min_stay, 3)

    def test_min_stay_override_month(self):
        config = {
            "availability": {
                "min_stay": {
                    "default": 2,
                    "overrides": [
                        {"when": {"months": [7, 8]}, "min_nights": 4}
                    ],
                },
                "checkin_days": {"blocked": []},
                "checkout_days": {"blocked": []},
                "same_day_checkin": {"allowed": False},
                "same_day_checkout": {"allowed": False},
                "gap_handling": {"auto_block_gaps": False},
            }
        }
        d = datetime(2026, 8, 15)
        rec = self.strat.compute(
            property_uid="prop1",
            date=d.strftime("%Y-%m-%d"),
            calendar_entry=None,
            bookings_in_window=[],
            config=config,
        )
        self.assertTrue(rec.is_available)
        self.assertEqual(rec.min_stay, 4)

    def test_gap_auto_block(self):
        config = {
            "availability": {
                "min_stay": {"default": 2, "overrides": []},
                "checkin_days": {"blocked": []},
                "checkout_days": {"blocked": []},
                "same_day_checkin": {"allowed": False},
                "same_day_checkout": {"allowed": False},
                "gap_handling": {"auto_block_gaps": True, "min_gap_nights": 1},
            }
        }
        # Two bookings: May 10-13 and May 15-20. May 14 is an isolated gap night.
        bookings = [
            {"checkin": "2026-05-10", "checkout": "2026-05-13"},
            {"checkin": "2026-05-15", "checkout": "2026-05-20"},
        ]
        # May 14 is gap night between the two bookings
        rec = self.strat.compute(
            property_uid="prop1",
            date="2026-05-14",
            calendar_entry=None,
            bookings_in_window=bookings,
            config=config,
        )
        self.assertFalse(rec.is_available)
        self.assertIn("gap", rec.blocked_reason.lower())


class TestMinStayComputation(unittest.TestCase):
    def test_default_wins(self):
        cfg = {"min_stay": {"default": 3, "overrides": []}}
        d = datetime(2026, 5, 15)
        self.assertEqual(_compute_min_stay(cfg, d), 3)

    def test_month_override_wins(self):
        cfg = {
            "min_stay": {
                "default": 2,
                "overrides": [
                    {"when": {"months": [12]}, "min_nights": 5}
                ],
            }
        }
        d = datetime(2026, 12, 25)
        self.assertEqual(_compute_min_stay(cfg, d), 5)


if __name__ == "__main__":
    unittest.main()