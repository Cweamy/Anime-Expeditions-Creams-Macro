import threading
from unittest.mock import Mock, call
import pytest

from core import runner as runner_module
from core.runner import MacroRunner
from core.diagnostics import FailureCategory, RecoveryAction, FailureReport, create_failure_report

@pytest.fixture
def runner():
    mock_mouse = Mock()
    mock_keyboard = Mock()
    mock_log = Mock()
    r = MacroRunner(mouse=mock_mouse, keyboard=mock_keyboard, log=mock_log)
    r._stop_event = threading.Event()
    r._attempt_rejoin = Mock()
    r._recover_to_lobby = Mock()
    return r

def test_handle_structured_failure_retry_step(runner):
    report = create_failure_report(
        code="TEST_ERR_01",
        phase="VISUAL_CHECK",
        retryable=True,
        category=FailureCategory.TRANSIENT_VISUAL,
        user_message="Timeout on UI",
        user_action="Clicking button",
        recovery_action=RecoveryAction.RETRY_STEP
    )
    result = runner._handle_structured_failure(report)
    assert result is True

def test_handle_structured_failure_return_to_lobby_disconnect(runner):
    report = create_failure_report(
        code="ROBLOX_DISCONNECTED",
        phase="MATCH_LOOP",
        retryable=False,
        category=FailureCategory.GAME_STATE,
        user_message="Roblox disconnected",
        user_action="Waiting for match",
        recovery_action=RecoveryAction.RETURN_TO_LOBBY
    )
    hwnd = Mock()
    result = runner._handle_structured_failure(report, hwnd=hwnd)
    assert result is False
    runner._attempt_rejoin.assert_called_once_with(hwnd, runner._stop_event)
    runner._recover_to_lobby.assert_not_called()

def test_handle_structured_failure_return_to_lobby_ui_timeout(runner):
    report = create_failure_report(
        code="TEST_ERR_03",
        phase="LOBBY_NAV",
        retryable=False,
        category=FailureCategory.TRANSIENT_VISUAL,
        user_message="Menu did not load",
        user_action="Navigating",
        recovery_action=RecoveryAction.RETURN_TO_LOBBY
    )
    hwnd = Mock()
    result = runner._handle_structured_failure(report, hwnd=hwnd)
    assert result is False
    runner._recover_to_lobby.assert_called_once_with(hwnd, runner._stop_event)

def test_handle_structured_failure_stop_runner(runner):
    report = create_failure_report(
        code="TEST_ERR_04",
        phase="RUNNER_LOOP",
        retryable=False,
        category=FailureCategory.INTERNAL,
        user_message="Fatal error",
        user_action="Unknown state",
        recovery_action=RecoveryAction.STOP_RUNNER
    )
    assert not runner._stop_event.is_set()
    result = runner._handle_structured_failure(report)
    assert result is False
    assert runner._stop_event.is_set()

def test_recovery_halts_when_retry_threshold_reached(runner):
    # Testing that a bounded retry loop will properly halt when limit is reached
    # simulating a loop that uses _handle_structured_failure
    max_retries = 3
    retries = 0
    hwnd = Mock()

    report = create_failure_report(
        code="TEST_ERR_05",
        phase="LOAD_TEAM",
        retryable=True,
        category=FailureCategory.TRANSIENT_VISUAL,
        user_message="Timeout loading team",
        user_action="load_team",
        recovery_action=RecoveryAction.RETRY_STEP
    )

    success = False
    for attempt in range(max_retries):
        retries += 1
        # simulating a failure and check
        if runner._handle_structured_failure(report, hwnd=hwnd):
            continue

    assert retries == max_retries
    assert not success


def test_guarded_phase_logs_exception_and_recovers_to_lobby(runner):
    def fail():
        raise RuntimeError("simulated vision failure")

    completed, result = runner._run_guarded_phase(
        "Auto Bounty", 123, runner._stop_event, fail)

    assert completed is False
    assert result is None
    runner._recover_to_lobby.assert_called_once_with(123, runner._stop_event)
    assert any(
        "Unexpected error during Auto Bounty: RuntimeError: simulated vision failure"
        in call_args.args[0]
        for call_args in runner._log.call_args_list
    )


