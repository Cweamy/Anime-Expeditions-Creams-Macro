import threading

from core import runner as runner_module
from core.runner import MacroRunner


LEAVE_MATCH = {"score": 1.0, "cx": 100, "cy": 100}
RETURN_MATCH = {"score": 1.0, "cx": 200, "cy": 200}


def _runner():
    runner = MacroRunner.__new__(MacroRunner)
    runner._mouse = object()
    runner._log = lambda _message: None
    runner._debug_save = lambda *_args: None
    runner._checkpoint = lambda _stop_event: False
    return runner


def test_stop_does_not_click_leave_stage_or_return_to_lobby(monkeypatch):
    """F2/Stop must just stop -- it must not try to quit the current match
    out to the lobby first. Reported live: Stop was leaving a live match
    instead of simply halting where it was."""
    runner = MacroRunner(object(), object(), lambda *_a, **_kw: None)
    runner._current_hwnd = 123
    runner._stop_logged = False
    runner._paused_logged = False
    runner._pause_event.clear()

    searched = []
    monkeypatch.setattr(
        runner_module.vision, "find_image",
        lambda *_a, **_kw: searched.append("find_image") or LEAVE_MATCH)
    monkeypatch.setattr(
        runner_module.vision, "click_match",
        lambda *_a, **_kw: searched.append("click_match"))

    stop_event = threading.Event()
    stop_event.set()
    assert runner._checkpoint(stop_event) is True
    assert searched == [], "Stop must not search for or click Leave Stage/Return to Lobby"


def test_leave_stage_stops_retrying_when_return_modal_appears(monkeypatch):
    clicks = []
    searched = []

    monkeypatch.setattr(
        runner_module.vision, "wait_for_image", lambda *_args, **_kwargs: LEAVE_MATCH)

    def find_image(_hwnd, name):
        searched.append(name)
        return RETURN_MATCH if name == "return" else LEAVE_MATCH

    monkeypatch.setattr(runner_module.vision, "find_image", find_image)
    monkeypatch.setattr(
        runner_module.vision, "click_match", lambda *_args: clicks.append("click"))
    monkeypatch.setattr(runner_module.wm, "activate_window", lambda _hwnd: True)
    monkeypatch.setattr(runner_module.time, "sleep", lambda _seconds: None)

    result = _runner()._click_and_verify_gone(
        123, threading.Event(), "leave_stage", 8.0, success_name="return")

    assert result is True
    assert clicks == ["click"]
    assert searched == ["return"]


def test_other_buttons_still_retry_when_they_remain_visible(monkeypatch):
    clicks = []

    monkeypatch.setattr(
        runner_module.vision, "wait_for_image", lambda *_args, **_kwargs: LEAVE_MATCH)
    monkeypatch.setattr(
        runner_module.vision, "find_image", lambda *_args, **_kwargs: LEAVE_MATCH)
    monkeypatch.setattr(
        runner_module.vision, "click_match", lambda *_args: clicks.append("click"))
    monkeypatch.setattr(runner_module.wm, "activate_window", lambda _hwnd: True)
    monkeypatch.setattr(runner_module.time, "sleep", lambda _seconds: None)

    result = _runner()._click_and_verify_gone(
        123, threading.Event(), "repeat_stage", 8.0)

    assert result is True
    assert len(clicks) == 3


def test_return_to_lobby_click_is_verified_and_retried(monkeypatch):
    clicks = []
    focus_attempts = []
    checks = iter((RETURN_MATCH, None))

    monkeypatch.setattr(
        runner_module.vision, "wait_for_image", lambda *_args, **_kwargs: RETURN_MATCH)
    monkeypatch.setattr(
        runner_module.vision, "find_image", lambda *_args, **_kwargs: next(checks))
    monkeypatch.setattr(
        runner_module.vision, "click_match", lambda *_args: clicks.append("click"))
    monkeypatch.setattr(
        runner_module.wm, "activate_window", lambda hwnd: focus_attempts.append(hwnd) or True)
    monkeypatch.setattr(runner_module.time, "sleep", lambda _seconds: None)

    result = _runner()._click_return_to_lobby_if_found(123, threading.Event())

    assert result is True
    assert clicks == ["click", "click"]
    assert focus_attempts == [123, 123]


def test_missing_return_to_lobby_remains_optional(monkeypatch):
    clicks = []

    monkeypatch.setattr(
        runner_module.vision, "wait_for_image", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runner_module.vision, "click_match", lambda *_args: clicks.append("click"))

    result = _runner()._click_return_to_lobby_if_found(123, threading.Event())

    assert result is False
    assert clicks == []
