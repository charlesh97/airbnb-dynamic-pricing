"""Tests for the pricing engine stacked pricing pipeline."""

import unittest
import sys

sys.path.insert(0, "src")

from pricing_engine.engine import PricingEngine


class TestPricingEngine(unittest.TestCase):
    def setUp(self):
        self.engine = PricingEngine()
        self.base_config = {
            "base_price": 250.0,
            "default_base_price": 250.0,
            "min_price": 100.0,
            "max_price": 500.0,
            "seasonal_months": {f"{m:02d}": 1.0 for m in range(1, 13)},
            "dow_multipliers": {
                "mon": 1.0,
                "tue": 1.0,
                "wed": 1.0,
                "thu": 1.0,
                "fri": 1.0,
                "sat": 1.0,
                "sun": 1.0,
            },
            "availability": {
                "booking_window_days": 120,
                "min_stay": {"default": 2, "overrides": []},
                "checkin_days": {"blocked": []},
                "checkout_days": {"blocked": []},
                "block_day_before": False,
                "block_day_after": False,
                "far_future": {"window_days": 60, "discount": 1.0},
                "last_minute": {"window_days": 7, "discount": 1.0, "threshold_occupancy": 1.0},
            },
            "pricing_adjustments": {
                "occupancy_pacing": {
                    "enabled": True,
                    "window_days": 14,
                    "target_occupancy": 0.25,
                    "sensitivity": 0.2,
                    "max_discount": 0.1,
                    "max_increase": 0.1,
                    "min_available_nights": 5,
                },
                "booking_velocity": {
                    "enabled": True,
                    "recent_window_days": 7,
                    "baseline_window_days": 60,
                    "sensitivity": 0.08,
                    "max_discount": 0.0,
                    "max_increase": 0.15,
                    "min_recent_bookings": 2,
                    "min_baseline_bookings": 3,
                },
            },
            "external_market_data": {"enabled": False},
        }

    def test_compute_range_produces_count(self):
        results = self.engine.compute_range(
            property_uid="prop1",
            from_date="2026-06-01",
            to_date="2026-06-07",
            calendar_data=[],
            bookings_in_window=[],
            config=self.base_config,
        )
        self.assertEqual(len(results), 7)

    def test_price_clamped_to_max(self):
        cfg = {**self.base_config, "max_price": 120.0}
        result = self.engine.compute_price(
            property_uid="prop1",
            date="2026-06-15",
            calendar_entry=None,
            bookings_in_window=[],
            config=cfg,
        )
        self.assertLessEqual(result.final_price, 120.0)

    def test_price_clamped_to_min(self):
        cfg = {**self.base_config, "min_price": 260.0}
        result = self.engine.compute_price(
            property_uid="prop1",
            date="2026-06-15",
            calendar_entry=None,
            bookings_in_window=[],
            config=cfg,
        )
        self.assertGreaterEqual(result.final_price, 260.0)

    def test_occupancy_above_target_increases(self):
        bookings = [
            {"checkin": "2026-06-01", "checkout": "2026-06-09", "created_dttm": "2026-05-28"},
        ]
        result = self.engine.compute_price(
            property_uid="prop1",
            date="2026-06-15",
            calendar_entry=None,
            bookings_in_window=bookings,
            config=self.base_config,
        )
        demand = result.all_factors.get("demand", {})
        occ = demand.get("occupancy_pacing", {})
        self.assertGreater(occ.get("multiplier", 1.0), 1.0)

    def test_no_legacy_factor_math_in_active_path(self):
        cfg = {
            **self.base_config,
            "demand_config": {
                "occupancy_factor": 0.99,
                "velocity_factor": 0.99,
                "demand_window_days": 14,
                "velocity_window_days": 7,
            },
        }
        result = self.engine.compute_price(
            property_uid="prop1",
            date="2026-06-15",
            calendar_entry=None,
            bookings_in_window=[],
            config=cfg,
        )
        demand = result.all_factors.get("demand", {})
        occ_inputs = demand.get("occupancy_pacing", {}).get("inputs", {})
        vel_inputs = demand.get("booking_velocity", {}).get("inputs", {})
        self.assertNotEqual(occ_inputs.get("sensitivity"), 0.99)
        self.assertNotEqual(vel_inputs.get("sensitivity"), 0.99)


if __name__ == "__main__":
    unittest.main()
