import threading
from unittest.mock import MagicMock

from core.runner import MacroRunner


def test_startup_resource_priority_places_auto_shop_after_auto_fuel(monkeypatch):
    phases = []
    runner = MacroRunner(MagicMock(), MagicMock(), MagicMock())
    stop_event = threading.Event()

    monkeypatch.setattr("core.runner.wm.is_window", lambda _hwnd: True)
    monkeypatch.setattr("core.runner.wm.show_window", lambda _hwnd: None)
    monkeypatch.setattr("core.runner.wm.activate_window", lambda _hwnd: True)
    monkeypatch.setattr("core.runner.wm.is_process_elevated", lambda _hwnd: False)
    monkeypatch.setattr("core.runner.vision.find_image", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_checkpoint", lambda _stop: False)
    monkeypatch.setattr(runner, "_run_bounties", lambda *_args: False)
    monkeypatch.setattr(runner, "_run_challenges", lambda *_args: None)
    monkeypatch.setattr(runner, "_run_crafting_if_due", lambda *_args: None)
    monkeypatch.setattr(runner, "_fuel_wants_in", lambda: True)
    monkeypatch.setattr(runner, "_run_fuel_refill", lambda *_args: None)
    monkeypatch.setattr(runner, "_auto_shop_wants_in", lambda: True)
    monkeypatch.setattr(runner, "_run_auto_shop", lambda *_args: None)

    def guarded(phase, _hwnd, _stop_event, action):
        phases.append(phase)
        return True, action()

    monkeypatch.setattr(runner, "_run_guarded_phase", guarded)

    runner._run(lambda: 1, lambda: [], stop_event)

    assert phases == [
        "Auto Bounty",
        "Challenge",
        "Auto Crafting",
        "Auto Fuel",
        "Auto Shop",
    ]


def test_due_auto_shop_between_repeats_restores_task_dashboard_context(monkeypatch):
    statuses = []
    logs = []
    setup_hwnds = []
    runner = MacroRunner(
        MagicMock(),
        MagicMock(),
        logs.append,
        set_status=lambda **fields: statuses.append(fields),
    )
    task = {
        "mode": "story",
        "map": "Flower Forest",
        "stage": "1",
        "difficulty": "normal",
        "play_mode": "solo",
        "macro": "Farm",
        "repeat": 2,
    }

    monkeypatch.setattr(runner, "_checkpoint", lambda _stop: False)
    monkeypatch.setattr(
        runner,
        "_run_task_setup",
        lambda hwnd, *_args: setup_hwnds.append(hwnd) or True,
    )
    monkeypatch.setattr(runner, "_play_one_match", lambda *_args, **_kwargs: "win")
    monkeypatch.setattr(runner, "_handle_match_result", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(runner, "_challenge_has_ready_stage", lambda: False)
    monkeypatch.setattr(runner, "_crafting_wants_in", lambda *_args: False)
    monkeypatch.setattr(runner, "_fuel_wants_in", lambda: False)
    monkeypatch.setattr(runner, "_auto_shop_wants_in", lambda: True)

    def run_shop(*_args, **_kwargs):
        runner._current_hwnd = 456
        runner._set_status(
            current_task="Auto Shop",
            action="Preparing Gold Shop...",
            mode="shop",
            map="-",
            stage="-",
            difficulty="-",
            play_mode="-",
            macro="-",
        )

    monkeypatch.setattr(runner, "_run_auto_shop", run_shop)
    monkeypatch.setattr("core.runner.wm.is_window", lambda hwnd: hwnd == 456)

    completed = runner._run_task(
        123,
        threading.Event(),
        task,
        1,
        1,
        {},
        3,
        8,
        {},
        {},
    )

    assert completed is True
    auto_shop_index = next(
        index for index, status in enumerate(statuses)
        if status.get("current_task") == "Auto Shop"
    )
    restored = next(
        status for status in statuses[auto_shop_index + 1:]
        if status.get("action") == "Resuming after Auto Shop..."
    )
    assert restored["current_task"] == "1 / 1"
    assert restored["current_repeat"] == "2 / 2"
    assert restored["map"] == "Flower Forest"
    assert restored["mode"] == "story"
    assert setup_hwnds == [123, 456]
    assert any("[Macro] Auto Shop is due." in message for message in logs)
    assert any("[Macro] Auto Shop pass finished." in message for message in logs)


def test_auto_shop_is_checked_again_after_a_completed_task(monkeypatch):
    phases = []
    auto_shop_checks = 0
    runner = MacroRunner(MagicMock(), MagicMock(), MagicMock())
    stop_event = threading.Event()
    task = {
        "mode": "story",
        "map": "Flower Forest",
        "stage": "1",
        "repeat": 1,
    }

    monkeypatch.setattr("core.runner.wm.is_window", lambda _hwnd: True)
    monkeypatch.setattr("core.runner.wm.show_window", lambda _hwnd: None)
    monkeypatch.setattr("core.runner.wm.activate_window", lambda _hwnd: True)
    monkeypatch.setattr("core.runner.wm.is_process_elevated", lambda _hwnd: False)
    monkeypatch.setattr("core.runner.vision.find_image", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_checkpoint", lambda _stop: stop_event.is_set())
    monkeypatch.setattr(runner, "_run_bounties", lambda *_args: False)
    monkeypatch.setattr(runner, "_run_challenges", lambda *_args: None)
    monkeypatch.setattr(runner, "_run_crafting_if_due", lambda *_args: None)
    monkeypatch.setattr(runner, "_fuel_wants_in", lambda: False)
    monkeypatch.setattr(runner, "_run_task", lambda *_args: True)

    def run_shop_if_due(*_args):
        nonlocal auto_shop_checks
        auto_shop_checks += 1
        if auto_shop_checks == 2:
            stop_event.set()

    monkeypatch.setattr(runner, "_run_auto_shop_if_due", run_shop_if_due)

    def guarded(phase, _hwnd, _stop_event, action):
        phases.append(phase)
        return True, action()

    monkeypatch.setattr(runner, "_run_guarded_phase", guarded)

    runner._run(lambda: 1, lambda: [task], stop_event)

    assert auto_shop_checks == 2
    assert phases[-3:] == ["Auto Crafting", "Auto Fuel", "Auto Shop"]
