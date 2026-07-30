import threading
from unittest.mock import MagicMock

from core.runner import MacroRunner


def test_due_fuel_between_repeats_restores_task_dashboard_context(monkeypatch):
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
    monkeypatch.setattr(runner, "_crafting_wants_in", lambda *_a, **_k: False)
    monkeypatch.setattr(runner, "_fuel_wants_in", lambda: True)

    def run_fuel(*_args, **_kwargs):
        runner._current_hwnd = 456
        runner._set_status(
            current_task="Auto Fuel",
            action="Preparing Auto Fuel...",
            mode="fuel",
            map="-",
            stage="-",
            difficulty="-",
            play_mode="-",
            macro="-",
        )

    monkeypatch.setattr(runner, "_run_fuel_refill", run_fuel)
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
    auto_fuel_index = next(
        index for index, status in enumerate(statuses)
        if status.get("current_task") == "Auto Fuel"
    )
    restored = next(
        status for status in statuses[auto_fuel_index + 1:]
        if status.get("action") == "Resuming after Auto Fuel..."
    )
    assert restored["current_task"] == "1 / 1"
    assert restored["current_repeat"] == "2 / 2"
    assert restored["map"] == "Flower Forest"
    assert restored["mode"] == "story"
    assert setup_hwnds == [123, 456]
    assert any("[Macro] Auto Fuel is due." in message for message in logs)
    assert any("[Macro] Auto Fuel pass finished." in message for message in logs)
