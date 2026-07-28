"""
Diagnostic module for Creams Macro.
Provides structured failure reporting and recovery actions.
"""

from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Dict, Any, Optional

class FailureCategory(Enum):
    USER_CONFIG = auto()
    ENVIRONMENT = auto()
    GAME_STATE = auto()
    TRANSIENT_VISUAL = auto()
    INTERNAL = auto()

class RecoveryAction(Enum):
    RETRY_STEP = auto()
    RETRY_PHASE = auto()
    RETURN_TO_LOBBY = auto()
    STOP_TASK = auto()
    STOP_RUNNER = auto()

@dataclass(frozen=True)
class FailureReport:
    code: str
    category: FailureCategory
    phase: str
    retryable: bool
    recovery_action: RecoveryAction
    user_message: str
    user_action: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert the report to a dictionary."""
        return {
            "code": self.code,
            "category": self.category.name,
            "phase": self.phase,
            "retryable": self.retryable,
            "recovery_action": self.recovery_action.name,
            "user_message": self.user_message,
            "user_action": self.user_action,
            "details": self.details,
        }

def create_failure_report(
    code: str,
    category: FailureCategory,
    phase: str,
    retryable: bool,
    recovery_action: RecoveryAction,
    user_message: str,
    user_action: str,
    details: Optional[Dict[str, Any]] = None
) -> FailureReport:
    """Helper function to create a FailureReport."""
    return FailureReport(
        code=code,
        category=category,
        phase=phase,
        retryable=retryable,
        recovery_action=recovery_action,
        user_message=user_message,
        user_action=user_action,
        details=details or {}
    )
