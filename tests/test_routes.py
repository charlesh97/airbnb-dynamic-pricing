"""Tests for dashboard routes."""
from unittest.mock import MagicMock, patch
import pytest
pytest.importorskip("fastapi")
pytest.importorskip("fastapi.testclient")
from fastapi.testclient import TestClient

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.routes.calendar import router
from dashboard.routes.push import router as push_router
from fastapi import FastAPI

app = FastAPI()
app.include_router(router)
app.include_router(push_router)
client = TestClient(app)


class TempConfigDir:
    def __init__(self, tmp_path):
        self.dir = tmp_path / "configs"
        self.dir.mkdir()
        self.config_dir = self.dir
        self._data = {}

    def save(self, property_uid, config):
        self._data[property_uid] = config
        p = self.dir / f"{property_uid}.json"
        p.write_text('{"property_uid": "' + property_uid + '"}')

    def has(self, property_uid):
        return property_uid in self._data

    def list_properties(self):
        return list(self._data.keys())


@pytest.fixture
def tmp_config_dir(tmp_path):
    return TempConfigDir(tmp_path)


def test_discover_endpoint_returns_igms_list(monkeypatch, tmp_config_dir):
    """GET /api/properties/discover returns iGMS list with has_local_config flags."""
    mock_props = [
        {"property_uid": "p1", "name": "Prop One", "state": "CA"},
        {"property_uid": "p2", "name": "Prop Two", "state": "TX"},
    ]
    mock_client = MagicMock()
    mock_client.get_all_properties.return_value = mock_props

    with patch('dashboard.routes.calendar._get_pricing_client', return_value=mock_client):
        with patch('dashboard.engine_proxy._CONFIG_STORE', tmp_config_dir):
            tmp_config_dir.save("p1", {"property_uid": "p1", "name": "Local One", "state": "CA"})
            response = client.get("/api/properties/discover")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    p1 = next(x for x in data if x["property_uid"] == "p1")
    assert p1["has_local_config"] == True
    assert p1["name"] == "Prop One"
    p2 = next(x for x in data if x["property_uid"] == "p2")
    assert p2["has_local_config"] == False


def test_discover_no_file_writes(tmp_config_dir):
    """Discover endpoint does not create any files."""
    mock_props = [{"property_uid": "test1", "name": "Test One", "state": "NY"}]
    mock_client = MagicMock()
    mock_client.get_all_properties.return_value = mock_props

    before = set(tmp_config_dir.dir.glob("*.json"))

    with patch('dashboard.routes.calendar._get_pricing_client', return_value=mock_client):
        with patch('dashboard.engine_proxy._CONFIG_STORE', tmp_config_dir):
            response = client.get("/api/properties/discover")

    assert response.status_code == 200
    after = set(tmp_config_dir.dir.glob("*.json"))
    assert before == after


def test_add_property_creates_json(tmp_config_dir, monkeypatch):
    """POST /api/properties/add with new uid creates config file and returns created."""
    mock_props = [{"property_uid": "newuid", "name": "New Property", "state": "NY"}]
    mock_client = MagicMock()
    mock_client.get_all_properties.return_value = mock_props

    with patch('dashboard.routes.calendar._get_pricing_client', return_value=mock_client):
        with patch('dashboard.engine_proxy._CONFIG_STORE', tmp_config_dir):
            response = client.post("/api/properties/add", json={"property_uid": "newuid"})

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "created"
    assert data["property_uid"] == "newuid"
    assert data["name"] == "New Property"
    assert (tmp_config_dir.dir / "newuid.json").exists()


def test_add_property_existing_returns_exists(tmp_config_dir, monkeypatch):
    """POST /api/properties/add with existing uid returns {status: 'exists'}."""
    with patch('dashboard.engine_proxy._CONFIG_STORE', tmp_config_dir):
        tmp_config_dir.save("existing", {"property_uid": "existing", "name": "Existing"})
        response = client.post("/api/properties/add", json={"property_uid": "existing"})

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "exists"


def test_add_property_missing_uid_returns_error():
    """POST /api/properties/add with missing uid returns error."""
    response = client.post("/api/properties/add", json={})
    assert response.status_code == 200
    assert "error" in response.json()


