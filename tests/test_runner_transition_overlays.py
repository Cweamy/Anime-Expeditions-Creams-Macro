import threading
from pathlib import Path
from unittest.mock import MagicMock

import cv2
import numpy as np

from core import runner as runner_module
from core import vision
from core.runner import MacroRunner


PARTY_MATCH = {"score": 0.99, "cx": 714, "cy": 175}


def _runner():
    runner = MacroRunner.__new__(MacroRunner)
    runner._mouse = MagicMock()
    runner._coords = {"unit_info_reset_x": 3, "unit_info_reset_y": 3}
    runner._log = MagicMock()
    runner._debug_save = lambda *_args: None
    return runner


def test_party_overlay_uses_zero_hold_then_parks_and_verifies(monkeypatch):
    runner = _runner()
    detections = iter(((PARTY_MATCH, "invite_players_open"), (None, None)))

    monkeypatch.setattr(
        runner_module.vision,
        "find_image_any",
        lambda *_args, **_kwargs: next(detections),
    )
    monkeypatch.setattr(
        runner_module.vision,
        "find_image",
        lambda *_args, **_kwargs: PARTY_MATCH,
    )
    monkeypatch.setattr(
        runner_module.vision,
        "ref_to_screen",
        lambda _hwnd, x, y: (x + 10, y + 20),
    )
    monkeypatch.setattr(runner_module.wm, "activate_window", lambda _hwnd: True)
    monkeypatch.setattr(
        runner_module.wm,
        "get_window_rect_screen",
        lambda _hwnd: (100, 200, 1252, 956),
    )
    monkeypatch.setattr(runner_module.time, "sleep", lambda _seconds: None)

    assert runner._dismiss_party_overlay(123, threading.Event()) is True
    runner._mouse.click.assert_called_once_with(724, 195, hold=0.0)
    runner._mouse.move_to.assert_called_once_with(103, 203)


def test_party_overlay_stops_after_two_failed_dismissals(monkeypatch):
    runner = _runner()

    monkeypatch.setattr(
        runner_module.vision,
        "find_image_any",
        lambda *_args, **_kwargs: (PARTY_MATCH, "nav_disband"),
    )
    monkeypatch.setattr(
        runner_module.vision,
        "ref_to_screen",
        lambda _hwnd, x, y: (x, y),
    )
    monkeypatch.setattr(runner_module.wm, "activate_window", lambda _hwnd: True)
    monkeypatch.setattr(
        runner_module.wm,
        "get_window_rect_screen",
        lambda _hwnd: (0, 0, 1152, 756),
    )
    monkeypatch.setattr(runner_module.time, "sleep", lambda _seconds: None)

    assert runner._dismiss_party_overlay(123, threading.Event()) is False
    assert runner._mouse.click.call_count == 2
    assert runner._mouse.move_to.call_count == 2


def test_gamemode_click_retries_after_party_overlay(monkeypatch):
    runner = _runner()
    click_target = MagicMock()
    detections = iter(((PARTY_MATCH, "invite_players_open"), (None, None)))

    monkeypatch.setattr(
        runner_module.vision,
        "find_image_any",
        lambda *_args, **_kwargs: next(detections),
    )
    monkeypatch.setattr(runner, "_dismiss_party_overlay", MagicMock(return_value=True))
    monkeypatch.setattr(runner_module.time, "sleep", lambda _seconds: None)

    assert runner._click_gamemode_target(
        123, threading.Event(), "Challenge", click_target) is True
    assert click_target.call_count == 2
    runner._dismiss_party_overlay.assert_called_once()


def test_play_click_parks_cursor_before_gamemode_transition(monkeypatch):
    runner = _runner()
    runner._set_status = MagicMock()
    play_match = {"score": 1.0, "cx": 575, "cy": 675}

    monkeypatch.setattr(
        runner_module.vision,
        "find_image_any",
        lambda *_args, **_kwargs: (play_match, "nav_play"),
    )
    click_match = MagicMock()
    monkeypatch.setattr(runner_module.vision, "click_match", click_match)
    monkeypatch.setattr(runner_module.wm, "activate_window", lambda _hwnd: True)
    monkeypatch.setattr(
        runner_module.wm,
        "get_window_rect_screen",
        lambda _hwnd: (100, 200, 1252, 956),
    )
    monkeypatch.setattr(runner_module.time, "sleep", lambda _seconds: None)

    assert runner._click_play(123, threading.Event()) is True
    click_match.assert_called_once_with(runner._mouse, 123, play_match)
    runner._mouse.move_to.assert_called_once_with(103, 203)


def test_invite_decline_variant_survives_small_ui_scale_shift(tmp_path):
    source = Path(vision.UI_ASSETS_DIR, "nav_disband", "nav_disband_invite.png")
    template = cv2.imread(str(source), cv2.IMREAD_GRAYSCALE)
    assert template is not None

    variant_dir = tmp_path / "nav_disband"
    variant_dir.mkdir()
    cv2.imwrite(str(variant_dir / source.name), template)

    scaled = cv2.resize(template, None, fx=1.05, fy=1.05, interpolation=cv2.INTER_LINEAR)
    frame = np.zeros((756, 1152), dtype=np.uint8)
    height, width = scaled.shape
    frame[155:155 + height, 645:645 + width] = scaled

    vision.clear_template_cache()
    try:
        match = vision.find_in_gray_multiscale(
            frame,
            "nav_disband",
            template_dir=str(tmp_path),
        )
    finally:
        vision.clear_template_cache()

    assert match is not None
    assert match["score"] >= vision.DEFAULT_THRESHOLD
