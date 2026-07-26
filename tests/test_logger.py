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


class DummyWindow:
    def __init__(self):
        self.evaluated = []

    def evaluate_js(self, js: str):
        self.evaluated.append(js)


def test_api_push_log_batching():
    """Api buffers log lines and flushes in batches to PyWebView evaluate_js."""
    import main

    api = main.Api()
    dummy_win = DummyWindow()
    api.set_window(dummy_win)

    api.push_log("Log line 1")
    api.push_log("Log line 2")

    assert api._log_queue.qsize() == 2

    api.flush_logs()
    assert len(dummy_win.evaluated) == 1
    assert "Log line 1" in dummy_win.evaluated[0]
    assert "Log line 2" in dummy_win.evaluated[0]
    assert "appendLogBatch" in dummy_win.evaluated[0]


def test_api_push_log_immediate_critical_error():
    """Critical error logs flush immediately."""
    import main

    api = main.Api()
    dummy_win = DummyWindow()
    api.set_window(dummy_win)

    api.push_log("Critical error occurred!", immediate=False)
    assert len(dummy_win.evaluated) == 1
    assert "Critical error occurred!" in dummy_win.evaluated[0]
