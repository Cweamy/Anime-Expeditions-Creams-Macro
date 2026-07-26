import logging
from logging.handlers import RotatingFileHandler
import os
import time

from . import constants

LOG_FILE = os.path.join(constants.APP_DIR, "debug.log")
MAX_LOG_SIZE = 5 * 1024 * 1024  # 5 MB per log file
BACKUP_COUNT = 3  # Keep up to 3 backup files (debug.log.1, debug.log.2, etc.)


class Logger:
    """Writes to debug.log. Adds timestamps to debug logs. GUI logs format
    the message alone since barely anyone reads the per-line clock there."""

    def __init__(self):
        # Rotation handler for log file management
        self._handler = None

    def _get_handler(self) -> RotatingFileHandler | None:
        """Obtains or creates the RotatingFileHandler instance."""
        if self._handler is None:
            try:
                log_dir = os.path.dirname(LOG_FILE)
                if log_dir and not os.path.exists(log_dir):
                    os.makedirs(log_dir, exist_ok=True)

                self._handler = RotatingFileHandler(
                    LOG_FILE,
                    maxBytes=MAX_LOG_SIZE,
                    backupCount=BACKUP_COUNT,
                    encoding="utf-8",
                )
            except OSError:
                self._handler = None
        return self._handler

    def log(self, message: str) -> None:
        """Logs a timestamp-formatted message to the rotating log file."""
        handler = self._get_handler()
        if handler is not None:
            try:
                timestamp = time.strftime("%H:%M:%S")
                line = f"[{timestamp}] {message}"
                record = logging.LogRecord(
                    name="logging",
                    level=logging.INFO,
                    pathname="",
                    lineno=0,
                    msg=line,
                    args=(),
                    exc_info=None,
                )
                handler.emit(record)
            except OSError:
                self.close()

    def close(self) -> None:
        """Closes the file handler and releases resources."""
        if self._handler is not None:
            try:
                self._handler.close()
            except OSError:
                pass
            self._handler = None
