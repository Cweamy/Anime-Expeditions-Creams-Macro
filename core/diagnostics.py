"""
Diagnostic module for Creams Macro.
Provides structured failure reporting and recovery actions.
"""

from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Dict, Any, Optional
import os
import re
import json
import time
import shutil
from core import constants

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

def redact_sensitive_info(text: str) -> str:
    """
    Redacts sensitive information from text.
    Redacts Windows user profile paths and Discord webhook URLs.
    """
    if not text:
        return text

    # Redact Windows profile paths (C:\Users\Username\...) -> C:\Users\<REDACTED>\...
    # Handle single backslash, double backslash (from JSON), and forward slashes
    text = re.sub(
        r'(?i)([a-z]:(?:\\\\?|/)Users(?:\\\\?|/))[^\\/]+((?:\\\\?|/))',
        r'\g<1><REDACTED>\g<2>',
        text
    )

    # Redact Discord webhooks
    text = re.sub(
        r'(?i)(https?://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/)\d+/[A-Za-z0-9_-]+',
        r'\g<1><REDACTED>',
        text
    )
    return text

def save_failure_snapshot(report: FailureReport, screenshot_bgr=None, log_tail: Optional[str] = None) -> str:
    """
    Saves a failure snapshot directory with report, screenshot, and sanitized logs.
    Maintains a maximum of 10 historical snapshots.
    """
    import cv2
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    snapshot_dir = os.path.join(constants.APP_DIR, "debug", "failures", f"{timestamp}_{report.code}")
    os.makedirs(snapshot_dir, exist_ok=True)

    with open(os.path.join(snapshot_dir, "failure.json"), "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, indent=2)

    if screenshot_bgr is not None:
        cv2.imwrite(os.path.join(snapshot_dir, "screenshot.png"), screenshot_bgr)

    if log_tail is not None:
        sanitized_log = redact_sensitive_info(log_tail)
        with open(os.path.join(snapshot_dir, "recent_logs.txt"), "w", encoding="utf-8") as f:
            f.write(sanitized_log)

    # Cleanup oldest snapshots (keep 10)
    failures_dir = os.path.join(constants.APP_DIR, "debug", "failures")
    try:
        snapshots = []
        for name in os.listdir(failures_dir):
            path = os.path.join(failures_dir, name)
            if os.path.isdir(path):
                snapshots.append(path)
        snapshots.sort(key=os.path.getmtime)
        while len(snapshots) > 10:
            oldest = snapshots.pop(0)
            shutil.rmtree(oldest, ignore_errors=True)
    except OSError:
        pass

    return snapshot_dir
