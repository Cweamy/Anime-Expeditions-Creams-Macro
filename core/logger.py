import os
import time

from . import constants

LOG_FILE = os.path.join(constants.APP_DIR, "debug.log")


class Logger:
    """Full timestamped history goes to debug.log; the UI's Logs panel shows
    the message alone since barely anyone reads the per-line clock there."""

    def __init__(self):
        self._file = None

    def _get_file(self):
        if self._file is None or getattr(self._file, "closed", True):
            try:
                self._file = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
            except OSError:
                self._file = None
        return self._file

    def log(self, message: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {message}\n"
        f = self._get_file()
        if f is not None:
            try:
                f.write(line)
                f.flush()
            except OSError:
                self._file = None

    def close(self) -> None:
        if self._file is not None and not getattr(self._file, "closed", True):
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None
