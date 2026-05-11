"""Tests for CLI local-only property iteration."""
import sys
from unittest.mock import MagicMock, patch

sys.modules['igms_wrapper'] = MagicMock()
sys.modules['igms_wrapper.client'] = MagicMock()
sys.modules['holidays'] = MagicMock()
sys.modules['rich'] = MagicMock()
sys.modules['rich.console'] = MagicMock()
sys.modules['rich.table'] = MagicMock()
sys.modules['rich.markup'] = MagicMock()

from src.pricing_engine.cli import cmd_status, cmd_run, cmd_dry_run, cmd_push
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
    """cmd_push should iterate only local JSON property UIDs and delegate to pipeline."""
    store = PropertyConfigStore(config_dir=str(tmp_path))
    store.save("prop1", {"property_uid": "prop1", "name": "Local Property"})

    mock_result = MagicMock()
    mock_result.success = True
    mock_result.from_date = "2026-01-01"
    mock_result.to_date = "2026-06-01"
    mock_result.base_booking_window_days = 120
    mock_result.effective_window_days = 180
    mock_result.dates_evaluated = 0
    mock_result.price_updates_sent = 0
    mock_result.availability_updates_sent = 0
    mock_result.dates_skipped_booked = 0
    mock_result.dates_skipped_live_blocked = 0
    mock_result.dates_skipped_outside_window = 0
    mock_result.skipped_live_blocked_dates = []
    mock_result.errors = []
    mock_result.warnings = []

    with patch('src.pricing_engine.cli.PropertyConfigStore', return_value=store):
        with patch('src.pricing_engine.cli.run_push_pipeline', return_value=mock_result) as mock_pipeline:
            args = MagicMock()
            args.log_level = "ERROR"
            args.env = ".env"
            args.dry_run = False
            cmd_push(args)

            mock_pipeline.assert_called_once()
            call_args = mock_pipeline.call_args[0][0]
            assert call_args.property_uid == "prop1"


def test_push_dry_run_sets_pipeline_request_flag(tmp_path, monkeypatch):
    """cmd_push should pass dry_run=True into PushPipelineRequest."""
    store = PropertyConfigStore(config_dir=str(tmp_path))
    store.save("prop1", {"property_uid": "prop1", "name": "Local Property"})

    mock_result = MagicMock()
    mock_result.success = True
    mock_result.from_date = "2026-01-01"
    mock_result.to_date = "2026-06-01"
    mock_result.base_booking_window_days = 120
    mock_result.effective_window_days = 180
    mock_result.dates_evaluated = 0
    mock_result.price_updates_sent = 0
    mock_result.availability_updates_sent = 0
    mock_result.dates_skipped_booked = 0
    mock_result.dates_skipped_live_blocked = 0
    mock_result.dates_skipped_outside_window = 0
    mock_result.skipped_live_blocked_dates = []
    mock_result.errors = []
    mock_result.warnings = []

    with patch('src.pricing_engine.cli.PropertyConfigStore', return_value=store):
        with patch('src.pricing_engine.cli.run_push_pipeline', return_value=mock_result) as mock_pipeline:
            args = MagicMock()
            args.log_level = "ERROR"
            args.env = ".env"
            args.dry_run = True
            cmd_push(args)

            mock_pipeline.assert_called_once()
            call_args = mock_pipeline.call_args[0][0]
            assert call_args.property_uid == "prop1"
            assert call_args.dry_run is True


def test_dry_run_alias_delegates_to_cmd_run():
    """`dry-run` subcommand should delegate to cmd_run."""
    args = MagicMock()
    with patch('src.pricing_engine.cli.cmd_run') as mock_run:
        cmd_dry_run(args)
        mock_run.assert_called_once_with(args)
