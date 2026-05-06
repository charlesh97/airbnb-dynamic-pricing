"""Tests for WheelhouseFetcher."""

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from pricing_engine.wheelhouse_fetcher import WheelhouseFetcher


class TestWheelhouseFetcher(unittest.TestCase):
    def setUp(self):
        self.wh = WheelhouseFetcher(api_key="test-key")

    def test_check_coverage(self):
        with patch.object(self.wh.session, "get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"in_market": True, "market_name": "Palm Springs"}
            mock_get.return_value = mock_resp

            result = self.wh.check_coverage(
                latitude=33.8303,
                longitude=-116.5453,
                country="US",
                postal_code="92262",
            )
            self.assertTrue(result["in_market"])
            self.assertEqual(result["market_name"], "Palm Springs")
            mock_get.assert_called_once()
            call_args = mock_get.call_args
            self.assertEqual(call_args[0][0], f"{self.wh.base_url}in_market")

    def test_fetch_recommendations(self):
        sample_data = {
            "daily_recommendation": [
                {"date": "2026-06-01", "total_price": 250.0, "adr": 200.0, "occupancy": 0.65},
                {"date": "2026-06-02", "total_price": 260.0, "adr": 210.0, "occupancy": 0.70},
                {"date": "2026-07-15", "total_price": 320.0, "adr": 260.0, "occupancy": 0.80},
            ]
        }
        with patch.object(self.wh.session, "get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = sample_data
            mock_get.return_value = mock_resp

            from_date = datetime(2026, 6, 1)
            to_date = datetime(2026, 6, 30)

            recs = self.wh.fetch_recommendations(
                latitude=33.8303,
                longitude=-116.5453,
                bedrooms=2,
                baths=1.5,
                sleeps=4,
                from_date=from_date,
                to_date=to_date,
            )

            # Should filter to June only (June 1, June 2 — July 15 excluded)
            self.assertEqual(len(recs), 2)
            self.assertEqual(recs[0]["total_price"], 250.0)
            self.assertEqual(recs[1]["total_price"], 260.0)

    def test_build_market_rates(self):
        recs = [
            {"date": "2026-06-01", "total_price": 250.0},
            {"date": "2026-06-02", "total_price": 260.0},
            {"date_iso": "2026-07-01T00:00:00", "total_price": 300.0},
        ]
        rates = self.wh.build_market_rates(recs)
        self.assertEqual(rates["2026-06-01"], 250.0)
        self.assertEqual(rates["2026-06-02"], 260.0)
        self.assertEqual(rates["2026-07-01"], 300.0)

    def test_build_market_rates_with_timestamps(self):
        recs = [
            {"date_iso": "2026-06-01T12:00:00Z", "total_price": 250.0},
        ]
        rates = self.wh.build_market_rates(recs)
        self.assertEqual(rates["2026-06-01"], 250.0)


if __name__ == "__main__":
    unittest.main()