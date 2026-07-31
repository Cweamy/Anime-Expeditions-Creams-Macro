import os

import cv2
import numpy as np

from core import vision


def _match():
    return {"x": 4, "y": 5, "w": 12, "h": 10, "cx": 10, "cy": 10, "score": 0.95}


def test_match_debug_can_draw_on_a_readonly_exact_size_capture(monkeypatch, tmp_path):
    raw = bytes(np.zeros((32, 40, 3), dtype=np.uint8))
    readonly = np.frombuffer(raw, dtype=np.uint8).reshape(32, 40, 3)
    assert readonly.flags.writeable is False

    monkeypatch.setattr(vision, "DEBUG_DIR", str(tmp_path))
    monkeypatch.setattr(vision, "capture_game_bgr", lambda _hwnd: readonly)

    path = vision.save_match_debug(123, "event_act", _match())

    assert path == os.path.join(str(tmp_path), "vision_event_act.png")
    rendered = cv2.imread(path)
    assert rendered is not None
    assert np.any(rendered[:, :, 1] > 0), "the green match rectangle was not drawn"


def test_match_debug_failure_is_logged_and_does_not_escape(monkeypatch, tmp_path):
    logs = []
    monkeypatch.setattr(vision, "DEBUG_DIR", str(tmp_path))
    monkeypatch.setattr(
        vision, "capture_game_bgr",
        lambda _hwnd: (_ for _ in ()).throw(RuntimeError("capture failed")))

    result = vision.save_match_debug(123, "event_act", _match(), log=logs.append)

    assert result is None
    assert len(logs) == 1
    assert "capture failed" in logs[0]
    assert "continuing" in logs[0]


def test_match_debug_write_failure_is_best_effort(monkeypatch, tmp_path):
    logs = []
    monkeypatch.setattr(vision, "DEBUG_DIR", str(tmp_path))
    monkeypatch.setattr(
        vision, "capture_game_bgr",
        lambda _hwnd: np.zeros((32, 40, 3), dtype=np.uint8))
    monkeypatch.setattr(vision.cv2, "imwrite", lambda *_args: False)

    result = vision.save_match_debug(123, "event_act", _match(), log=logs.append)

    assert result is None
    assert len(logs) == 1
    assert "could not write" in logs[0]
