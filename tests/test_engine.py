"""Tests for the pricing engine stacked pricing pipeline."""

import unittest
import sys

sys.path.insert(0, "src")

from pricing_engine.engine import PricingEngine, _round_price_to_nearest_dollar


class TestPricingEngine(unittest.TestCase):
    def setUp(self):
        self.engine = PricingEngine()
        self.base_config = {
            "base_price": 250.0,
            "default_base_price": 250.0,
            "min_price": 100.0,
            "max_price": 500.0,
            "availability": {
                "booking_window_days": 120,
                "checkin_days": {"blocked": []},
                "checkout_days": {"blocked": []},
                "block_day_before": False,
                "block_day_after": False,
            },
            "pricing_adjustments": {
                "seasonal_months_pct": {f"{m:02d}": 0.0 for m in range(1, 13)},
                "dow_pct": {
                    "mon": 0.0,
                    "tue": 0.0,
                    "wed": 0.0,
                    "thu": 0.0,
                    "fri": 0.0,
                    "sat": 0.0,
                    "sun": 0.0,
                },
                "price_adjust_pct": 0.0,
                "far_future_window_days": 9999,
                "far_future_discount_pct": 0.0,
                "last_minute_window_days": 7,
                "last_minute_discount_pct": 0.0,
                "last_minute_threshold_occupancy_pct": 100.0,
                "occupancy_pacing_enabled": True,
                "occupancy_pacing_window_days": 14,
                "occupancy_pacing_target_occupancy_pct": 25.0,
                "occupancy_pacing_sensitivity_pct": 20.0,
                "occupancy_pacing_max_discount_pct": 10.0,
                "occupancy_pacing_max_increase_pct": 10.0,
                "occupancy_pacing_min_available_nights": 5,
                "booking_velocity_enabled": True,
                "booking_velocity_recent_window_days": 7,
                "booking_velocity_baseline_window_days": 60,
                "booking_velocity_sensitivity_pct": 8.0,
                "booking_velocity_max_discount_pct": 0.0,
                "booking_velocity_max_increase_pct": 15.0,
                "booking_velocity_min_recent_bookings": 2,
                "booking_velocity_min_baseline_bookings": 3,
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

    def test_round_price_to_nearest_dollar_half_up(self):
        self.assertEqual(_round_price_to_nearest_dollar(100.49), 100.0)
        self.assertEqual(_round_price_to_nearest_dollar(100.50), 101.0)

    def test_final_recommendation_is_whole_dollar(self):
        cfg = {
            **self.base_config,
            "pricing_adjustments": {
                **self.base_config["pricing_adjustments"],
                "price_adjust_pct": 7.25,
            },
        }
        result = self.engine.compute_price(
            property_uid="prop1",
            date="2026-06-15",
            calendar_entry=None,
            bookings_in_window=[],
            config=cfg,
        )
        self.assertEqual(result.final_price, round(result.final_price))
        self.assertEqual(
            result.final_price,
            result.all_factors.get("explanation", {}).get("final_price"),
        )


if __name__ == "__main__":
    unittest.main()