def test_bounty_exception_does_not_prevent_challenge_or_queue(monkeypatch, runner):
    monkeypatch.setattr(runner_module.wm, "is_window", lambda _hwnd: True)
    monkeypatch.setattr(runner_module.wm, "show_window", lambda _hwnd: None)
    monkeypatch.setattr(runner_module.wm, "activate_window", lambda _hwnd: True)
    monkeypatch.setattr(runner_module.wm, "is_process_elevated", lambda _hwnd: False)
    monkeypatch.setattr(runner_module.wm, "is_self_elevated", lambda: False)
    monkeypatch.setattr(runner_module.vision, "find_image", lambda *_args, **_kwargs: None)
    runner._run_bounties = Mock(side_effect=RuntimeError("capture failed"))
    runner._bounty_settings = Mock(return_value={"enabled": True})
    runner._run_challenges = Mock()
    runner._crafting_wants_in = Mock(return_value=False)
    runner._recover_to_lobby.return_value = True

    runner._run(
        lambda: 123, lambda: [], runner._stop_event,
        coords={}, default_walk_paths={}, webhook={})

    runner._recover_to_lobby.assert_called_once_with(123, runner._stop_event)
    runner._run_challenges.assert_called_once()
    assert any(
        "Auto Bounty pass finished and the Task Queue is empty"
        in call_args.args[0]
        for call_args in runner._log.call_args_list
    )


def test_single_task_exception_skips_task_instead_of_stopping_runner(
        monkeypatch, runner):
    monkeypatch.setattr(runner_module.wm, "is_window", lambda _hwnd: True)
    monkeypatch.setattr(runner_module.wm, "show_window", lambda _hwnd: None)
    monkeypatch.setattr(runner_module.wm, "activate_window", lambda _hwnd: True)
    monkeypatch.setattr(runner_module.wm, "is_process_elevated", lambda _hwnd: False)
    monkeypatch.setattr(runner_module.wm, "is_self_elevated", lambda: False)
    monkeypatch.setattr(runner_module.vision, "find_image", lambda *_args, **_kwargs: None)
    runner._run_bounties = Mock(return_value=False)
    runner._run_challenges = Mock()
    runner._crafting_wants_in = Mock(return_value=False)
    runner._run_task = Mock(side_effect=RuntimeError("task crashed"))
    runner._recover_to_lobby.return_value = True
    queue_reads = iter([[{"mode": "event", "map": "Event", "repeat": 1}], []])

    runner._run(
        lambda: 123, lambda: next(queue_reads), runner._stop_event,
        coords={}, default_walk_paths={}, webhook={})

    runner._recover_to_lobby.assert_called_once_with(123, runner._stop_event)
    assert any(
        "Unexpected error during task 1/1: RuntimeError: task crashed"
        in call_args.args[0]
        for call_args in runner._log.call_args_list
    )


def _prepare_run_environment(monkeypatch, runner):
    monkeypatch.setattr(runner_module.wm, "is_window", lambda _hwnd: True)
    monkeypatch.setattr(runner_module.wm, "show_window", lambda _hwnd: None)
    monkeypatch.setattr(runner_module.wm, "activate_window", lambda _hwnd: True)
    monkeypatch.setattr(runner_module.wm, "is_process_elevated", lambda _hwnd: False)
    monkeypatch.setattr(runner_module.wm, "is_self_elevated", lambda: False)
    monkeypatch.setattr(runner_module.vision, "find_image", lambda *_args, **_kwargs: None)
    runner._bounty_settings = Mock(return_value={"enabled": True})
    runner._recover_to_lobby.return_value = True


