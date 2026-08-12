"""Repeat Stage has to let the map settle before placing units.

A first entry into a stage gets CAMERA_SETUP_SETTLE, then the camera drag
itself, then Team Loadout, before any unit is placed. A repeat skips all
three -- so the settle those steps incidentally provided disappeared with
them, and Place Unit ran against a world that had not finished rendering.
"""
import threading
from unittest.mock import MagicMock

from core import runner as runner_module
from core.runner import MacroRunner
from core.runner_constants import CAMERA_SETUP_SETTLE, REPEAT_ENTRY_SETTLE


def _runner(monkeypatch):
    runner = MacroRunner(MagicMock(), MagicMock(), MagicMock())
    runner._log = lambda *_a, **_k: None
    runner._set_status = lambda **_k: None
    runner._checkpoint = lambda _stop: False
    runner._run_prestart_blocks = lambda *_a, **_k: None
    runner._apply_team_loadout = lambda *_a, **_k: True
    runner._team_loadout_key = lambda _task: None
    monkeypatch.setattr(runner_module.camera, "run_camera_setup", lambda *_a, **_k: None)
    monkeypatch.setattr(runner_module.camera, "run_camera_drag_hold", lambda *_a, **_k: None)
    return runner


def _settles(monkeypatch, *, first_repeat):
    """Run _run_prestart and collect every settle it asks for."""
    slept = []
    runner = _runner(monkeypatch)
    runner._interruptible_sleep = lambda seconds, _stop=None: slept.append(seconds)
    runner._run_prestart(
        123, threading.Event(), {"mode": "expedition"}, {}, first_repeat)
    return slept


def test_a_repeat_entry_settles_before_placing_units(monkeypatch):
    """The regression: without this the repeat path went from "Teleported
    in-game" straight into Place Unit with nothing in between, and every unit
    aligned to the same large offset at the edge of the search box."""
    assert REPEAT_ENTRY_SETTLE in _settles(monkeypatch, first_repeat=False)


def test_a_first_entry_keeps_its_own_camera_settle_and_does_not_add_the_repeat_one(monkeypatch):
    """A first entry already waits via CAMERA_SETUP_SETTLE and the camera
    drag, so it must not pay the repeat settle on top -- that would add five
    seconds to every single fresh stage entry."""
    slept = _settles(monkeypatch, first_repeat=True)

    assert CAMERA_SETUP_SETTLE in slept
    assert REPEAT_ENTRY_SETTLE not in slept


def test_the_repeat_settle_is_long_enough_to_matter(monkeypatch):
    """Guards against it being trimmed back to something that cannot cover
    what the camera path provided -- the settle plus the 730ms drag hold."""
    assert REPEAT_ENTRY_SETTLE > CAMERA_SETUP_SETTLE
