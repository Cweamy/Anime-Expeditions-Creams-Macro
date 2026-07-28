import os
import json
import tempfile
import shutil
from unittest import mock
import pytest
from core.diagnostics import redact_sensitive_info, save_failure_snapshot, FailureReport, FailureCategory, RecoveryAction
from core import constants

def test_redact_sensitive_info():
    # Test Windows paths
    text_with_path = "Error in C:\\Users\\Bryan\\AppData\\Local\\Temp\\test.log"
    redacted = redact_sensitive_info(text_with_path)
    assert "C:\\Users\\<REDACTED>\\AppData" in redacted
    assert "Bryan" not in redacted

    text_with_forward_slash = "Error in C:/Users/JohnDoe/AppData/Local/"
    redacted_fwd = redact_sensitive_info(text_with_forward_slash)
    assert "C:/Users/<REDACTED>/AppData" in redacted_fwd
    assert "JohnDoe" not in redacted_fwd

    # Test Discord webhook
    text_with_webhook = "Posting to https://discord.com/api/webhooks/1234567890/abcde-fghij"
    redacted_webhook = redact_sensitive_info(text_with_webhook)
    assert "https://discord.com/api/webhooks/<REDACTED>" in redacted_webhook
    assert "1234567890" not in redacted_webhook

    # Test multiple occurrences
    multi_text = "Path C:\\Users\\User1\\x and url https://discord.com/api/webhooks/123/abc"
    redacted_multi = redact_sensitive_info(multi_text)
    assert "C:\\Users\\<REDACTED>\\x" in redacted_multi
    assert "https://discord.com/api/webhooks/<REDACTED>" in redacted_multi

def test_save_failure_snapshot(monkeypatch):
    temp_dir = tempfile.mkdtemp()
    monkeypatch.setattr(constants, "APP_DIR", temp_dir)

    try:
        report = FailureReport(
            code="ERR01",
            category=FailureCategory.INTERNAL,
            phase="test",
            retryable=False,
            recovery_action=RecoveryAction.STOP_TASK,
            user_message="Test",
            user_action="None"
        )
        
        # Test basic snapshot
        snapshot_dir = save_failure_snapshot(report, log_tail="Test C:\\Users\\Bryan\\test.log")
        
        assert os.path.exists(snapshot_dir)
        assert os.path.exists(os.path.join(snapshot_dir, "failure.json"))
        assert os.path.exists(os.path.join(snapshot_dir, "recent_logs.txt"))
        
        with open(os.path.join(snapshot_dir, "recent_logs.txt"), "r") as f:
            logs = f.read()
            assert "C:\\Users\\<REDACTED>\\test.log" in logs
            
        # Test retention (max 10)
        import time
        for i in range(15):
            rep = FailureReport(
                code=f"ERR{i}",
                category=FailureCategory.INTERNAL,
                phase="test",
                retryable=False,
                recovery_action=RecoveryAction.STOP_TASK,
                user_message="Test",
                user_action="None"
            )
            save_failure_snapshot(rep)
            time.sleep(0.01) # to ensure different timestamps
            
        failures_dir = os.path.join(temp_dir, "debug", "failures")
        snapshots = os.listdir(failures_dir)
        assert len(snapshots) == 10
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

@mock.patch("main.os.path.expanduser")
def test_export_failure_report(mock_expanduser, monkeypatch):
    import main
    
    # Mock dependencies
    api = main.Api()
    api._window = mock.MagicMock()
    api._window.create_file_dialog.return_value = ["mock_bundle.zip"]
    mock_expanduser.return_value = "mock_dir"
    
    # Mock run_health_check
    api.run_health_check = mock.MagicMock(return_value={})
    
    temp_dir = tempfile.mkdtemp()
    monkeypatch.setattr(constants, "APP_DIR", temp_dir)
    
    # Create mock debug log and config
    with open(os.path.join(temp_dir, "debug.log"), "w", encoding="utf-8") as f:
        f.write("Log trace with C:\\Users\\Bryan\\test.log")
        
    def mock_cfg_load():
        return {"webhook_url": "https://discord.com/api/webhooks/123/abc", "other_path": "C:\\Users\\Admin\\x"}
        
    monkeypatch.setattr("core.settings.load", mock_cfg_load)
    monkeypatch.setattr("main.cfg.load", mock_cfg_load)
    
    try:
        # Avoid file writing outside temp
        import zipfile
        mock_zip = mock.MagicMock()
        mock_zip_instance = mock.MagicMock()
        mock_zip.return_value.__enter__.return_value = mock_zip_instance
        
        with mock.patch("zipfile.ZipFile", mock_zip):
            api.export_failure_report()
            
            # Verify zip writes
            writes = mock_zip_instance.writestr.call_args_list
            
            log_tail_written = False
            settings_written = False
            
            for call in writes:
                args, kwargs = call
                if args[0] == "log_tail.txt":
                    log_tail_written = True
                    assert "C:\\Users\\<REDACTED>\\test.log" in args[1]
                elif args[0] == "settings.json":
                    settings_written = True
                    settings_data = json.loads(args[1])
                    assert settings_data["webhook_url"] == "<redacted>"
                    assert settings_data["other_path"] == "C:\\Users\\<REDACTED>\\x"
                    
            assert log_tail_written
            assert settings_written
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