def test_push_endpoint_delegates_and_returns_pipeline_fields():
    """POST /api/calendar/push delegates to pipeline, returns full schema."""
    mock_result = MagicMock()
    mock_result.success = True
    mock_result.from_date = "2026-05-10"
    mock_result.to_date = "2026-07-09"
    mock_result.base_booking_window_days = 120
    mock_result.effective_window_days = 180
    mock_result.dates_evaluated = 180
    mock_result.price_updates_sent = 5
    mock_result.availability_updates_sent = 2
    mock_result.dates_skipped_booked = 3
    mock_result.dates_skipped_live_blocked = 1
    mock_result.dates_skipped_outside_window = 0
    mock_result.skipped_live_blocked_dates = ["2026-05-15"]
    mock_result.errors = []
    mock_result.warnings = ["test warning"]

    with patch(
        "dashboard.routes.push.run_push_pipeline",
        return_value=mock_result,
    ) as mock_pipeline:
        response = client.post(
            "/api/calendar/push",
            json={"property_uid": "test-prop"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["price_updates_sent"] == 5
    assert data["availability_updates_sent"] == 2
    assert data["dates_skipped_booked"] == 3
    assert data["dates_skipped_live_blocked"] == 1
    assert data["skipped_live_blocked_dates"] == ["2026-05-15"]
    assert data["warnings"] == ["test warning"]
    assert data["effective_window_days"] == 180
    mock_pipeline.assert_called_once()


def _base_day(date_str: str) -> dict:
    return {
        "date": date_str,
        "final_price": 200.0,
        "current_airbnb_price": 190.0,
        "price_delta": 10.0,
        "price_delta_pct": 5.26,
        "match_status": "higher",
        "is_available": True,
        "blocked_reason": None,
        "confidence": 0.9,
        "is_holiday": False,
        "holiday_name": None,
        "holiday_proximity": None,
        "live_price_status": "ok",
        "has_proposed_change": True,
    }


def test_calendar_marks_booked_when_live_status_is_booked():
    """GET /api/calendar marks live booked statuses as booked even without bookings feed rows."""
    date_str = "2026-06-17"
    mock_client = MagicMock()
    mock_client.get_calendar.return_value = [{"date": date_str, "price": 155, "status": "booked"}]

    with patch("dashboard.routes.calendar._get_pricing_client", return_value=mock_client):
        with patch("dashboard.routes.calendar.compute_month", return_value=[_base_day(date_str)]):
            with patch("dashboard.routes.calendar._fetch_bookings_for_window", return_value=[]):
                response = client.get(f"/api/calendar/2026/6?property_uid=test-prop")

    assert response.status_code == 200
    day = response.json()["days"][0]
    assert day["is_available"] is False
    assert day["blocked_reason"] == "booked"
    assert day["has_proposed_change"] is False


def test_calendar_marks_booked_when_live_reason_mentions_guest():
    """GET /api/calendar maps guest/reservation reason hints to booked."""
    date_str = "2026-06-17"
    mock_client = MagicMock()
    mock_client.get_calendar.return_value = [{
        "date": date_str,
        "price": 155,
        "status": "unavailable",
        "reason": "Airbnb guest reservation",
    }]

    with patch("dashboard.routes.calendar._get_pricing_client", return_value=mock_client):
        with patch("dashboard.routes.calendar.compute_month", return_value=[_base_day(date_str)]):
            with patch("dashboard.routes.calendar._fetch_bookings_for_window", return_value=[]):
                response = client.get(f"/api/calendar/2026/6?property_uid=test-prop")

    assert response.status_code == 200
    day = response.json()["days"][0]
    assert day["blocked_reason"] == "booked"


def test_calendar_uses_strict_display_bookings_for_spans():
    """Booking spans should come from strict month fetch, not widened pricing fetch."""
    date_str = "2026-06-17"
    mock_client = MagicMock()
    mock_client.get_calendar.return_value = [{
        "date": date_str,
        "price": 207,
        "is_available": 0,
        "unavailable_reason": "Blocked by reservation",
    }]
    widened_rows = [{
        "booking_status": "accepted",
        "checkin": "2026-06-28",
        "checkout": "2026-07-07",
        "reservation_code": "HM359B52D9",
        "guest_name": "",
    }]
    strict_rows = [{
        "booking_status": "accepted",
        "checkin": "2026-06-17",
        "checkout": "2026-06-28",
        "reservation_code": "HMR32KZ2ZR",
        "guest_name": "",
    }]

    with patch("dashboard.routes.calendar._get_pricing_client", return_value=mock_client):
        with patch("dashboard.routes.calendar.compute_month", return_value=[_base_day(date_str)]):
            with patch("dashboard.routes.calendar._fetch_bookings_for_window", return_value=widened_rows):
                with patch("dashboard.routes.calendar._fetch_bookings_for_display_window", return_value=strict_rows):
                    response = client.get(f"/api/calendar/2026/6?property_uid=test-prop")

    assert response.status_code == 200
    data = response.json()
    spans = data["bookings"]
    assert len(spans) == 1
    assert spans[0]["reservation_code"] == "HMR32KZ2ZR"
    assert spans[0]["checkin"] == "2026-06-17"
    assert spans[0]["checkout"] == "2026-06-28"
