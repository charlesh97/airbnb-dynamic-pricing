"""Tests for dashboard routes."""
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.routes.calendar import router
from fastapi import FastAPI

app = FastAPI()
app.include_router(router)
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