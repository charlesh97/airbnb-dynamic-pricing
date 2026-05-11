"""Integration tests — require live IGMS_ACCESS_TOKEN.

These tests are skipped if IGMS_ACCESS_TOKEN is not set.
Run with: IGMS_ACCESS_TOKEN=your_token python -m pytest tests/test_integration.py -v
"""

import os
import pytest
import unittest


# Only run integration tests if token is present
TOKEN = os.getenv("IGMS_ACCESS_TOKEN", "")


@unittest.skipUnless(TOKEN, "IGMS_ACCESS_TOKEN not set — skipping live API tests")
class TestLiveAPI(unittest.TestCase):
    def setUp(self):
        from pricing_engine.client import PricingClient
        self.client = PricingClient.from_env()

    def test_properties_load(self):
        props = self.client.get_all_properties()
        self.assertGreater(len(props), 0)

    def test_calendar_read(self):
        """Calendar read confirmed working."""
        cal = self.client.get_calendar(
            "850410072530215128",
            "2026-05-04",
            "2026-05-10",
        )
        self.assertIn("data", cal)
        self.assertGreater(len(cal["data"]), 0)

    def test_pricing_write_endpoint(self):
        """Test the pricing write endpoint.

        This is the critical test — success means we have the right endpoint.
        Will fail with 400/401/404 if scope or endpoint is wrong.
        """
        result = self.client.update_calendar_price(
            listing_uid="6925833560458409984",
            property_uid="6925833560458409984",
            date="2099-01-01",  # Far future, safe to test
            price=9999.0,
        )
        # Expected: 200/201/204 = success
        # 400 = bad payload (may mean endpoint found but payload rejected)
        # 401 = auth issue (no pricing-management scope)
        # 404 = endpoint not found
        self.assertIn(
            result.status_code,
            (200, 201, 204),
            f"Calendar write failed: {result.status_code} — {result.payload}",
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
