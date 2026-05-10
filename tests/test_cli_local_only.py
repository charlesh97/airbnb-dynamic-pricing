"""Tests for CLI local-only property iteration."""
import sys
from unittest.mock import MagicMock, patch

sys.modules['igms_wrapper'] = MagicMock()
sys.modules['igms_wrapper.client'] = MagicMock()
sys.modules['holidays'] = MagicMock()

from src.pricing_engine.cli import cmd_status, cmd_run, cmd_push
from src.pricing_engine.config_store import PropertyConfigStore

def test_status_iterates_local_only_properties(tmp_path, monkeypatch, capsys):
    """cmd_status should iterate only local JSON property UIDs."""
    store = PropertyConfigStore(config_dir=str(tmp_path))
    store.save("uid1", {"property_uid": "uid1", "name": "Local One"})
    store.save("uid2", {"property_uid": "uid2", "name": "Local Two"})
    
    mock_config = MagicMock()
    mock_config.pricing_window_days = 30
    
    with patch('src.pricing_engine.cli.PropertyConfigStore', return_value=store):
        with patch('src.pricing_engine.cli.EngineConfig.from_env', return_value=mock_config):
            with patch('src.pricing_engine.cli.PricingClient') as mock_client_class:
                mock_client = MagicMock()
                mock_client_class.from_env.return_value = mock_client
                mock_client.get_all_properties.return_value = []
                
                args = MagicMock()
                args.log_level = "ERROR"
                args.env = ".env"
                cmd_status(args)
                
                mock_client.get_all_properties.assert_not_called()


def test_run_iterates_local_only_properties(tmp_path, monkeypatch):
    """cmd_run should iterate only local JSON property UIDs."""
    store = PropertyConfigStore(config_dir=str(tmp_path))
    store.save("local1", {"property_uid": "local1", "name": "Property 1"})
    
    mock_config = MagicMock()
    mock_config.pricing_window_days = 30
    
    with patch('src.pricing_engine.cli.PropertyConfigStore', return_value=store):
        with patch('src.pricing_engine.cli.EngineConfig.from_env', return_value=mock_config):
            with patch('src.pricing_engine.cli.PricingClient') as mock_client_class:
                mock_client = MagicMock()
                mock_client_class.from_env.return_value = mock_client
                
                args = MagicMock()
                args.log_level = "ERROR"
                args.env = ".env"
                cmd_run(args)
                
                mock_client.get_all_properties.assert_not_called()


def test_push_iterates_local_only_properties(tmp_path, monkeypatch):
    """cmd_push should iterate only local JSON property UIDs."""
    store = PropertyConfigStore(config_dir=str(tmp_path))
    store.save("prop1", {"property_uid": "prop1", "name": "Local Property"})
    
    mock_config = MagicMock()
    mock_config.pricing_window_days = 30
    
    with patch('src.pricing_engine.cli.PropertyConfigStore', return_value=store):
        with patch('src.pricing_engine.cli.EngineConfig.from_env', return_value=mock_config):
            with patch('src.pricing_engine.cli.PricingClient') as mock_client_class:
                mock_client = MagicMock()
                mock_client_class.from_env.return_value = mock_client
                
                args = MagicMock()
                args.log_level = "ERROR"
                args.env = ".env"
                args.dry_run = False
                cmd_push(args)
                
                mock_client.get_all_properties.assert_not_called()