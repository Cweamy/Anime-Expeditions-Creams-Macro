import os
import time

LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "debug.log")


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
