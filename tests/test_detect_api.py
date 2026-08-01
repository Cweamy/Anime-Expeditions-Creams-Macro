import base64

import cv2
import numpy as np

import main


def test_debug_test_detect_captures_one_full_frame_and_returns_preview(monkeypatch):
    api = main.Api.__new__(main.Api)
    api.game_hwnd = 123
    logs = []
    api.push_log = logs.append
    api.show_game = lambda: None
    api.hide_game = lambda: None
    frame = np.zeros((12, 16, 3), dtype=np.uint8)
    captured = []
    report = {
        "found": False,
        "region": (2, 3, 6, 5),
        "details": [],
    }

    monkeypatch.setattr(main.wm, "is_window", lambda hwnd: hwnd == 123)

    from core import detect, vision
    monkeypatch.setattr(vision, "capture_game_bgr", lambda hwnd, region=None: captured.append(
        (hwnd, region)) or frame)
    monkeypatch.setattr(detect, "diagnose_frame", lambda captured_frame, block: (
        report if captured_frame is frame and block["image"] == "Defense" else None))
    monkeypatch.setattr(detect, "render_diagnostic", lambda captured_frame, _report: captured_frame)

    result = api.debug_test_detect({"mode": "single", "image": "Defense"})

    assert result["ok"] is True
    assert result["found"] is False
    assert result["region"] == {"x": 2, "y": 3, "w": 6, "h": 5}
    assert result["width"] == 16 and result["height"] == 12
    assert result["data_uri"].startswith("data:image/png;base64,")
    assert captured == [(123, None)]
    assert logs and "Detect test: not found" in logs[-1]
    assert cv2.imdecode(np.frombuffer(base64.b64decode(
        result["data_uri"].split(",", 1)[1]), dtype=np.uint8), cv2.IMREAD_COLOR).shape[:2] == (12, 16)


def test_debug_test_detect_temporarily_shows_hidden_roblox(monkeypatch):
    api = main.Api.__new__(main.Api)
    api.game_hwnd = 123
    api.push_log = lambda _message: None
    events = []
    api.show_game = lambda: events.append("show")
    api.hide_game = lambda: events.append("hide")
    frame = np.zeros((4, 6, 3), dtype=np.uint8)

    monkeypatch.setattr(main.wm, "is_window", lambda _hwnd: True)
    monkeypatch.setattr(main.wm, "is_window_visible", lambda _hwnd: False)
    from core import detect, vision
    monkeypatch.setattr(vision, "capture_game_bgr", lambda _hwnd, region=None: frame)
    monkeypatch.setattr(detect, "diagnose_frame", lambda _frame, _block: {
        "found": True, "region": None, "details": [],
    })
    monkeypatch.setattr(detect, "render_diagnostic", lambda _frame, _report: frame)

    result = api.debug_test_detect({"mode": "single", "image": "nav_play"})

    assert result["ok"] is True
    assert events == ["show", "hide"]
