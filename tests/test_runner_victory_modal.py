import threading
from pathlib import Path
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest

from core import runner as runner_module
from core import vision
from core.runner import MacroRunner


MODAL_MATCH = {"score": 0.99, "cx": 576, "cy": 562}


def _runner():
    runner = MacroRunner.__new__(MacroRunner)
    runner._mouse = MagicMock()
    runner._coords = {"unit_info_reset_x": 3, "unit_info_reset_y": 3}
    runner._log = MagicMock()
    runner._debug_save = lambda *_args: None
    return runner


def test_result_obtainment_modal_uses_zero_hold_then_parks(monkeypatch):
    runner = _runner()
    detections = iter((MODAL_MATCH, None))

    monkeypatch.setattr(
        runner_module.vision,
        "find_image",
        lambda *_args, **_kwargs: next(detections),
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

    assert runner._clear_result_obtainment_modal(123, threading.Event()) is True
    runner._mouse.click.assert_called_once_with(586, 582, hold=0.0)
    runner._mouse.move_to.assert_called_once_with(103, 203)


def test_result_obtainment_modal_stops_after_two_failed_closes(monkeypatch):
    runner = _runner()

    monkeypatch.setattr(
        runner_module.vision,
        "find_image",
        lambda *_args, **_kwargs: MODAL_MATCH,
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

    assert runner._clear_result_obtainment_modal(123, threading.Event()) is False
    assert runner._mouse.click.call_count == 2
    assert runner._mouse.move_to.call_count == 2


@pytest.mark.parametrize(
    ("name", "filename"),
    (
        ("victory", "victory_current.png"),
        ("result_modal_close", "result_modal_close.png"),
        ("leave_stage", "leave_stage_live.png"),
        ("return", "return_live.png"),
    ),
)
@pytest.mark.parametrize("scale", (0.85, 1.15))
def test_victory_recovery_assets_cover_fifteen_percent_scale_shift(name, filename, scale):
    path = next(
        candidate
        for candidate in vision.template_variant_paths(name)
        if Path(candidate).name == filename
    )
    template = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    assert template is not None

    scaled = cv2.resize(
        template,
        None,
        fx=scale,
        fy=scale,
        interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR,
    )
    frame = np.zeros((300, 500), dtype=np.uint8)
    height, width = scaled.shape
    frame[10:10 + height, 10:10 + width] = scaled

    vision.clear_template_cache()
    try:
        match = vision.find_in_gray_multiscale(frame, name)
    finally:
        vision.clear_template_cache()

    assert match is not None
    assert match["score"] >= vision.DEFAULT_THRESHOLD
