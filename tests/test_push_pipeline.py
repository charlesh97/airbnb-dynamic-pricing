"""Tests for push_pipeline.py — the single push-to-iGMS pipeline."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import sys
from pathlib import Path

# Make src/ importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Prevent real igms_wrapper / holidays imports from breaking collection
sys.modules["igms_wrapper"] = MagicMock()
sys.modules["igms_wrapper.client"] = MagicMock()
sys.modules["holidays"] = MagicMock()

from pricing_engine.push_pipeline import (  # noqa: E402
    PushPipelineRequest,
    run_push_pipeline,
    _build_live_day_map,
    _build_booked_nights_set,
    _coerce_is_available,
    _parse_date,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_merged_config(booking_window_days=120, base_price=150.0):
    return {
        "property_uid": "test-prop",
        "name": "Test Property",
        "base_price": base_price,
        "min_price": 50.0,
        "max_price": 2000.0,
        "pricing_adjustments": {
            "seasonal_months_pct": {f"{m:02d}": 0.0 for m in range(1, 13)},
            "dow_pct": {
                "mon": 0.0, "tue": 0.0, "wed": 0.0, "thu": 0.0,
                "fri": 0.0, "sat": 0.0, "sun": 0.0,
            },
            "price_adjust_pct": 0.0,
            "holiday_buffer_days": 0,
            "holiday_buffer_slope_pct": 0.0,
            "holiday_multipliers_pct": {},
            "holiday_default_pct": 0.0,
            "local_events": [],
            "far_future_window_days": 9999,
            "far_future_discount_pct": 0.0,
            "last_minute_window_days": 1,
            "last_minute_discount_pct": 0.0,
            "last_minute_threshold_occupancy_pct": 0.0,
            "occupancy_pacing_enabled": True,
            "occupancy_pacing_window_days": 14,
            "occupancy_pacing_target_occupancy_pct": 25.0,
            "occupancy_pacing_sensitivity_pct": 30.0,
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
        "availability": {
            "booking_window_days": booking_window_days,
            "checkin_days": {"blocked": []},
            "checkout_days": {"blocked": []},
            "block_day_before": False,
            "block_day_after": False,
        },
        "config_schema_version": 4,
    }


def _make_calendar_entry(date_str, price=150.0, is_available=True):
    return {
        "date": date_str,
        "price": price,
        "is_available": is_available,
        "listing_uid": "lst_001",
        "listing_name": "Test Listing",
    }


def _make_booking(checkin, checkout, status="confirmed"):
    return {
        "booking_id": f"bk_{checkin}",
        "property_uid": "test-prop",
        "listing_uid": "lst_001",
        "platform_type": "airbnb",
        "checkin": checkin,
        "checkout": checkout,
        "created_dttm": f"{checkin}T12:00:00",
        "booking_status": status,
        "nights": 3,
        "gross_rental_price": 450.0,
        "guests": 2,
    }


def _today():
    return datetime.now().date()


# ── unit-tests for internal helpers ───────────────────────────────────────────


class TestCoerceIsAvailable(unittest.TestCase):
    def test_bool_true(self):
        self.assertTrue(_coerce_is_available({"is_available": True}))

    def test_bool_false(self):
        self.assertFalse(_coerce_is_available({"is_available": False}))

    def test_int_one(self):
        self.assertTrue(_coerce_is_available({"is_available": 1}))

    def test_int_zero(self):
        self.assertFalse(_coerce_is_available({"is_available": 0}))

    def test_str_true(self):
        self.assertTrue(_coerce_is_available({"is_available": "true"}))

    def test_str_false(self):
        self.assertFalse(_coerce_is_available({"is_available": "false"}))

    def test_status_available(self):
        self.assertTrue(_coerce_is_available({"status": "available"}))

    def test_status_blocked(self):
        self.assertFalse(_coerce_is_available({"status": "blocked"}))

    def test_status_booked(self):
        self.assertFalse(_coerce_is_available({"status": "booked"}))

    def test_missing_both_returns_none(self):
        self.assertIsNone(_coerce_is_available({"price": 100}))


class TestParseDate(unittest.TestCase):
    def test_iso_date(self):
        self.assertEqual(_parse_date("2026-06-15"), date(2026, 6, 15))

    def test_datetime_str(self):
        self.assertEqual(_parse_date("2026-06-15T12:00:00"), date(2026, 6, 15))

    def test_empty(self):
        self.assertIsNone(_parse_date(""))

    def test_none(self):
        self.assertIsNone(_parse_date(None))


class TestBuildLiveDayMap(unittest.TestCase):
    def test_builds_map(self):
        entries = [
            {"date": "2026-06-01", "price": 100, "is_available": True},
            {"date": "2026-06-02", "price": 120, "is_available": False},
        ]
        result = _build_live_day_map(entries)
        self.assertEqual(result["2026-06-01"]["price"], 100)
        self.assertTrue(result["2026-06-01"]["is_available"])
        self.assertEqual(result["2026-06-02"]["price"], 120)
        self.assertFalse(result["2026-06-02"]["is_available"])

    def test_skips_missing_date(self):
        entries = [{"price": 100}]
        result = _build_live_day_map(entries)
        self.assertEqual(len(result), 0)

    def test_coerces_string_numeric_fields(self):
        entries = [
            {"date": "2026-06-01", "price": "150.0", "is_available": "true"},
        ]
        result = _build_live_day_map(entries)
        self.assertEqual(result["2026-06-01"]["price"], 150.0)
        self.assertTrue(result["2026-06-01"]["is_available"])

    def test_invalid_numeric_fields_become_none(self):
        entries = [
            {"date": "2026-06-01", "price": "nope", "is_available": True},
        ]
        result = _build_live_day_map(entries)
        self.assertIsNone(result["2026-06-01"]["price"])


class TestBuildBookedNightsSet(unittest.TestCase):
    def test_builds_range(self):
        bookings = [_make_booking("2026-06-10", "2026-06-13")]
        result = _build_booked_nights_set(bookings)
        self.assertIn("2026-06-10", result)
        self.assertIn("2026-06-11", result)
        self.assertIn("2026-06-12", result)
        self.assertNotIn("2026-06-13", result)

    def test_skips_non_confirmed(self):
        bookings = [_make_booking("2026-06-10", "2026-06-12", status="pending")]
        result = _build_booked_nights_set(bookings)
        self.assertEqual(len(result), 0)

    def test_skips_bad_dates(self):
        bookings = [{"booking_status": "confirmed", "checkin": "", "checkout": ""}]
        result = _build_booked_nights_set(bookings)
        self.assertEqual(len(result), 0)


# ── pipeline integration tests ────────────────────────────────────────────────


class TestPushPipeline(unittest.TestCase):

    # ── test 1: effective window is booking_window_days + 60 ───────────────

    @patch("pricing_engine.push_pipeline.fetch_bookings_for_window")
    @patch("pricing_engine.push_pipeline.PricingClient")
    @patch("pricing_engine.push_pipeline.EngineConfig.from_env")
    @patch("pricing_engine.push_pipeline.PropertyConfigStore")
    def test_effective_window_is_booking_window_plus_60(
        self, mock_store_cls, mock_ecfg_from_env, mock_pc_cls, mock_fetch_bk
    ):
        config = _make_merged_config(booking_window_days=100)
        mock_store = MagicMock()
        mock_store.merge_with_env_defaults.return_value = config
        mock_store_cls.return_value = mock_store

        mock_ecfg = MagicMock()
        mock_ecfg.igms_access_token = "fake-token"
        mock_ecfg_from_env.return_value = mock_ecfg

        mock_client = MagicMock()
        mock_client.access_token = "fake-token"
        mock_client.get_calendar.return_value = []
        mock_pc_cls.return_value = mock_client
        mock_fetch_bk.return_value = []

        result = run_push_pipeline(PushPipelineRequest(property_uid="test-prop"))

        self.assertEqual(result.base_booking_window_days, 100)
        self.assertEqual(result.effective_window_days, 160)

    # ── test 2: no writes outside effective window ────────────────────────

    @patch("pricing_engine.push_pipeline.fetch_bookings_for_window")
    @patch("pricing_engine.push_pipeline.PricingClient")
    @patch("pricing_engine.push_pipeline.EngineConfig.from_env")
    @patch("pricing_engine.push_pipeline.PropertyConfigStore")
    def test_no_writes_outside_effective_window(
        self, mock_store_cls, mock_ecfg_from_env, mock_pc_cls, mock_fetch_bk
    ):
        config = _make_merged_config(booking_window_days=1)
        mock_store = MagicMock()
        mock_store.merge_with_env_defaults.return_value = config
        mock_store_cls.return_value = mock_store

        mock_ecfg = MagicMock()
        mock_ecfg.igms_access_token = "fake-token"
        mock_ecfg_from_env.return_value = mock_ecfg

        mock_client = MagicMock()
        mock_client.access_token = "fake-token"
        mock_client.get_calendar.return_value = []
        mock_pc_cls.return_value = mock_client
        mock_fetch_bk.return_value = []

        result = run_push_pipeline(PushPipelineRequest(property_uid="test-prop"))
        self.assertEqual(result.dates_skipped_outside_window, 0)
        self.assertEqual(result.effective_window_days, 61)

    # ── test 3: booked nights are skipped ─────────────────────────────────

    @patch("pricing_engine.push_pipeline.fetch_bookings_for_window")
    @patch("pricing_engine.push_pipeline.PricingClient")
    @patch("pricing_engine.push_pipeline.EngineConfig.from_env")
    @patch("pricing_engine.push_pipeline.PropertyConfigStore")
    def test_booked_nights_skipped(
        self, mock_store_cls, mock_ecfg_from_env, mock_pc_cls, mock_fetch_bk
    ):
        config = _make_merged_config(booking_window_days=5)
        mock_store = MagicMock()
        mock_store.merge_with_env_defaults.return_value = config
        mock_store_cls.return_value = mock_store

        mock_ecfg = MagicMock()
        mock_ecfg.igms_access_token = "fake-token"
        mock_ecfg_from_env.return_value = mock_ecfg

        mock_client = MagicMock()
        mock_client.access_token = "fake-token"
        mock_client.get_calendar.return_value = []
        mock_pc_cls.return_value = mock_client

        # Book tomorrow through day after tomorrow
        t = _today()
        checkin = (t + timedelta(days=1)).isoformat()
        checkout = (t + timedelta(days=3)).isoformat()
        mock_fetch_bk.return_value = [_make_booking(checkin, checkout)]

        result = run_push_pipeline(PushPipelineRequest(property_uid="test-prop"))
        self.assertGreaterEqual(result.dates_skipped_booked, 1)

    # ── test 4: live-blocked dates are skipped and reported ───────────────

    @patch("pricing_engine.push_pipeline.fetch_bookings_for_window")
    @patch("pricing_engine.push_pipeline.PricingClient")
    @patch("pricing_engine.push_pipeline.EngineConfig.from_env")
    @patch("pricing_engine.push_pipeline.PropertyConfigStore")
    def test_live_blocked_skipped_and_reported(
        self, mock_store_cls, mock_ecfg_from_env, mock_pc_cls, mock_fetch_bk
    ):
        t = _today()
        blocked_str = (t + timedelta(days=2)).isoformat()

        config = _make_merged_config(booking_window_days=5)
        mock_store = MagicMock()
        mock_store.merge_with_env_defaults.return_value = config
        mock_store_cls.return_value = mock_store

        mock_ecfg = MagicMock()
        mock_ecfg.igms_access_token = "fake-token"
        mock_ecfg_from_env.return_value = mock_ecfg

        mock_client = MagicMock()
        mock_client.access_token = "fake-token"
        mock_client.get_calendar.return_value = [
            _make_calendar_entry(blocked_str, is_available=False)
        ]
        mock_pc_cls.return_value = mock_client
        mock_fetch_bk.return_value = []

        result = run_push_pipeline(PushPipelineRequest(property_uid="test-prop"))
        self.assertGreaterEqual(result.dates_skipped_live_blocked, 1)
        self.assertIn(blocked_str, result.skipped_live_blocked_dates)

    # ── test 5a: no diff → no push ────────────────────────────────────────

    @patch("pricing_engine.push_pipeline.fetch_bookings_for_window")
    @patch("pricing_engine.push_pipeline.PricingClient")
    @patch("pricing_engine.push_pipeline.EngineConfig.from_env")
    @patch("pricing_engine.push_pipeline.PropertyConfigStore")
    def test_diff_only_no_diff_no_push(
        self, mock_store_cls, mock_ecfg_from_env, mock_pc_cls, mock_fetch_bk
    ):
        t = _today()
        today_str = t.isoformat()
        config = _make_merged_config(booking_window_days=1)
        mock_store = MagicMock()
        mock_store.merge_with_env_defaults.return_value = config
        mock_store_cls.return_value = mock_store

        mock_ecfg = MagicMock()
        mock_ecfg.igms_access_token = "fake-token"
        mock_ecfg_from_env.return_value = mock_ecfg

        # Live price matches desired output.
        mock_client = MagicMock()
        mock_client.access_token = "fake-token"
        mock_client.get_calendar.return_value = [
            _make_calendar_entry(today_str, price=150.0)
        ]
        mock_pc_cls.return_value = mock_client
        mock_fetch_bk.return_value = []

        # Mock engine so all results have price=150 and available=True.
        with patch("pricing_engine.push_pipeline.PricingEngine") as mock_eng_cls:
            mock_engine = MagicMock()
            mock_eng_cls.return_value = mock_engine

            from pricing_engine.engine import DatePrice
            mock_dp = DatePrice(
                date=today_str,
                property_uid="test-prop",
                final_price=150.0,
                strategy_prices={},
                confidence=0.9,
                all_factors={},
            )
            mock_engine.compute_range.return_value = [mock_dp]
            mock_engine.compute_availability.return_value = MagicMock(
                is_available=True,
                blocked_reason=None,
            )

            result = run_push_pipeline(PushPipelineRequest(property_uid="test-prop"))

        self.assertEqual(result.price_updates_sent, 0)
        self.assertEqual(result.availability_updates_sent, 0)
        mock_client.set_calendar_batch.assert_not_called()

    # ── test 5b: price diff → push ────────────────────────────────────────

    @patch("pricing_engine.push_pipeline.fetch_bookings_for_window")
    @patch("pricing_engine.push_pipeline.PricingClient")
    @patch("pricing_engine.push_pipeline.EngineConfig.from_env")
    @patch("pricing_engine.push_pipeline.PropertyConfigStore")
    def test_diff_only_price_diff_pushes(
        self, mock_store_cls, mock_ecfg_from_env, mock_pc_cls, mock_fetch_bk
    ):
        t = _today()
        today_str = t.isoformat()
        config = _make_merged_config(booking_window_days=1)
        mock_store = MagicMock()
        mock_store.merge_with_env_defaults.return_value = config
        mock_store_cls.return_value = mock_store

        mock_ecfg = MagicMock()
        mock_ecfg.igms_access_token = "fake-token"
        mock_ecfg_from_env.return_value = mock_ecfg

        # Live price=100, desired=150 → diff
        mock_client = MagicMock()
        mock_client.access_token = "fake-token"
        mock_client.get_calendar.return_value = [
            _make_calendar_entry(today_str, price=100.0)
        ]
        mock_result = MagicMock()
        mock_result.status_code = 200
        mock_client.set_calendar_batch.return_value = mock_result
        mock_pc_cls.return_value = mock_client
        mock_fetch_bk.return_value = []

        with patch("pricing_engine.push_pipeline.PricingEngine") as mock_eng_cls:
            mock_engine = MagicMock()
            mock_eng_cls.return_value = mock_engine

            from pricing_engine.engine import DatePrice
            mock_dp = DatePrice(
                date=today_str,
                property_uid="test-prop",
                final_price=150.0,
                strategy_prices={},
                confidence=0.9,
                all_factors={},
            )
            mock_engine.compute_range.return_value = [mock_dp]
            mock_engine.compute_availability.return_value = MagicMock(
                is_available=True,
                blocked_reason=None,
            )

            result = run_push_pipeline(PushPipelineRequest(property_uid="test-prop"))

        self.assertGreaterEqual(result.price_updates_sent, 1)
        mock_client.set_calendar_batch.assert_called_once()
        sent_days = mock_client.set_calendar_batch.call_args.kwargs["days"]
        self.assertTrue(sent_days)
        self.assertNotIn("min_stay", sent_days[0])

    @patch("pricing_engine.push_pipeline.fetch_bookings_for_window")
    @patch("pricing_engine.push_pipeline.PricingClient")
    @patch("pricing_engine.push_pipeline.EngineConfig.from_env")
    @patch("pricing_engine.push_pipeline.PropertyConfigStore")
    def test_diff_with_string_live_price_pushes_without_crash(
        self, mock_store_cls, mock_ecfg_from_env, mock_pc_cls, mock_fetch_bk
    ):
        t = _today()
        today_str = t.isoformat()
        config = _make_merged_config(booking_window_days=1)
        mock_store = MagicMock()
        mock_store.merge_with_env_defaults.return_value = config
        mock_store_cls.return_value = mock_store

        mock_ecfg = MagicMock()
        mock_ecfg.igms_access_token = "fake-token"
        mock_ecfg_from_env.return_value = mock_ecfg

        # Live price as string should be safely handled.
        mock_client = MagicMock()
        mock_client.access_token = "fake-token"
        mock_client.get_calendar.return_value = [
            _make_calendar_entry(today_str, price="100.0")
        ]
        mock_result = MagicMock()
        mock_result.status_code = 200
        mock_client.set_calendar_batch.return_value = mock_result
        mock_pc_cls.return_value = mock_client
        mock_fetch_bk.return_value = []

        with patch("pricing_engine.push_pipeline.PricingEngine") as mock_eng_cls:
            mock_engine = MagicMock()
            mock_eng_cls.return_value = mock_engine

            from pricing_engine.engine import DatePrice
            mock_dp = DatePrice(
                date=today_str,
                property_uid="test-prop",
                final_price=150.0,
                strategy_prices={},
                confidence=0.9,
                all_factors={},
            )
            mock_engine.compute_range.return_value = [mock_dp]
            mock_engine.compute_availability.return_value = MagicMock(
                is_available=True,
                blocked_reason=None,
            )

            result = run_push_pipeline(PushPipelineRequest(property_uid="test-prop"))

        self.assertTrue(result.success)
        self.assertGreaterEqual(result.price_updates_sent, 1)
        mock_client.set_calendar_batch.assert_called_once()
        sent_days = mock_client.set_calendar_batch.call_args.kwargs["days"]
        self.assertTrue(sent_days)
        self.assertNotIn("min_stay", sent_days[0])

    # ── test 5c: unavailable date -> availability block push ─────────────

    @patch("pricing_engine.push_pipeline.fetch_bookings_for_window")
    @patch("pricing_engine.push_pipeline.PricingClient")
    @patch("pricing_engine.push_pipeline.EngineConfig.from_env")
    @patch("pricing_engine.push_pipeline.PropertyConfigStore")
    def test_unavailable_date_pushes_block_not_min_stay(
        self, mock_store_cls, mock_ecfg_from_env, mock_pc_cls, mock_fetch_bk
    ):
        t = _today()
        today_str = t.isoformat()
        config = _make_merged_config(booking_window_days=1)
        mock_store = MagicMock()
        mock_store.merge_with_env_defaults.return_value = config
        mock_store_cls.return_value = mock_store

        mock_ecfg = MagicMock()
        mock_ecfg.igms_access_token = "fake-token"
        mock_ecfg_from_env.return_value = mock_ecfg

        # Live availability unknown (None) should still be block-eligible.
        mock_client = MagicMock()
        mock_client.access_token = "fake-token"
        mock_client.get_calendar.return_value = [
            {
                "date": today_str,
                "price": 150.0,
            }
        ]
        mock_batch_result = MagicMock()
        mock_batch_result.status_code = 200
        mock_client.set_calendar_batch.return_value = mock_batch_result
        mock_avail_result = MagicMock()
        mock_avail_result.status_code = 200
        mock_client.set_property_availability.return_value = mock_avail_result
        mock_pc_cls.return_value = mock_client
        mock_fetch_bk.return_value = []

        with patch("pricing_engine.push_pipeline.PricingEngine") as mock_eng_cls:
            mock_engine = MagicMock()
            mock_eng_cls.return_value = mock_engine

            from pricing_engine.engine import DatePrice
            mock_dp = DatePrice(
                date=today_str,
                property_uid="test-prop",
                final_price=150.0,
                strategy_prices={},
                confidence=0.9,
                all_factors={},
            )
            mock_engine.compute_range.return_value = [mock_dp]
            mock_engine.compute_availability.return_value = MagicMock(
                is_available=False,
                blocked_reason="day_before_checkin_blocked",
            )

            result = run_push_pipeline(PushPipelineRequest(property_uid="test-prop"))

        self.assertEqual(result.price_updates_sent, 0)
        self.assertGreaterEqual(result.availability_updates_sent, 1)
        # Current pipeline blocks via set_calendar_batch (the v2
        # set_property_availability endpoint lacks scope for our token).
        mock_client.set_property_availability.assert_not_called()
        sent = mock_client.set_calendar_batch.call_args
        self.assertIsNotNone(sent)
        days = sent.kwargs.get("days", [])
        self.assertTrue(any(d.get("is_available") is False for d in days))

    # ── test 6: dry-run never writes ──────────────────────────────────────

    @patch("pricing_engine.push_pipeline.fetch_bookings_for_window")
    @patch("pricing_engine.push_pipeline.PricingClient")
    @patch("pricing_engine.push_pipeline.EngineConfig.from_env")
    @patch("pricing_engine.push_pipeline.PropertyConfigStore")
    def test_dry_run_never_writes(
        self, mock_store_cls, mock_ecfg_from_env, mock_pc_cls, mock_fetch_bk
    ):
        today_str = _today().isoformat()
        config = _make_merged_config(booking_window_days=1, base_price=200.0)

        mock_store = MagicMock()
        mock_store.merge_with_env_defaults.return_value = config
        mock_store_cls.return_value = mock_store

        mock_ecfg = MagicMock()
        mock_ecfg.igms_access_token = "fake-token"
        mock_ecfg_from_env.return_value = mock_ecfg

        mock_client = MagicMock()
        mock_client.access_token = "fake-token"
        mock_client.get_calendar.return_value = [
            _make_calendar_entry(today_str, price=100.0)
        ]
        mock_pc_cls.return_value = mock_client
        mock_fetch_bk.return_value = []

        result = run_push_pipeline(
            PushPipelineRequest(property_uid="test-prop", dry_run=True)
        )
        self.assertTrue(result.success)
        mock_client.set_calendar_batch.assert_not_called()
        mock_client.set_property_availability.assert_not_called()

    # ── test 7: contiguous blocks are grouped into availability ranges ─────

    @patch("pricing_engine.push_pipeline.fetch_bookings_for_window")
    @patch("pricing_engine.push_pipeline.PricingClient")
    @patch("pricing_engine.push_pipeline.EngineConfig.from_env")
    @patch("pricing_engine.push_pipeline.PropertyConfigStore")
    def test_pipeline_groups_contiguous_block_ranges(
        self, mock_store_cls, mock_ecfg_from_env, mock_pc_cls, mock_fetch_bk
    ):
        t = _today()
        d1 = t.isoformat()
        d2 = (t + timedelta(days=1)).isoformat()
        d3 = (t + timedelta(days=3)).isoformat()

        config = _make_merged_config(booking_window_days=1)
        mock_store = MagicMock()
        mock_store.merge_with_env_defaults.return_value = config
        mock_store_cls.return_value = mock_store

        mock_ecfg = MagicMock()
        mock_ecfg.igms_access_token = "fake-token"
        mock_ecfg_from_env.return_value = mock_ecfg

        mock_client = MagicMock()
        mock_client.access_token = "fake-token"
        mock_client.get_calendar.return_value = [
            _make_calendar_entry(d1, price=100.0),
            _make_calendar_entry(d2, price=100.0),
            _make_calendar_entry(d3, price=100.0),
        ]
        mock_avail_result = MagicMock()
        mock_avail_result.status_code = 200
        mock_client.set_property_availability.return_value = mock_avail_result
        mock_pc_cls.return_value = mock_client
        mock_fetch_bk.return_value = []

        with patch("pricing_engine.push_pipeline.PricingEngine") as mock_eng_cls:
            mock_engine = MagicMock()
            mock_eng_cls.return_value = mock_engine
            from pricing_engine.engine import DatePrice

            mock_engine.compute_range.return_value = [
                DatePrice(date=d1, property_uid="test-prop", final_price=150.0, strategy_prices={}, confidence=0.9, all_factors={}),
                DatePrice(date=d2, property_uid="test-prop", final_price=150.0, strategy_prices={}, confidence=0.9, all_factors={}),
                DatePrice(date=d3, property_uid="test-prop", final_price=150.0, strategy_prices={}, confidence=0.9, all_factors={}),
            ]
            mock_engine.compute_availability.return_value = MagicMock(
                is_available=False,
                blocked_reason="day_before_checkin_blocked",
            )

            result = run_push_pipeline(PushPipelineRequest(property_uid="test-prop"))

        self.assertEqual(result.availability_updates_sent, 3)
        # Availability blocks go through set_calendar_batch with is_available:
        # False (chunked, one call for all 3 in this case).
        mock_client.set_property_availability.assert_not_called()
        sent = mock_client.set_calendar_batch.call_args
        self.assertIsNotNone(sent)
        days = sent.kwargs.get("days", [])
        block_days = [d for d in days if d.get("is_available") is False]
        self.assertGreaterEqual(len(block_days), 1)

    # ── test 8: error during set_calendar_batch ──────────────────────────

    @patch("pricing_engine.push_pipeline.fetch_bookings_for_window")
    @patch("pricing_engine.push_pipeline.PricingClient")
    @patch("pricing_engine.push_pipeline.EngineConfig.from_env")
    @patch("pricing_engine.push_pipeline.PropertyConfigStore")
    def test_set_calendar_batch_error_reported(
        self, mock_store_cls, mock_ecfg_from_env, mock_pc_cls, mock_fetch_bk
    ):
        today_str = _today().isoformat()
        config = _make_merged_config(booking_window_days=1, base_price=200.0)

        mock_store = MagicMock()
        mock_store.merge_with_env_defaults.return_value = config
        mock_store_cls.return_value = mock_store

        mock_ecfg = MagicMock()
        mock_ecfg.igms_access_token = "fake-token"
        mock_ecfg_from_env.return_value = mock_ecfg

        mock_client = MagicMock()
        mock_client.access_token = "fake-token"
        mock_client.get_calendar.return_value = [
            _make_calendar_entry(today_str, price=100.0)
        ]
        mock_result = MagicMock()
        mock_result.status_code = 500
        mock_result.payload = "Internal error"
        mock_client.set_calendar_batch.return_value = mock_result
        mock_pc_cls.return_value = mock_client
        mock_fetch_bk.return_value = []

        result = run_push_pipeline(PushPipelineRequest(property_uid="test-prop"))
        self.assertFalse(result.success)
        self.assertTrue(any("500" in e for e in result.errors))

    # ── test 9: exception during set_calendar_batch ──────────────────────

    @patch("pricing_engine.push_pipeline.fetch_bookings_for_window")
    @patch("pricing_engine.push_pipeline.PricingClient")
    @patch("pricing_engine.push_pipeline.EngineConfig.from_env")
    @patch("pricing_engine.push_pipeline.PropertyConfigStore")
    def test_set_calendar_batch_exception_reported(
        self, mock_store_cls, mock_ecfg_from_env, mock_pc_cls, mock_fetch_bk
    ):
        today_str = _today().isoformat()
        config = _make_merged_config(booking_window_days=1, base_price=200.0)

        mock_store = MagicMock()
        mock_store.merge_with_env_defaults.return_value = config
        mock_store_cls.return_value = mock_store

        mock_ecfg = MagicMock()
        mock_ecfg.igms_access_token = "fake-token"
        mock_ecfg_from_env.return_value = mock_ecfg

        mock_client = MagicMock()
        mock_client.access_token = "fake-token"
        mock_client.get_calendar.return_value = [
            _make_calendar_entry(today_str, price=100.0)
        ]
        mock_client.set_calendar_batch.side_effect = Exception("timeout")
        mock_pc_cls.return_value = mock_client
        mock_fetch_bk.return_value = []

        result = run_push_pipeline(PushPipelineRequest(property_uid="test-prop"))
        self.assertFalse(result.success)
        self.assertIn("timeout", result.errors[0])

    # ── test 10: calendar fetch failure ──────────────────────────────────

    @patch("pricing_engine.push_pipeline.fetch_bookings_for_window")
    @patch("pricing_engine.push_pipeline.PricingClient")
    @patch("pricing_engine.push_pipeline.EngineConfig.from_env")
    @patch("pricing_engine.push_pipeline.PropertyConfigStore")
    def test_calendar_fetch_failure_returns_error(
        self, mock_store_cls, mock_ecfg_from_env, mock_pc_cls, mock_fetch_bk
    ):
        config = _make_merged_config()
        mock_store = MagicMock()
        mock_store.merge_with_env_defaults.return_value = config
        mock_store_cls.return_value = mock_store

        mock_ecfg = MagicMock()
        mock_ecfg.igms_access_token = "fake-token"
        mock_ecfg_from_env.return_value = mock_ecfg

        mock_client = MagicMock()
        mock_client.access_token = "fake-token"
        mock_client.get_calendar.side_effect = Exception("connection refused")
        mock_pc_cls.return_value = mock_client
        mock_fetch_bk.return_value = []

        result = run_push_pipeline(PushPipelineRequest(property_uid="test-prop"))
        self.assertFalse(result.success)
        self.assertIn("connection refused", result.errors[0])

    # ── test 11: missing property config ─────────────────────────────────

    @patch("pricing_engine.push_pipeline.PropertyConfigStore")
    def test_missing_config_returns_error(self, mock_store_cls):
        mock_store = MagicMock()
        mock_store.load.return_value = {}
        mock_store_cls.return_value = mock_store

        result = run_push_pipeline(PushPipelineRequest(property_uid="no-such-prop"))
        self.assertFalse(result.success)
        self.assertTrue(any("not found" in e for e in result.errors))
