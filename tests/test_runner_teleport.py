import threading
from unittest.mock import MagicMock

import pytest

from core import runner as runner_module
from core.runner import MacroRunner


def test_normal_loading_screen_is_not_treated_as_a_disconnect(monkeypatch):
    runner = MacroRunner(MagicMock(), MagicMock(), MagicMock())
    searched = []
    times = iter((0.0, 0.0, 2.0))

    monkeypatch.setattr(runner_module.time, "time", lambda: next(times))
    monkeypatch.setattr(runner_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        runner_module.vision,
        "find_image",
        lambda _hwnd, name: searched.append(name) or None,
    )

    result = runner._wait_for_teleport_result(
        123, threading.Event(), timeout=1.0
    )

    assert result == "timeout"
    assert searched == ["nav_unitmanager", "reconnect"]
    assert "teleportstuck" not in searched


class _Clock:
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.mark.parametrize(
    ("name", "timeout", "success_at", "reconnect_at", "missing", "stopped", "expected"),
    [
        ("immediate success", 20.0, 0.0, None, None, False, "ok"),
        ("ordinary delayed load", 20.0, 6.0, None, None, False, "ok"),
        ("slow solo load", 20.0, 19.5, None, None, False, "ok"),
        ("very slow matchmaking load", 300.0, 150.0, None, None, False, "ok"),
        ("full timeout", 20.0, None, None, None, False, "timeout"),
        ("definite reconnect", 20.0, None, 2.1, None, False, "disconnected"),
        ("optional reconnect crop missing", 20.0, 3.0, None, "reconnect", False, "ok"),
        ("required unit-manager crop missing", 20.0, None, None, "nav_unitmanager", False, "timeout"),
        ("cancelled by user", 20.0, None, None, None, True, "stopped"),
    ],
)
def test_teleport_wait_simulation_matrix(
        monkeypatch, name, timeout, success_at, reconnect_at, missing, stopped,
        expected):
    del name  # pytest uses this only to make failures self-explanatory
    runner = MacroRunner(MagicMock(), MagicMock(), MagicMock())
    clock = _Clock()
    stop_event = threading.Event()
    if stopped:
        stop_event.set()
    searched = []

    def find_image(_hwnd, image_name):
        searched.append(image_name)
        if image_name == missing:
            raise runner_module.vision.TemplateNotFound(image_name)
        if image_name == "nav_unitmanager":
            return {"score": 1.0} if success_at is not None and clock.now >= success_at else None
        if image_name == "reconnect":
            return {"score": 1.0} if reconnect_at is not None and clock.now >= reconnect_at else None
        # Rate-limited lobby check -- being on the lobby means no teleport is
        # coming. Every case here is genuinely loading, so never found.
        if image_name == "nav_play":
            return None
        raise AssertionError(f"unexpected teleport search: {image_name}")

    monkeypatch.setattr(runner_module.time, "time", clock.time)
    monkeypatch.setattr(runner_module.time, "sleep", clock.sleep)
    monkeypatch.setattr(runner_module.vision, "find_image", find_image)

    result = runner._wait_for_teleport_result(
        123, stop_event, timeout=timeout)

    assert result == expected
    assert "teleportstuck" not in searched


def test_confirmed_in_game_wins_when_reconnect_art_is_also_visible(monkeypatch):
    runner = MacroRunner(MagicMock(), MagicMock(), MagicMock())
    searched = []

    monkeypatch.setattr(runner_module.time, "time", _Clock().time)
    monkeypatch.setattr(
        runner_module.vision,
        "find_image",
        lambda _hwnd, name: searched.append(name) or {"score": 1.0},
    )

    assert runner._wait_for_teleport_result(
        123, threading.Event(), timeout=20.0) == "ok"
    assert searched == ["nav_unitmanager"]


def test_teleport_wait_delay_sweep_covers_poll_boundaries(monkeypatch):
    """Exercise every 0.3s poll boundary in Solo plus long MM delays."""
    runner = MacroRunner(MagicMock(), MagicMock(), MagicMock())
    scenarios = [
        (round(index * 0.3, 1), 20.0)
        for index in range(67)  # 0.0 through 19.8 seconds
    ] + [
        (20.0, 20.0),  # the exact deadline is correctly a timeout
        (30.0, 300.0), (60.0, 300.0), (120.0, 300.0),
        (180.0, 300.0), (240.0, 300.0), (299.7, 300.0),
    ]

    for success_at, timeout in scenarios:
        clock = _Clock()
        searched = []

        def find_image(_hwnd, image_name):
            searched.append(image_name)
            if image_name == "nav_unitmanager":
                return {"score": 1.0} if clock.now >= success_at else None
            if image_name == "reconnect":
                return None
            if image_name == "nav_play":
                return None      # genuinely loading, never on the lobby
            raise AssertionError(f"unexpected teleport search: {image_name}")

        monkeypatch.setattr(runner_module.time, "time", clock.time)
        monkeypatch.setattr(runner_module.time, "sleep", clock.sleep)
        monkeypatch.setattr(runner_module.vision, "find_image", find_image)

        result = runner._wait_for_teleport_result(
            123, threading.Event(), timeout=timeout)

        expected = "timeout" if success_at >= timeout else "ok"
        assert result == expected, (success_at, timeout, clock.now)
        assert "teleportstuck" not in searched


@pytest.mark.parametrize(
    ("result", "expected", "rejoins"),
    [
        ("ok", True, 0),
        ("timeout", False, 0),
        ("stopped", False, 0),
        ("disconnected", False, 1),
    ],
)
def test_teleport_wrapper_routes_each_result(result, expected, rejoins):
    runner = MacroRunner(MagicMock(), MagicMock(), MagicMock())
    runner._wait_for_teleport_result = MagicMock(return_value=result)
    runner._handle_disconnect = MagicMock()

    actual = runner._wait_teleport_in(
        123, threading.Event(), webhook={}, task={"map": "Map"}, timeout=20.0)

    assert actual is expected
    assert runner._handle_disconnect.call_count == rejoins


def test_teleport_timeout_saves_diagnostic_screenshot():
    runner = MacroRunner(MagicMock(), MagicMock(), MagicMock())
    runner._wait_for_teleport_result = MagicMock(return_value="timeout")
    runner._save_debug_screenshot_unconditional = MagicMock(
        return_value="teleport_timeout.png"
    )

    assert runner._wait_teleport_in(
        123, threading.Event(), webhook={}, task={"map": "Map"}, timeout=20.0
    ) is False

    runner._save_debug_screenshot_unconditional.assert_called_once_with(
        123, "teleport_timeout"
    )


def test_slow_solo_teleport_waits_across_chunks_without_reclicking(monkeypatch):
    runner = MacroRunner(MagicMock(), MagicMock(), MagicMock())
    match = {"score": 1.0, "x": 1, "y": 2, "w": 3, "h": 4}
    start_results = iter(((match, "nav_start"), (None, None), (None, None)))
    teleport_results = iter(("timeout", "timeout", "ok"))
    clicks = []

    monkeypatch.setattr(
        runner_module.vision, "wait_for_image_any",
        lambda *_args, **_kwargs: next(start_results))
    monkeypatch.setattr(
        runner_module.vision, "click_match",
        lambda *_args, **_kwargs: clicks.append("start"))
    runner._wait_for_teleport_result = (
        lambda *_args, **_kwargs: next(teleport_results))
    runner._debug_save = lambda *_args, **_kwargs: ""

    assert runner._click_start_and_wait_teleport(
        123, threading.Event(), webhook={}, task={"map": "Map"}) is True
    assert clicks == ["start"]
