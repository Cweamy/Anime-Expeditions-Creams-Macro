import threading

from core import keys
from core.runner_constants import (
    FUEL_ACTION_TIMEOUT,
    FUEL_CLICK_DELAY,
    FUEL_CONFIRM_TIMEOUT,
)
from core.runner_fuel import FuelOps


class DummyKeyboard:
    def __init__(self):
        self.combos = []
        self.taps = []
        self.typed = []

    def combo(self, *key_codes):
        self.combos.append(key_codes)

    def tap(self, key_code):
        self.taps.append(key_code)

    def type_text(self, text):
        self.typed.append(text)


class DummyRunner(FuelOps):
    def __init__(self, due=("resource_drill", "gold_mine")):
        self._keyboard = DummyKeyboard()
        self._mouse = object()
        self._current_hwnd = 123
        self.logs = []
        self.statuses = []
        self.results = []
        self.paths_run = []
        self.refills = []
        self.recovered = 0
        self.settings = {
            "enabled": True,
            "resources": {
                "resource_drill": {
                    "enabled": True,
                    "due": "resource_drill" in due,
                    "amount": 25,
                },
                "gold_mine": {
                    "enabled": True,
                    "due": "gold_mine" in due,
                    "amount": "max",
                },
            },
            "paths": {
                "hub_to_resource_drill": "To Drill",
                "hub_to_gold_mine": "To Gold",
                "resource_drill_to_gold_mine": "Drill To Gold",
            },
        }
        self._get_fuel_settings = lambda: self.settings
        self._mark_fuel_refill_result = (
            lambda resource, succeeded: self.results.append((resource, succeeded))
        )

    def _log(self, message):
        self.logs.append(message)

    def _set_status(self, **kwargs):
        self.statuses.append(kwargs)

    def _checkpoint(self, stop_event):
        return stop_event.is_set()

    def _ensure_lobby(self, hwnd, stop_event):
        return True

    def _fuel_enter_hub(self, hwnd, stop_event):
        return True

    def _fuel_run_path(self, path_name, stop_event):
        self.paths_run.append(path_name)
        return bool(path_name)

    def _wait_for_image_gone(self, hwnd, names, timeout, stop_event):
        return True

    def _recover_to_lobby(self, hwnd, stop_event):
        self.recovered += 1
        return True


def test_both_due_use_drill_first_then_continue_to_gold(monkeypatch):
    runner = DummyRunner()
    monkeypatch.setattr(
        runner,
        "_fuel_refill_station",
        lambda _hwnd, _stop, resource, amount: (
            runner.refills.append((resource, amount)) or resource != "resource_drill"
        ),
    )
    monkeypatch.setattr("core.runner_fuel.wm.show_window", lambda _hwnd: None)
    monkeypatch.setattr("core.runner_fuel.wm.activate_window", lambda _hwnd: True)
    monkeypatch.setattr("core.runner_fuel.time.sleep", lambda _seconds: None)

    runner._run_fuel_refill(123, threading.Event())

    assert runner.paths_run == ["To Drill", "Drill To Gold"]
    assert runner.refills == [
        ("resource_drill", 25),
        ("gold_mine", "max"),
    ]
    assert runner.results == [
        ("resource_drill", False),
        ("gold_mine", True),
    ]
    assert runner.recovered == 1


def test_single_gold_due_uses_direct_hub_path(monkeypatch):
    runner = DummyRunner(due=("gold_mine",))
    monkeypatch.setattr(
        runner,
        "_fuel_refill_station",
        lambda _hwnd, _stop, resource, amount: (
            runner.refills.append((resource, amount)) or True
        ),
    )
    monkeypatch.setattr("core.runner_fuel.wm.show_window", lambda _hwnd: None)
    monkeypatch.setattr("core.runner_fuel.wm.activate_window", lambda _hwnd: True)
    monkeypatch.setattr("core.runner_fuel.time.sleep", lambda _seconds: None)

    runner._run_fuel_refill(123, threading.Event())

    assert runner.paths_run == ["To Gold"]
    assert runner.refills == [("gold_mine", "max")]
    assert runner.results == [("gold_mine", True)]


def test_manual_test_ignores_master_toggle_and_timer(monkeypatch):
    runner = DummyRunner(due=())
    runner.settings["enabled"] = False
    monkeypatch.setattr(
        runner,
        "_fuel_refill_station",
        lambda _hwnd, _stop, resource, amount: (
            runner.refills.append((resource, amount)) or True
        ),
    )
    monkeypatch.setattr("core.runner_fuel.wm.show_window", lambda _hwnd: None)
    monkeypatch.setattr("core.runner_fuel.wm.activate_window", lambda _hwnd: True)
    monkeypatch.setattr("core.runner_fuel.time.sleep", lambda _seconds: None)

    runner._run_fuel_refill(123, threading.Event(), force=True)

    assert runner.paths_run == ["To Drill", "Drill To Gold"]
    assert runner.results == [
        ("resource_drill", True),
        ("gold_mine", True),
    ]


def test_missing_first_combined_path_retries_resources_independently(monkeypatch):
    runner = DummyRunner()
    runner.settings["paths"]["hub_to_resource_drill"] = ""
    monkeypatch.setattr("core.runner_fuel.wm.show_window", lambda _hwnd: None)
    monkeypatch.setattr("core.runner_fuel.wm.activate_window", lambda _hwnd: True)
    monkeypatch.setattr("core.runner_fuel.time.sleep", lambda _seconds: None)

    runner._run_fuel_refill(123, threading.Event())

    assert runner.refills == []
    assert runner.results == [
        ("resource_drill", False),
        ("gold_mine", False),
    ]


