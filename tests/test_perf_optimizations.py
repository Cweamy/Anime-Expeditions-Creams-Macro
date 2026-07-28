"""Tests for performance optimizations in templates metadata caching and stage_select scroll hover."""
import json
import os
import pytest
import numpy as np

from core import templates as tpl
from core import stage_select


def test_stored_name_mtime_caching(tmp_path):
    """Verify that _stored_name caches display name by file mtime and invalidates when modified."""
    template_file = tmp_path / "test_macro.json"
    template_file.write_text(json.dumps({"name": "Initial Name", "blocks": []}), encoding="utf-8")

    path_str = str(template_file)
    # First call reads from disk
    name1 = tpl._stored_name(path_str, "fallback")
    assert name1 == "Initial Name"

    # Modify file contents and update mtime to ensure cache invalidation
    template_file.write_text(json.dumps({"name": "Updated Name", "blocks": []}), encoding="utf-8")
    future_mtime = os.path.getmtime(path_str) + 2.0
    os.utime(path_str, (future_mtime, future_mtime))

    # Second call detects mtime change and reads new display name
    name2 = tpl._stored_name(path_str, "fallback")
    assert name2 == "Updated Name"


def test_stage_select_hover_wiggle_once_per_pass(monkeypatch):
    """Verify that find_and_click_map calls _hover_wiggle once per pass, not on every nudge."""
    wiggle_calls = []

    def mock_wiggle(mouse):
        wiggle_calls.append(True)

    monkeypatch.setattr(stage_select, "_hover_wiggle", mock_wiggle)
    monkeypatch.setattr(stage_select.wm, "get_window_rect_screen", lambda hwnd: (0, 0, 1152, 756))

    # Mock vision.find_image_any to return match only on 3rd nudge (nudge index 2)
    call_count = 0

    def mock_find_image_any(hwnd, names, threshold, template_dir):
        nonlocal call_count
        call_count += 1
        if call_count >= 3:
            return {"x": 100, "y": 100, "w": 50, "h": 50, "cx": 125, "cy": 125, "score": 0.9}, names[0]
        return None, None

    monkeypatch.setattr(stage_select.vision, "find_image_any", mock_find_image_any)
    monkeypatch.setattr(stage_select.vision, "save_match_debug", lambda *a, **kw: None)
    monkeypatch.setattr(stage_select.vision, "click_match", lambda *a, **kw: None)

    class MockMouse:
        def move_to(self, x, y):
            pass
        def scroll(self, step):
            pass

    mouse = MockMouse()
    res = stage_select.find_and_click_map(mouse, 12345, "TestMap", lambda msg: None, scroll_nudges=5)

    assert res is True
    # _hover_wiggle should be called exactly ONCE at pass start, even though 3 nudges executed
    assert len(wiggle_calls) == 1
