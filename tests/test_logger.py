import os
import tempfile
from core import logger


def test_logger_persists_messages(monkeypatch):
    """Logger reuses file handler and writes lines correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_log = os.path.join(tmpdir, "test.log")
        monkeypatch.setattr(logger, "LOG_FILE", tmp_log)

        log_inst = logger.Logger()
        log_inst.log("Test message 1")
        log_inst.log("Test message 2")

        with open(tmp_log, "r", encoding="utf-8") as f:
            lines = f.readlines()

        assert len(lines) == 2
        assert "Test message 1" in lines[0]
        assert "Test message 2" in lines[1]

        log_inst.close()
        assert log_inst._handler is None


def test_logger_file_rotation(monkeypatch):
    """Logger rotates files when MAX_LOG_SIZE is reached."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_log = os.path.join(tmpdir, "test.log")
        monkeypatch.setattr(logger, "LOG_FILE", tmp_log)
        monkeypatch.setattr(logger, "MAX_LOG_SIZE", 50)
        monkeypatch.setattr(logger, "BACKUP_COUNT", 2)

        log_inst = logger.Logger()
        for i in range(10):
            log_inst.log(f"Message number {i} with long text to trigger file rotation")

        log_inst.close()

        # Verify that the main log file and the rotated file exist
        assert os.path.exists(tmp_log)
        assert os.path.exists(f"{tmp_log}.1")

