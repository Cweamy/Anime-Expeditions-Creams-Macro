import os
import tempfile
from core import logger


def test_logger_persists_messages(monkeypatch):
    """Logger reuses file handle and writes lines correctly."""
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
        assert log_inst._file is None
