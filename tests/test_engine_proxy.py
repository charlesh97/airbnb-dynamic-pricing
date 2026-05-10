"""Tests for dashboard/engine_proxy.py"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


holidays_mock = MagicMock()
sys.modules['igms_wrapper'] = MagicMock()
sys.modules['igms_wrapper.client'] = MagicMock()
sys.modules['holidays'] = holidays_mock

import dashboard.engine_proxy
from dashboard.engine_proxy import PropertyConfigStore, get_properties


@pytest.fixture
def tmp_config_dir(tmp_path):
    """Provide a temporary config directory and patch _CONFIG_STORE to use it."""
    store = PropertyConfigStore(config_dir=str(tmp_path))
    orig = dashboard.engine_proxy._CONFIG_STORE
    dashboard.engine_proxy._CONFIG_STORE = store
    yield store
    dashboard.engine_proxy._CONFIG_STORE = orig


def test_get_properties_local_only(tmp_config_dir):
    """get_properties() returns only local JSON files, no iGMS calls."""
    (tmp_config_dir.config_dir / "uid1.json").write_text(json.dumps({
        "property_uid": "uid1", "name": "Alpha", "state": "CA"
    }))
    (tmp_config_dir.config_dir / "uid2.json").write_text(json.dumps({
        "property_uid": "uid2", "name": "Beta", "state": "VA"
    }))

    with patch.object(dashboard.engine_proxy, '_get_pricing_client') as mock_client:
        result = get_properties()
        mock_client.assert_not_called()

    assert len(result) == 2
    uids = {r["property_uid"] for r in result}
    assert uids == {"uid1", "uid2"}