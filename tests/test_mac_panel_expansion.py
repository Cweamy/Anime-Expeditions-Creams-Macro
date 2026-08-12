"""Regression coverage for the macOS "Macro Manager screen shows nothing" fix.

The bug was three macOS realities colliding:

1. OCR crops (reward preview/read, stats preview/read, the reward scrollbar
   probe) were plain screen-space grabs. When the panel expands it covers the
   game, so those grabs read the panel instead of Roblox. They now route
   through core.ocr.capture_region_from_window / sample_color_matches_window,
   which read the game window's own backing store.
2. set_panel_expanded refused to expand while a macro was running, so the
   Manager screen stayed in the narrow docked strip and looked empty.
3. showDocked re-collapsed the panel even when the user was on a non-Dashboard
   screen, so the strip never got the width its columns need.

These tests pin the first two (pure-Python) on a non-mac machine by faking
sys.platform; the JS re-assert is covered by test_ui_js.py.
"""
import numpy as np

import main
from core import ocr, vision


def test_capture_region_from_window_crops_the_backing_store(monkeypatch):
    store = np.zeros((200, 300, 3), dtype=np.uint8)
    store[50:60, 40:50] = (10, 20, 30)

    def fake_capture(hwnd, region):
        x, y, w, h = region
        return store[y:y + h, x:x + w]

    monkeypatch.setattr(vision, "capture_window_region_bgr", fake_capture)

    result = ocr.capture_region_from_window(123, 40, 50, 10, 10)
    assert result.shape == (10, 10, 3)
    assert result[0, 0].tolist() == [10, 20, 30]


def test_capture_region_from_window_clamps_zero_sized_crops(monkeypatch):
    """The screen-space twin floors dims at 1x1 (capture_region's
    max(1, ...)); the window twin must do the same so a drifted/zero-sized
    settings region averages a real pixel instead of an empty array
    (NaN -> False in the color probe)."""
    seen = {}

    def fake_capture(hwnd, region):
        seen["region"] = region
        return np.zeros((1, 1, 3), dtype=np.uint8)

    monkeypatch.setattr(vision, "capture_window_region_bgr", fake_capture)
    ocr.capture_region_from_window(123, 0, 0, 0, -5)
    assert seen["region"] == (0, 0, 1, 1)


def test_sample_color_matches_window_is_false_when_capture_fails(monkeypatch):
    monkeypatch.setattr(vision, "capture_window_region_bgr", lambda hwnd, region: None)
    assert ocr.sample_color_matches_window(123, 0, 0, 10, 10, 0xFFFFFF) is False


def test_sample_color_matches_window_averages_the_patch(monkeypatch):
    patch = np.full((5, 5, 3), (0, 0, 255), dtype=np.uint8)  # BGR red
    monkeypatch.setattr(
        vision, "capture_window_region_bgr", lambda hwnd, region: patch
    )
    # expected_rgb_hex is 0xRRGGBB -> red = 0xFF0000
    assert bool(ocr.sample_color_matches_window(123, 0, 0, 5, 5, 0xFF0000, tolerance=1)) is True
    assert bool(ocr.sample_color_matches_window(123, 0, 0, 5, 5, 0x0000FF, tolerance=1)) is False


def test_capture_game_region_uses_window_store_on_darwin(monkeypatch):
    monkeypatch.setattr(main.sys, "platform", "darwin")
    image = np.zeros((4, 4, 3), dtype=np.uint8)

    def fake_window_capture(hwnd, x, y, width, height):
        return image

    monkeypatch.setattr(ocr, "capture_region_from_window", fake_window_capture)

    region = {"x": 1, "y": 2, "width": 3, "height": 4}
    assert main._capture_game_region(99, region) is image


def test_capture_game_region_raises_when_darwin_store_is_empty(monkeypatch):
    monkeypatch.setattr(main.sys, "platform", "darwin")
    monkeypatch.setattr(ocr, "capture_region_from_window", lambda *a: None)

    import pytest
    with pytest.raises(RuntimeError):
        main._capture_game_region(99, {"x": 0, "y": 0, "width": 1, "height": 1})


def test_capture_game_region_keeps_screen_capture_on_windows(monkeypatch):
    monkeypatch.setattr(main.sys, "platform", "win32")
    monkeypatch.setattr(main.wm, "get_window_rect_screen", lambda hwnd: (100, 200, 0, 0))
    seen = {}

    def fake_capture_region(left, top, width, height):
        seen["rect"] = (left, top, width, height)
        return np.zeros((height, width, 3), dtype=np.uint8)

    monkeypatch.setattr(ocr, "capture_region", fake_capture_region)

    region = {"x": 10, "y": 20, "width": 30, "height": 40}
    main._capture_game_region(99, region)
    assert seen["rect"] == (110, 220, 30, 40)


def test_game_region_color_matches_routes_to_window_probe_on_darwin(monkeypatch):
    monkeypatch.setattr(main.sys, "platform", "darwin")
    called = {}

    def fake_window_probe(hwnd, x, y, width, height, expected, tolerance=20):
        called["args"] = (x, y, width, height, expected, tolerance)
        return True

    monkeypatch.setattr(ocr, "sample_color_matches_window", fake_window_probe)
    assert bool(main._game_region_color_matches(99, 5, 6, 7, 8, 0x373737, 25)) is True
    assert called["args"] == (5, 6, 7, 8, 0x373737, 25)


def test_set_panel_expanded_allows_expansion_while_running_on_darwin(monkeypatch):
    monkeypatch.setattr(main.sys, "platform", "darwin")
    api = main.Api.__new__(main.Api)
    api._window = object()
    api._mac_panel_ready = True
    api._mac_panel_width = None
    api.runner = type("Runner", (), {"is_running": lambda self: True})()
    api.docker = type("Docker", (), {"docked": True})()
    api._mac_geometry_lock = type("Lock", (), {
        "__enter__": lambda self: self,
        "__exit__": lambda self, *a: None,
    })()

    layout = {"x": 0, "y": 0, "panel_w": 500, "expanded_w": 1500, "panel_h": 1000}
    monkeypatch.setattr(main, "_mac_panel_layout", lambda: layout)
    applied = []
    api._apply_panel_geometry = lambda x, y, w, h: applied.append((x, y, w, h))

    api.set_panel_expanded(True)

    assert applied == [(0, 0, 1500, 1000)]