def test_walk_path_opens_station_with_e_after_replay(monkeypatch):
    runner = DummyRunner(due=("resource_drill",))
    replayed = []
    monkeypatch.setattr(
        "core.runner_fuel.walk_paths.load_path",
        lambda _name: {"events": [{"t": 0.0, "key": "w", "state": "down"}]},
    )
    monkeypatch.setattr(
        "core.runner_fuel.walk_paths.replay_events",
        lambda events, keyboard, stop_event: replayed.extend(events),
    )
    monkeypatch.setattr("core.runner_fuel.time.sleep", lambda _seconds: None)

    assert FuelOps._fuel_run_path(
        runner, "Recorded Route", threading.Event()) is True
    assert replayed
    assert runner._keyboard.taps == [ord("E")]
    assert runner.statuses[-1]["action"] == "Opening the fuel station..."


def test_numeric_refill_selects_input_twice_and_confirms(monkeypatch):
    runner = DummyRunner(due=("resource_drill",))
    clicks = []
    double_clicks = []
    matches = iter((
        {"cx": 10, "cy": 20},
        {"cx": 30, "cy": 40},
        {"cx": 50, "cy": 60},
        None,
    ))
    monkeypatch.setattr(runner, "_fuel_wait_for", lambda *_args, **_kwargs: next(matches))
    monkeypatch.setattr(runner, "_wait_for_image_gone", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "core.runner_fuel.vision.click_match",
        lambda _mouse, _hwnd, match: clicks.append(match),
    )
    monkeypatch.setattr(
        "core.runner_fuel.vision.double_click_match",
        lambda _mouse, _hwnd, match: double_clicks.append(match),
    )
    monkeypatch.setattr("core.runner_fuel.time.sleep", lambda _seconds: None)

    assert runner._fuel_refill_station(
        123, threading.Event(), "resource_drill", 37) is True
    assert len(double_clicks) == 1
    assert runner._keyboard.combos == [(keys.VK_CONTROL, ord("A"))]
    assert runner._keyboard.taps == [keys.VK_DELETE]
    assert runner._keyboard.typed == ["37"]
    assert len(clicks) == 2


def test_confirmation_must_disappear_before_timer_success(monkeypatch):
    runner = DummyRunner(due=("gold_mine",))
    monkeypatch.setattr(
        runner,
        "_fuel_wait_for",
        lambda *_args, **_kwargs: {"cx": 10, "cy": 20},
    )
    monkeypatch.setattr(runner, "_wait_for_image_gone", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("core.runner_fuel.vision.click_match", lambda *_args: None)
    monkeypatch.setattr("core.runner_fuel.time.sleep", lambda _seconds: None)

    assert runner._fuel_refill_station(
        123, threading.Event(), "gold_mine", "max") is False


def test_success_closes_station_hud_when_close_button_is_found(monkeypatch):
    runner = DummyRunner(due=("gold_mine",))
    clicks = []
    matches = iter((
        {"name": "fuel_add"},
        {"name": "fuel_max"},
        {"name": "fuel_confirm"},
        {"name": "nav_closeui"},
    ))
    monkeypatch.setattr(
        runner, "_fuel_wait_for", lambda *_args, **_kwargs: next(matches))
    monkeypatch.setattr(
        runner, "_wait_for_image_gone", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "core.runner_fuel.vision.click_match",
        lambda _mouse, _hwnd, match: clicks.append(match["name"]),
    )
    monkeypatch.setattr("core.runner_fuel.time.sleep", lambda _seconds: None)

    assert runner._fuel_refill_station(
        123, threading.Event(), "gold_mine", "max") is True
    assert clicks == [
        "fuel_add",
        "fuel_max",
        "fuel_confirm",
        "nav_closeui",
    ]
    assert "[Fuel] Gold Mine station HUD closed." in runner.logs


def test_refill_spaces_clicks_and_tolerates_delayed_server_ui(monkeypatch):
    runner = DummyRunner(due=("gold_mine",))
    waits = []
    sleeps = []

    def wait_for(hwnd, name, timeout, stop_event):
        waits.append((name, timeout))
        if name == "nav_closeui":
            return None
        return {"name": name}

    def wait_until_gone(hwnd, names, timeout, stop_event):
        waits.append((names[0], timeout))
        return True

    monkeypatch.setattr(runner, "_fuel_wait_for", wait_for)
    monkeypatch.setattr(runner, "_wait_for_image_gone", wait_until_gone)
    monkeypatch.setattr(
        "core.runner_fuel.vision.click_match", lambda *_args: None)
    monkeypatch.setattr(
        "core.runner_fuel.time.sleep", lambda seconds: sleeps.append(seconds))

    assert runner._fuel_refill_station(
        123, threading.Event(), "gold_mine", "max") is True
    assert sleeps == [
        FUEL_CLICK_DELAY,
        FUEL_CLICK_DELAY,
        FUEL_CLICK_DELAY,
    ]
    assert waits[:3] == [
        ("fuel_add", FUEL_ACTION_TIMEOUT),
        ("fuel_max", FUEL_ACTION_TIMEOUT),
        ("fuel_confirm", FUEL_ACTION_TIMEOUT),
    ]
    assert waits[3] == ("fuel_confirm", FUEL_CONFIRM_TIMEOUT)


def test_manual_test_idle_status_clears_all_fuel_context():
    runner = DummyRunner()

    runner._fuel_set_idle_status()

    assert runner.statuses[-1] == {
        "current_task": "-",
        "current_repeat": "-",
        "map": "-",
        "action": "Idle",
        "mode": "-",
        "stage": "-",
        "difficulty": "-",
        "play_mode": "-",
        "macro": "-",
    }
