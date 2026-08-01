import time
from unittest.mock import Mock

from core import runner as runner_module
from core.runner import MacroRunner


def test_memory_refresh_is_disabled_by_default():
    runner = MacroRunner(Mock(), Mock(), Mock())

    assert runner._memory_refresh_due() is False
    assert runner._memory_refresh_next_at is None


def test_memory_refresh_due_uses_monotonic_deadline_and_rearms():
    runner = MacroRunner(Mock(), Mock(), Mock())
    runner._memory_refresh_enabled = True
    runner._memory_refresh_interval_seconds = 3600.0
    runner._memory_refresh_next_at = time.monotonic() - 1

    assert runner._memory_refresh_due() is True

    runner._complete_memory_refresh()

    assert runner._memory_refresh_due() is False
    assert runner._memory_refresh_next_at > time.monotonic()


def test_due_memory_refresh_rejoins_between_matches_not_during_one(monkeypatch):
    runner = MacroRunner(Mock(), Mock(), Mock())
    runner._stop_event = runner_module.threading.Event()
    runner._memory_refresh_enabled = True
    runner._memory_refresh_interval_seconds = 3600.0
    runner._memory_refresh_next_at = time.monotonic() - 1
    runner._run_task_setup = Mock(return_value=True)
    runner._play_one_match = Mock(return_value="win")
    runner._handle_match_result = Mock(return_value=True)
    runner._attempt_rejoin = Mock(return_value=True)
    runner._checkpoint = Mock(return_value=False)
    runner._challenge_has_ready_stage = Mock(return_value=False)
    runner._crafting_wants_in = Mock(return_value=False)
    runner._fuel_wants_in = Mock(return_value=False)
    runner._auto_shop_wants_in = Mock(return_value=False)
    runner._current_hwnd = 123
    monkeypatch.setattr(runner_module.wm, "is_window", lambda _hwnd: True)

    completed = runner._run_task(
        123,
        runner._stop_event,
        {"map": "Forest", "mode": "story", "repeat": 2},
        1,
        1,
        {},
        None,
        None,
        {},
        {},
    )

    assert completed is True
    runner._attempt_rejoin.assert_called_once_with(123, runner._stop_event)
    assert runner._play_one_match.call_count == 2
    assert runner._run_task_setup.call_count == 2
    first_result = runner._handle_match_result.call_args_list[0]
    assert first_result.kwargs["repeat"] is False

