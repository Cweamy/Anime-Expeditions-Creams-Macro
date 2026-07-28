import threading
from unittest.mock import MagicMock

import pytest

from core import runner as runner_module
from core.runner import MacroRunner


def _runner():
    runner = MacroRunner(MagicMock(), MagicMock(), MagicMock())
    runner._set_status = MagicMock()
    return runner


@pytest.mark.parametrize(
    "task, expected",
    [
        ({"mode": "story", "stage": "Infinite", "infinite_wave_limit": 50}, 50),
        ({"mode": "story", "stage": "Infinite"}, 20),
        ({"mode": "story", "stage": "Infinite", "infinite_wave_limit": "bad"}, 20),
        ({"mode": "story", "stage": "Infinite", "infinite_wave_limit": 0}, 20),
        ({"mode": "story", "stage": "5", "infinite_wave_limit": 50}, None),
        ({"mode": "raid", "stage": "Infinite", "infinite_wave_limit": 50}, None),
    ],
)
def test_infinite_wave_limit_only_applies_to_story_infinite(task, expected):
    assert MacroRunner._infinite_wave_limit(task) == expected


def test_wave_limit_finishes_selected_wave_then_requires_a_confirming_read(monkeypatch):
    runner = _runner()
    readings = iter(((20, None), (21, None), (21, None)))
    left = []
    state = {}

    monkeypatch.setattr(
        runner_module.vision, "capture_window_region_bgr", lambda *_args: object())
    monkeypatch.setattr(runner_module.wave_module, "read_wave", lambda _image: next(readings))
    monkeypatch.setattr(
        runner, "_leave_infinite_at_wave_limit",
        lambda _hwnd, _stop, limit: left.append(limit) or True)

    for _ in range(3):
        state["next_check"] = 0
        result = runner._check_infinite_wave_limit(123, threading.Event(), 20, state)

    assert result == "wave_limit"
    assert left == [20]


def test_finite_wave_badge_cannot_trigger_infinite_exit(monkeypatch):
    runner = _runner()
    state = {}
    left = []

    monkeypatch.setattr(
        runner_module.vision, "capture_window_region_bgr", lambda *_args: object())
    monkeypatch.setattr(runner_module.wave_module, "read_wave", lambda _image: (21, 30))
    monkeypatch.setattr(
        runner, "_leave_infinite_at_wave_limit",
        lambda *_args: left.append(True) or True)

    for _ in range(3):
        state["next_check"] = 0
        assert runner._check_infinite_wave_limit(
            123, threading.Event(), 20, state) is None

    assert left == []


def test_failed_leave_reports_failure_to_match_loop(monkeypatch):
    runner = _runner()
    state = {"confirmations": 1}

    monkeypatch.setattr(
        runner_module.vision, "capture_window_region_bgr", lambda *_args: object())
    monkeypatch.setattr(runner_module.wave_module, "read_wave", lambda _image: (11, None))
    monkeypatch.setattr(runner, "_leave_infinite_at_wave_limit", lambda *_args: False)

    assert runner._check_infinite_wave_limit(
        123, threading.Event(), 10, state) == "failed"
