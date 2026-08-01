import threading
from unittest.mock import MagicMock

from core import runner as runner_module
from core.runner import MacroRunner


def _runner():
    runner = MacroRunner(MagicMock(), MagicMock(), MagicMock())
    # Isolate _run_prestart to just the camera-setup step under test --
    # Team Loadout/prestart blocks are exercised by their own test files.
    runner._apply_team_loadout = lambda *_a, **_kw: True
    runner._run_prestart_blocks = lambda *_a, **_kw: None
    return runner


def test_camera_settle_runs_before_the_drag(monkeypatch):
    calls = []
    monkeypatch.setattr(runner_module.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        runner_module.camera, "run_camera_setup",
        lambda *_a, **_kw: calls.append("camera"))

    runner = _runner()
    real_sleep = runner._interruptible_sleep

    def spy_sleep(seconds, stop_event=None):
        calls.append(("settle", seconds))
        return real_sleep(seconds, stop_event)

    runner._interruptible_sleep = spy_sleep

    assert runner._run_prestart(123, threading.Event(), {"mode": "story"}, {}) is True
    assert calls == [("settle", runner_module.CAMERA_SETUP_SETTLE), "camera"]


def test_stop_during_camera_settle_skips_the_drag_immediately(monkeypatch):
    """The settle must stay interruptible -- F2/Stop landing during it must
    not block for the full settle duration nor still run the camera drag."""
    calls = []
    monkeypatch.setattr(
        runner_module.camera, "run_camera_setup",
        lambda *_a, **_kw: calls.append("camera"))

    runner = _runner()
    stop_event = threading.Event()
    stop_event.set()

    assert runner._run_prestart(123, stop_event, {"mode": "story"}, {}) is False
    assert calls == [], "camera setup must not run once Stop has already landed"
