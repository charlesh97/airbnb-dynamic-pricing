"""Tests for booking_adapter pagination and normalization."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_MODULE_PATH = Path(__file__).parent.parent / "src" / "pricing_engine" / "booking_adapter.py"
_SPEC = importlib.util.spec_from_file_location("booking_adapter_under_test", _MODULE_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
fetch_bookings_for_window = _MODULE.fetch_bookings_for_window


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get_bookings(self, page=1, **filters):
        self.calls.append((page, filters))
        return self.responses[page - 1]


def _booking(property_uid: str, code: str) -> dict:
    return {
        "property_uid": property_uid,
        "checkin": "2026-07-02",
        "checkout": "2026-07-05",
        "created_dttm": "2026-05-10T00:08:30",
        "booking_status": "accepted",
        "reservation_code": code,
    }


def test_fetch_bookings_follows_nested_meta_has_next_page():
    client = FakeClient(
        [
            {
                "data": [
                    _booking("prop-1", "PAGE1"),
                    _booking("other-prop", "OTHER"),
                ],
                "meta": {"page": 1, "has_next_page": True},
            },
            {
                "data": [
                    _booking("prop-1", "PAGE2"),
                ],
                "meta": {"page": 2, "has_next_page": False},
            },
        ]
    )

    bookings = fetch_bookings_for_window(
        client,
        "prop-1",
        "2026-06-01",
        "2026-12-28",
    )

    assert [b["reservation_code"] for b in bookings] == ["PAGE1", "PAGE2"]
    assert [page for page, _ in client.calls] == [1, 2]
