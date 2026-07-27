import threading

from core import runner, runner_expedition
from core.runner import MacroRunner


class _Noop:
    def __getattr__(self, _):
        return lambda *args, **kwargs: None


def _runner():
    return MacroRunner(_Noop(), _Noop(), lambda message: None)


def test_match_result_poll_searches_one_captured_frame(monkeypatch):
    r = _runner()
    calls = []
    monkeypatch.setattr(runner.vision, "load_template_grays", lambda name: [object()])
    monkeypatch.setattr(
        runner.vision, "find_image_any",
        lambda hwnd, names: (calls.append(tuple(names)) or ({"score": 0.99}, "victory")))

    result = r._wait_for_match_result(123, threading.Event(), mode="story")

    assert result == "win"
    assert calls == [tuple(runner.RECONNECT_IMAGE_NAMES) + ("victory", "defeat")]


def test_teleport_poll_searches_all_signals_on_one_frame(monkeypatch):
    r = _runner()
    calls = []
    monkeypatch.setattr(runner.vision, "load_template_grays", lambda name: [object()])
    monkeypatch.setattr(
        runner.vision, "find_image_any",
        lambda hwnd, names: (calls.append(tuple(names)) or ({"score": 1.0}, "nav_unitmanager")))

    result = r._wait_for_teleport_or_stuck(123, threading.Event(), timeout=1)

    assert result == "ok"
    assert calls == [("nav_unitmanager",) + tuple(runner.RECONNECT_IMAGE_NAMES) + ("teleportstuck",)]


def test_expedition_poll_searches_template_signals_on_one_frame(monkeypatch):
    r = _runner()
    r._expedition_color_buttons = False
    calls = []
    monkeypatch.setattr(
        runner_expedition.vision, "find_image_any",
        lambda hwnd, names: (calls.append(tuple(names)) or ({"score": 0.98}, "defeat")))

    result = r._check_expedition_wave_result(123, threading.Event())

    assert result == "loss"
    assert calls == [(
        "nav_start_game", "nav_start_game_confirm", "select upgrade card",
        "defeat", "exp_extract", "exp_continue")]


def test_warning_poll_checks_confirm_warning_and_start_on_one_frame(monkeypatch):
    r = _runner()
    calls = []
    results = iter([
        ({"score": 0.97}, "warning"),
        ({"score": 0.99}, "nav_start_game"),
    ])
    monkeypatch.setattr(
        runner.vision, "find_image_any",
        lambda hwnd, names: (calls.append(tuple(names)) or next(results)))
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)

    r._wait_out_start_game_warning(123, threading.Event())

    assert calls == [
        ("nav_start_game_confirm", "warning"),
        ("nav_start_game_confirm", "warning", "nav_start_game"),
    ]
