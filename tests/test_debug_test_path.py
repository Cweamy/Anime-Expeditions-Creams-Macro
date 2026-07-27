"""Settings > Debug > "Test Walking Path" button state.

runTestPath() hides Run and shows Stop when a replay starts. Only
stop_test_path() ever swapped them back, so a replay that ended on its own
left a Stop button offering to stop a walk that had already finished -- for
the rest of the session, since nothing else touches those inline styles.

debug_test_path's worker now signals the UI when it finishes, the same way the
Tesseract install already does for its own unknown-duration button.
"""
import threading
import time

import main


def _make_api(monkeypatch, events, recording=False):
    api = main.Api.__new__(main.Api)
    api._path_test_stop = None
    api.keyboard = object()
    api.game_hwnd = 1
    api.pushed_ui = []
    api.push_ui = api.pushed_ui.append
    api.push_log = lambda msg: None

    from core import paths, window as wm
    monkeypatch.setattr(paths, "is_recording", lambda: recording)
    monkeypatch.setattr(paths, "load_path", lambda name: {"events": events})
    monkeypatch.setattr(paths, "replay_events", lambda ev, kb, stop: None)
    monkeypatch.setattr(wm, "is_window", lambda h: True)
    monkeypatch.setattr(wm, "show_window", lambda h: None)
    monkeypatch.setattr(wm, "activate_window", lambda h: None)
    monkeypatch.setattr(main.wm, "is_window", lambda h: True, raising=False)
    monkeypatch.setattr(main.wm, "show_window", lambda h: None, raising=False)
    monkeypatch.setattr(main.wm, "activate_window", lambda h: None, raising=False)
    return api


def _wait_for(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_finished_replay_tells_the_ui_to_restore_the_run_button(monkeypatch):
    api = _make_api(monkeypatch, [{"t": 0.0, "key": "w", "state": "down"}])

    assert api.debug_test_path("Some Path")["ok"] is True
    assert _wait_for(lambda: "testPathFinished" in api.pushed_ui), (
        "a replay that ends on its own must signal the UI, or Stop stays up forever"
    )


def test_a_crashing_replay_still_restores_the_run_button(monkeypatch):
    """finally:, not just the happy path -- a replay that raises would
    otherwise leave the button stuck in exactly the same way."""
    api = _make_api(monkeypatch, [{"t": 0.0, "key": "w", "state": "down"}])
    from core import paths

    def boom(ev, kb, stop):
        raise RuntimeError("simulated replay failure")

    monkeypatch.setattr(paths, "replay_events", boom)

    api.debug_test_path("Some Path")
    assert _wait_for(lambda: "testPathFinished" in api.pushed_ui)


def test_a_refused_start_does_not_signal_the_ui(monkeypatch):
    """No thread was started and the button was never swapped, so there is
    nothing to restore -- signalling here would be noise."""
    api = _make_api(monkeypatch, [])          # empty path -> refused
    result = api.debug_test_path("Some Path")
    assert result["ok"] is False and result["reason"] == "empty_path"
    time.sleep(0.3)
    assert api.pushed_ui == []
