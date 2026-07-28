"""
Tests for the diagnostics module.
"""

import pytest
from dataclasses import FrozenInstanceError
from core.diagnostics import (
    FailureCategory,
    RecoveryAction,
    FailureReport,
    create_failure_report
)

def test_failure_report_instantiation():
    """Test that a FailureReport can be instantiated and attributes are correct."""
    report = create_failure_report(
        code="TEST_ERR_01",
        category=FailureCategory.ENVIRONMENT,
        phase="INITIALIZATION",
        retryable=False,
        recovery_action=RecoveryAction.STOP_RUNNER,
        user_message="Test failure",
        user_action="Check logs",
        details={"info": "test"}
    )
    
    assert report.code == "TEST_ERR_01"
    assert report.category == FailureCategory.ENVIRONMENT
    assert report.phase == "INITIALIZATION"
    assert not report.retryable
    assert report.recovery_action == RecoveryAction.STOP_RUNNER
    assert report.user_message == "Test failure"
    assert report.user_action == "Check logs"
    assert report.details == {"info": "test"}

def test_failure_report_immutability():
    """Test that FailureReport is immutable."""
    report = create_failure_report(
        code="TEST_ERR_02",
        category=FailureCategory.INTERNAL,
        phase="EXECUTION",
        retryable=True,
        recovery_action=RecoveryAction.RETRY_STEP,
        user_message="Internal error",
        user_action="Wait and retry"
    )
    
    with pytest.raises(FrozenInstanceError):
        report.code = "NEW_CODE"

def test_failure_report_to_dict():
    """Test the to_dict serialization method."""
    report = create_failure_report(
        code="TEST_ERR_03",
        category=FailureCategory.USER_CONFIG,
        phase="VALIDATION",
        retryable=False,
        recovery_action=RecoveryAction.STOP_TASK,
        user_message="Invalid config",
        user_action="Fix config file",
        details={"key": "value"}
    )
    
    report_dict = report.to_dict()
    
    assert report_dict == {
        "code": "TEST_ERR_03",
        "category": "USER_CONFIG",
        "phase": "VALIDATION",
        "retryable": False,
        "recovery_action": "STOP_TASK",
        "user_message": "Invalid config",
        "user_action": "Fix config file",
        "details": {"key": "value"}
    }