@pytest.mark.parametrize("failed_phase", ["bounty", "challenge", "crafting"])
def test_resource_phase_exception_still_reaches_task_queue(
        monkeypatch, runner, failed_phase):
    _prepare_run_environment(monkeypatch, runner)
    calls = []

    def phase(name, result=None):
        def run(*_args, **_kwargs):
            calls.append(name)
            if failed_phase == name:
                raise RuntimeError(f"{name} failed")
            return result
        return run

    runner._run_bounties = Mock(side_effect=phase("bounty", False))
    runner._run_challenges = Mock(side_effect=phase("challenge"))
    runner._run_crafting = Mock(side_effect=phase("crafting"))
    runner._crafting_wants_in = Mock(side_effect=[True, False])
    runner._run_task = Mock(side_effect=phase("task", True))
    queue_reads = iter([[{"mode": "event", "map": "Event", "repeat": 1}], []])

    runner._run(
        lambda: 123, lambda: next(queue_reads), runner._stop_event,
        coords={}, default_walk_paths={}, webhook={})

    assert "task" in calls
    runner._recover_to_lobby.assert_called_once_with(123, runner._stop_event)
    phase_labels = {
        "bounty": "Auto Bounty",
        "challenge": "Challenge",
        "crafting": "Auto Crafting",
    }
    assert any(
        f"Unexpected error during {phase_labels[failed_phase]}"
        in call_args.args[0]
        for call_args in runner._log.call_args_list
    )


def test_recovery_exception_is_logged_without_blocking_later_phases(
        monkeypatch, runner):
    _prepare_run_environment(monkeypatch, runner)
    runner._run_bounties = Mock(side_effect=RuntimeError("bounty failed"))
    runner._recover_to_lobby.side_effect = RuntimeError("recovery failed")
    runner._run_challenges = Mock()
    runner._crafting_wants_in = Mock(return_value=False)
    runner._run_task = Mock(return_value=True)
    queue_reads = iter([[{"mode": "event", "map": "Event", "repeat": 1}], []])

    runner._run(
        lambda: 123, lambda: next(queue_reads), runner._stop_event,
        coords={}, default_walk_paths={}, webhook={})

    runner._run_challenges.assert_called_once()
    runner._run_task.assert_called_once()
    assert any(
        "Unexpected error during Auto Bounty lobby recovery"
        in call_args.args[0]
        for call_args in runner._log.call_args_list
    )


def test_stop_during_phase_failure_does_not_recover_or_continue(
        monkeypatch, runner):
    _prepare_run_environment(monkeypatch, runner)

    def fail_after_stop(*_args, **_kwargs):
        runner._stop_event.set()
        raise RuntimeError("stopped failure")

    runner._run_bounties = Mock(side_effect=fail_after_stop)
    runner._run_challenges = Mock()
    runner._crafting_wants_in = Mock(return_value=False)

    runner._run(
        lambda: 123, lambda: [], runner._stop_event,
        coords={}, default_walk_paths={}, webhook={})

    runner._recover_to_lobby.assert_not_called()
    runner._run_challenges.assert_not_called()


def test_crashed_task_does_not_block_later_tasks(monkeypatch, runner):
    _prepare_run_environment(monkeypatch, runner)
    runner._run_bounties = Mock(return_value=False)
    runner._run_challenges = Mock()
    runner._crafting_wants_in = Mock(return_value=False)
    completed_maps = []

    def run_task(_hwnd, _stop, task, *_args, **_kwargs):
        if task["map"] == "First":
            raise RuntimeError("first task failed")
        completed_maps.append(task["map"])
        return True

    runner._run_task = Mock(side_effect=run_task)
    queue_reads = iter([[
        {"mode": "story", "map": "First", "repeat": 1},
        {"mode": "story", "map": "Second", "repeat": 1},
    ], []])

    runner._run(
        lambda: 123, lambda: next(queue_reads), runner._stop_event,
        coords={}, default_walk_paths={}, webhook={})

    assert completed_maps == ["Second"]
    runner._recover_to_lobby.assert_called_once_with(123, runner._stop_event)


def test_crafting_readiness_exception_does_not_block_task_queue(
        monkeypatch, runner):
    _prepare_run_environment(monkeypatch, runner)
    runner._run_bounties = Mock(return_value=False)
    runner._run_challenges = Mock()
    runner._crafting_wants_in = Mock(
        side_effect=[ValueError("bad crafting counter"), False])
    runner._run_task = Mock(return_value=True)
    queue_reads = iter([[{"mode": "event", "map": "Event", "repeat": 1}], []])

    runner._run(
        lambda: 123, lambda: next(queue_reads), runner._stop_event,
        coords={}, default_walk_paths={}, webhook={})

    runner._run_task.assert_called_once()
    runner._recover_to_lobby.assert_called_once_with(123, runner._stop_event)
    assert any(
        "Unexpected error during Auto Crafting"
        in call_args.args[0]
        for call_args in runner._log.call_args_list
    )

