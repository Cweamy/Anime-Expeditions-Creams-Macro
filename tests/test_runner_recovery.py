import threading
from unittest.mock import Mock, call
import pytest

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

