import threading
from unittest.mock import MagicMock, call

from core import runner as runner_module
from core.runner import MacroRunner


def _runner():
    runner = MacroRunner(MagicMock(), MagicMock(), MagicMock())
    runner._coords = {
        "team_loadout_x": 800,
        "team_loadout_y": 324,
        "team_loadout_row_height": 126,
    }
    return runner


def test_retries_teams_click_until_loadout_list_is_visually_open(monkeypatch):
    runner = _runner()
    stop = threading.Event()
    team_match = {"cx": 100, "cy": 100, "score": 0.95}
    open_match = {"cx": 275, "cy": 185, "score": 0.91}
    confirm_match = {"cx": 483, "cy": 416, "score": 0.98}
    include_match = {"cx": 456, "cy": 436, "score": 0.99}
    clicked_matches = []
    open_checks = iter([None, open_match])

    def wait_for_image(_hwnd, name, **_kwargs):
        if name == "team_loadout_open":
            return next(open_checks)
        if name == "confirm":
            return confirm_match
        if name == "include":
            return include_match
        raise AssertionError(f"unexpected image: {name}")

    monkeypatch.setattr(runner_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(runner_module.wm, "get_window_rect_screen", lambda _hwnd: (0, 0, 1152, 756))
    monkeypatch.setattr(runner_module.vision, "wait_for_image", wait_for_image)
    monkeypatch.setattr(
        runner_module.vision, "click_match",
        lambda _mouse, _hwnd, match: clicked_matches.append(match),
    )

    assert runner._apply_team_loadout_panel(123, stop, team_match, 1, "include") is True
    assert clicked_matches == [team_match, team_match, confirm_match, include_match]
    runner._mouse.click.assert_called_once_with(800, 324)
    assert any(
        "retrying the Teams button" in logged.args[0]
        for logged in runner._log.call_args_list
    )


def test_confirm_failure_saves_the_screen_that_detection_could_not_read(monkeypatch):
    runner = _runner()
    stop = threading.Event()
    team_match = {"cx": 100, "cy": 100, "score": 0.95}
    open_match = {"cx": 275, "cy": 185, "score": 0.91}
    saved = []

    def wait_for_image(_hwnd, name, **_kwargs):
        return open_match if name == "team_loadout_open" else None

    monkeypatch.setattr(runner_module, "TEAM_LOADOUT_CONFIRM_RETRY_ATTEMPTS", 1)
    monkeypatch.setattr(runner_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(runner_module.wm, "get_window_rect_screen", lambda _hwnd: (0, 0, 1152, 756))
    monkeypatch.setattr(runner_module.vision, "wait_for_image", wait_for_image)
    monkeypatch.setattr(runner_module.vision, "click_match", lambda *_args: None)
    monkeypatch.setattr(
        runner, "_save_debug_screenshot_unconditional",
        lambda _hwnd, name: saved.append(name),
    )

    assert runner._apply_team_loadout_panel(123, stop, team_match, 1, "include") is False
    assert saved == ["team_loadout_confirm_failed"]


def test_never_scrolls_or_clicks_a_row_when_loadout_list_did_not_open(monkeypatch):
    runner = _runner()
    stop = threading.Event()
    team_match = {"cx": 100, "cy": 100, "score": 0.95}
    team_clicks = []
    saved = []

    monkeypatch.setattr(runner_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(runner_module.vision, "wait_for_image", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runner_module.vision, "click_match",
        lambda _mouse, _hwnd, match: team_clicks.append(match),
    )
    monkeypatch.setattr(
        runner, "_save_debug_screenshot_unconditional",
        lambda _hwnd, name: saved.append(name),
    )

    assert runner._apply_team_loadout_panel(123, stop, team_match, 6, "include") is False
    assert team_clicks == [team_match] * runner_module.TEAM_LOADOUT_OPEN_RETRY_ATTEMPTS
    runner._mouse.drag.assert_not_called()
    runner._mouse.click.assert_not_called()
    assert saved == ["team_loadout_open_failed"]


def test_equipment_failure_saves_screen_and_fails_instead_of_silently_continuing(monkeypatch):
    runner = _runner()
    stop = threading.Event()
    team_match = {"cx": 100, "cy": 100, "score": 0.95}
    open_match = {"cx": 275, "cy": 185, "score": 0.91}
    confirm_match = {"cx": 483, "cy": 416, "score": 0.98}
    saved = []

    def wait_for_image(_hwnd, name, **_kwargs):
        if name == "team_loadout_open":
            return open_match
        if name == "confirm":
            return confirm_match
        if name == "exclude":
            return None
        raise AssertionError(f"unexpected image: {name}")

    monkeypatch.setattr(runner_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(runner_module.wm, "get_window_rect_screen", lambda _hwnd: (0, 0, 1152, 756))
    monkeypatch.setattr(runner_module.vision, "wait_for_image", wait_for_image)
    monkeypatch.setattr(runner_module.vision, "click_match", lambda *_args: None)
    monkeypatch.setattr(
        runner, "_save_debug_screenshot_unconditional",
        lambda _hwnd, name: saved.append(name),
    )

    assert runner._apply_team_loadout_panel(123, stop, team_match, 1, "exclude") is False
    assert saved == ["team_loadout_exclude_failed"]
