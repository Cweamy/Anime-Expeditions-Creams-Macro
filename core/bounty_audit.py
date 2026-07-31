"""Portable, opt-in diagnostics for Auto Bounty live replays.

The audit is deliberately separate from ``debug.log``.  A run gets its own
folder containing JSONL events and the relevant captured frames, so a report
from another computer can be inspected without reproducing the screen state.
"""
from __future__ import annotations

import json
import os
import platform
import re
import sys
import threading
import time
from datetime import datetime

import cv2
import numpy as np

from . import constants


def _json_default(value):
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, tuple):
        return list(value)
    return str(value)


def _safe_label(value: str) -> str:
    label = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._")
    return label[:80] or "frame"


class BountyAudit:
    """Write a self-contained bounty trace beside the executable."""

    def __init__(self):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.root = os.path.join(
            constants.APP_DIR, "audit", "bounty", f"{stamp}_{os.getpid()}")
        self.frames_dir = os.path.join(self.root, "frames")
        os.makedirs(self.frames_dir, exist_ok=True)
        self.events_path = os.path.join(self.root, "events.jsonl")
        self._lock = threading.RLock()
        self._frame_number = 0
        self._closed = False
        self._events = open(
            self.events_path, "a", encoding="utf-8", buffering=1)
        self.event(
            "audit_started",
            created_at=datetime.now().astimezone().isoformat(),
            pid=os.getpid(),
            executable=sys.executable,
            argv=sys.argv,
            cwd=os.getcwd(),
            platform=platform.platform(),
            python=platform.python_version(),
            app_dir=constants.APP_DIR,
        )
        with open(os.path.join(self.root, "README.txt"), "w", encoding="utf-8") as readme:
            readme.write(
                "Auto Bounty audit trace\n"
                "========================\n"
                "events.jsonl contains timestamped detections, OCR summaries,\n"
                "match confidence, reference/screen coordinates, mouse paths,\n"
                "scroll verification, and frame filenames.\n"
                "frames/ contains the captured Roblox board states.\n"
            )

    def event(self, name: str, **data) -> None:
        with self._lock:
            if self._closed:
                return
            record = {
                "time": datetime.now().astimezone().isoformat(),
                "monotonic": time.monotonic(),
                "event": name,
                **data,
            }
            self._events.write(json.dumps(record, default=_json_default) + "\n")

    def frame(self, label: str, frame, **data) -> str | None:
        with self._lock:
            if self._closed:
                return None
            if frame is None or not hasattr(frame, "shape") or frame.size == 0:
                self.event("frame_unavailable", label=label, **data)
                return None
            self._frame_number += 1
            filename = f"{self._frame_number:05d}_{_safe_label(label)}.jpg"
            path = os.path.join(self.frames_dir, filename)
            image = np.asarray(frame)
            if not cv2.imwrite(path, image, [cv2.IMWRITE_JPEG_QUALITY, 92]):
                self.event("frame_write_failed", label=label, filename=filename, **data)
                return None
            self.event(
                "frame",
                label=label,
                filename=os.path.join("frames", filename),
                shape=list(image.shape),
                **data,
            )
            return path

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self.event("audit_finished")
            self._events.close()
            self._closed = True

