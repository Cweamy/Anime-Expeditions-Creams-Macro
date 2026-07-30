"""Log-batching in Api (ported from PR #34 onto the log_view.js architecture).

Exercises _flush_log_queue and the critical-line detection directly, building
the Api via __new__ so the heavy __init__ (Mouse/Keyboard/docker/threads) never
runs -- only the few attributes the flush path actually touches are set.
"""
import queue
import threading

import main


def _make_api():
    api = main.Api.__new__(main.Api)
    api._log_queue = queue.Queue()
    api._log_flush_lock = threading.Lock()
    api._window = None
    api._log_window = None
    return api


class _Win:
    def __init__(self):
        self.calls = []

    def evaluate_js(self, js):
        self.calls.append(js)


def test_flush_batches_queued_lines_into_one_ipc_call():
    api = _make_api()
    win = _Win()
    api._window = win
    for i in range(5):
        api._log_queue.put(f"line {i}")

    api._flush_log_queue()

    assert len(win.calls) == 1, "a burst of lines must cost a single evaluate_js call"
    assert "appendLogBatch" in win.calls[0]
    assert "line 0" in win.calls[0] and "line 4" in win.calls[0]
    assert api._log_queue.empty()


def test_flush_dispatches_to_both_windows():
    api = _make_api()
    main_win, log_win = _Win(), _Win()
    api._window, api._log_window = main_win, log_win
    api._log_queue.put("hello")

    api._flush_log_queue()

    assert len(main_win.calls) == 1
    assert len(log_win.calls) == 1


def test_flush_with_no_window_drops_batch_without_error():
    api = _make_api()
    api._log_queue.put("orphan")

    api._flush_log_queue()  # no window attached -- must not raise

    assert api._log_queue.empty()


def test_empty_queue_flush_is_a_noop():
    api = _make_api()
    win = _Win()
    api._window = win

    api._flush_log_queue()

    assert win.calls == []


def test_clear_logs_calls_view_only_helper_without_reentering_api():
    api = _make_api()
    main_win, log_win = _Win(), _Win()
    api._window, api._log_window = main_win, log_win
    api._log_history = ["old"]
    api._log_queue.put("queued")

    api.clear_logs()

    assert api._log_history == []
    assert api._log_queue.empty()
    assert main_win.calls == [
        "window.clearLogView && window.clearLogView()"
    ]
    assert log_win.calls == [
        "window.clearLogView && window.clearLogView()"
    ]
    assert all("clearLogs()" not in call for call in main_win.calls)

    api._log_queue.put("new after clear")
    api._flush_log_queue()

    assert len(main_win.calls) == 2
    assert "appendLogBatch" in main_win.calls[-1]
    assert "new after clear" in main_win.calls[-1]
    assert len(log_win.calls) == 2


def test_is_critical_log_detection():
    assert main._is_critical_log("Something failed badly")
    assert main._is_critical_log("[Update] ERROR: boom")
    assert main._is_critical_log("Unhandled exception")
    assert not main._is_critical_log("all good here")
    assert not main._is_critical_log("[Macro] placed unit")
