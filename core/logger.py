import os
import time

from . import constants

LOG_FILE = os.path.join(constants.APP_DIR, "debug.log")


class Logger:
    """Full timestamped history goes to debug.log; the UI's Logs panel shows
    the message alone since barely anyone reads the per-line clock there."""

    def log(self, message: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {message}\n"
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass
