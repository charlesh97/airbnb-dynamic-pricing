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
from dashboard.engine_proxy import (
    PropertyConfigStore,
    _date_price_to_dict,
    _has_effective_price_change,
    get_properties,
)
from pricing_engine.engine import DatePrice


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


def test_effective_change_uses_cent_rounding():
    assert _has_effective_price_change(100.01, 100.00) is True
    assert _has_effective_price_change(100.00, 100.00) is False
    assert _has_effective_price_change(100.004, 100.00) is False


def test_date_price_to_dict_marks_only_exact_holiday_day():
    dp_buffer = DatePrice(
        date="2026-12-24",
        property_uid="uid1",
        final_price=120.0,
        strategy_prices={},
        confidence=0.9,
        all_factors={
            "event": {
                "is_holiday": True,
                "holiday_name": "Christmas Day",
                "holiday_source": "auto",
                "holiday_buffer_applied": True,
            }
        },
    )
    dp_exact = DatePrice(
        date="2026-12-25",
        property_uid="uid1",
        final_price=130.0,
        strategy_prices={},
        confidence=0.9,
        all_factors={
            "event": {
                "is_holiday": True,
                "holiday_name": "Christmas Day",
                "holiday_source": "auto",
                "holiday_buffer_applied": False,
            }
        },
    )
    avail = MagicMock()
    avail.is_available = True
    avail.blocked_reason = None

    out_buffer = _date_price_to_dict(dp_buffer, 120.0, avail, {})
    out_exact = _date_price_to_dict(dp_exact, 120.0, avail, {})

    assert out_buffer["is_holiday"] is False
    assert out_buffer["holiday_name"] == "Christmas Day"
    assert out_buffer["holiday_proximity"]["buffer_applied"] is True

    assert out_exact["is_holiday"] is True
    assert out_exact["holiday_name"] == "Christmas Day"
    assert out_exact["holiday_proximity"]["buffer_applied"] is False


def test_date_price_to_dict_uses_cent_threshold_for_has_proposed_change():
    avail = MagicMock()
    avail.is_available = True
    avail.blocked_reason = None

    dp_no_change = DatePrice(
        date="2026-07-01",
        property_uid="uid1",
        final_price=100.004,
        strategy_prices={},
        confidence=0.9,
        all_factors={},
    )
    dp_change = DatePrice(
        date="2026-07-02",
        property_uid="uid1",
        final_price=100.01,
        strategy_prices={},
        confidence=0.9,
        all_factors={},
    )

    out_no_change = _date_price_to_dict(dp_no_change, 100.0, avail, {})
    out_change = _date_price_to_dict(dp_change, 100.0, avail, {})

    assert out_no_change["has_proposed_change"] is False
    assert out_change["has_proposed_change"] is True
