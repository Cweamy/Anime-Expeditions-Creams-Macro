import threading
from unittest.mock import MagicMock, call

import numpy as np
import pytest

from core import runner as runner_module
from core.runner import MacroRunner, _find_team_load_button


@pytest.fixture(autouse=True)
def _blank_game_capture(monkeypatch):
    monkeypatch.setattr(
        runner_module.vision,
        "capture_game_bgr",
        lambda _hwnd: np.zeros((756, 1152, 3), dtype=np.uint8),
    )


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
    monkeypatch.setattr(runner_module.ocr_windows, "is_available", lambda: False)
    monkeypatch.setattr(
        runner_module.vision, "click_match",
        lambda _mouse, _hwnd, match: clicked_matches.append(match),
    )

    assert runner._apply_team_loadout_panel(123, stop, team_match, 1, "include") is True
    assert clicked_matches == [team_match, team_match, confirm_match, include_match]
    runner._mouse.click.assert_called_once_with(850, 324)
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
    monkeypatch.setattr(runner_module.ocr_windows, "is_available", lambda: False)
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
    runner._mouse.scroll.assert_not_called()
    runner._mouse.click.assert_not_called()
    assert saved == ["team_loadout_open_failed"]


def test_windows_ocr_confirms_current_unit_teams_title_without_lowering_threshold(monkeypatch):
    runner = _runner()
    stop = threading.Event()
    team_match = {"cx": 100, "cy": 100, "score": 0.95}
    confirm_match = {"cx": 483, "cy": 416, "score": 0.98}
    include_match = {"cx": 456, "cy": 436, "score": 0.99}

    def wait_for_image(_hwnd, name, **_kwargs):
        return {
            "team_loadout_open": None,
            "confirm": confirm_match,
            "include": include_match,
        }[name]

    monkeypatch.setattr(runner_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(runner_module.wm, "get_window_rect_screen", lambda _hwnd: (0, 0, 1152, 756))
    monkeypatch.setattr(runner_module.vision, "wait_for_image", wait_for_image)
    monkeypatch.setattr(runner_module.vision, "click_match", lambda *_args: None)
    monkeypatch.setattr(
        runner_module.vision, "capture_game_bgr",
        lambda _hwnd: np.zeros((756, 1152, 3), dtype=np.uint8),
    )
    monkeypatch.setattr(runner_module.ocr_windows, "is_available", lambda: True)
    monkeypatch.setattr(runner_module.ocr_windows, "ocr_image", lambda _frame: "Unit Teams")

    assert runner._apply_team_loadout_panel(123, stop, team_match, 1, "include") is True
    assert any(
        'confirmed by Windows OCR: "Unit Teams"' in logged.args[0]
        for logged in runner._log.call_args_list
    )


def test_generic_ocr_text_does_not_confirm_by_default(monkeypatch):
    """Loose Team Panel Detection (Settings > Debug) is off by default --
    a generic word like "Teams" (which the still-open Unit Manager's own
    button already reads) must NOT be enough to confirm the Load Team list
    is open unless the user opted into the wider match."""
    runner = _runner()
    assert runner._loose_team_ocr_match is False
    stop = threading.Event()
    team_match = {"cx": 100, "cy": 100, "score": 0.95}
    saved = []

    monkeypatch.setattr(runner_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(runner_module.vision, "wait_for_image", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner_module.vision, "click_match", lambda *_args: None)
    monkeypatch.setattr(
        runner_module.vision, "capture_game_bgr",
        lambda _hwnd: np.zeros((756, 1152, 3), dtype=np.uint8),
    )
    monkeypatch.setattr(runner_module.ocr_windows, "is_available", lambda: True)
    monkeypatch.setattr(runner_module.ocr_windows, "ocr_image", lambda _frame: "Teams")
    monkeypatch.setattr(
        runner, "_save_debug_screenshot_unconditional",
        lambda _hwnd, name: saved.append(name),
    )

    assert runner._apply_team_loadout_panel(123, stop, team_match, 1, "include") is False
    assert saved == ["team_loadout_open_failed"]


def test_generic_ocr_text_confirms_when_loose_match_is_enabled(monkeypatch):
    runner = _runner()
    runner._loose_team_ocr_match = True
    stop = threading.Event()
    team_match = {"cx": 100, "cy": 100, "score": 0.95}
    confirm_match = {"cx": 483, "cy": 416, "score": 0.98}
    include_match = {"cx": 456, "cy": 436, "score": 0.99}

    def wait_for_image(_hwnd, name, **_kwargs):
        return {
            "team_loadout_open": None,
            "confirm": confirm_match,
            "include": include_match,
        }[name]

    monkeypatch.setattr(runner_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(runner_module.wm, "get_window_rect_screen", lambda _hwnd: (0, 0, 1152, 756))
    monkeypatch.setattr(runner_module.vision, "wait_for_image", wait_for_image)
    monkeypatch.setattr(runner_module.vision, "click_match", lambda *_args: None)
    monkeypatch.setattr(
        runner_module.vision, "capture_game_bgr",
        lambda _hwnd: np.zeros((756, 1152, 3), dtype=np.uint8),
    )
    monkeypatch.setattr(runner_module.ocr_windows, "is_available", lambda: True)
    monkeypatch.setattr(runner_module.ocr_windows, "ocr_image", lambda _frame: "Teams")

    assert runner._apply_team_loadout_panel(123, stop, team_match, 1, "include") is True
    assert any(
        'confirmed by Windows OCR: "Teams"' in logged.args[0]
        for logged in runner._log.call_args_list
    )


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


@pytest.mark.parametrize(
    ("team_num", "scroll_steps", "expected_y"),
    [(4, 4, 335), (5, 5, 372), (6, 6, 409), (7, 7, 446), (8, 7, 579)],
)
def test_scrolled_loadouts_use_wheel_over_scrollbar_and_click_visible_button_center(
        monkeypatch, team_num, scroll_steps, expected_y):
    runner = _runner()
    stop = threading.Event()
    team_match = {"cx": 100, "cy": 100, "score": 0.95}
    open_match = {"cx": 275, "cy": 185, "score": 0.91}
    confirm_match = {"cx": 483, "cy": 416, "score": 0.98}
    include_match = {"cx": 456, "cy": 436, "score": 0.99}

    def wait_for_image(_hwnd, name, **_kwargs):
        return {
            "team_loadout_open": open_match,
            "confirm": confirm_match,
            "include": include_match,
        }[name]

    monkeypatch.setattr(runner_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(runner_module.wm, "get_window_rect_screen", lambda _hwnd: (0, 0, 1152, 756))
    monkeypatch.setattr(runner_module.vision, "wait_for_image", wait_for_image)
    monkeypatch.setattr(runner_module.vision, "click_match", lambda *_args: None)

    assert runner._apply_team_loadout_panel(
        123, stop, team_match, team_num, "include") is True

    runner._mouse.move_to.assert_called_once_with(927, 400)
    runner._mouse.nudge.assert_called_once_with()
    assert runner._mouse.scroll.call_args_list == [
        call(runner_module.TEAM_LOADOUT_WHEEL_DELTA)
    ] * scroll_steps
    runner._mouse.drag.assert_not_called()
    runner._mouse.click.assert_called_once_with(850, expected_y)


def test_finds_partly_clipped_load_team_button_nearest_expected_row():
    frame = np.zeros((756, 1152, 3), dtype=np.uint8)
    # Two complete buttons and the visible top of slot 8, matching the
    # geometry from the live failure capture.
    frame[280:327, 778:894] = (0, 220, 80)
    frame[404:450, 778:894] = (0, 220, 80)
    frame[528:556, 778:894] = (0, 220, 80)

    assert _find_team_load_button(frame, expected_y=579) == (836, 542)


def test_live_button_detection_is_scaled_to_undocked_window(monkeypatch):
    runner = _runner()
    stop = threading.Event()
    team_match = {"cx": 100, "cy": 100, "score": 0.95}
    open_match = {"cx": 275, "cy": 185, "score": 0.91}
    confirm_match = {"cx": 483, "cy": 416, "score": 0.98}
    include_match = {"cx": 456, "cy": 436, "score": 0.99}
    frame = np.zeros((756, 1152, 3), dtype=np.uint8)
    frame[528:556, 778:894] = (0, 220, 80)

    def wait_for_image(_hwnd, name, **_kwargs):
        return {
            "team_loadout_open": open_match,
            "confirm": confirm_match,
            "include": include_match,
        }[name]

    monkeypatch.setattr(runner_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(runner_module.wm, "get_window_rect_screen", lambda _hwnd: (10, 20, 1194, 856))
    monkeypatch.setattr(runner_module.vision, "wait_for_image", wait_for_image)
    monkeypatch.setattr(runner_module.vision, "click_match", lambda *_args: None)
    monkeypatch.setattr(runner_module.vision, "capture_game_bgr", lambda _hwnd: frame)

    assert runner._apply_team_loadout_panel(
        123, stop, team_match, 8, "include") is True

    # (836, 542) in 1152x756 reference space, scaled into a 1184x836 window.
    runner._mouse.click.assert_called_once_with(869, 619)
