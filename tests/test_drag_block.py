"""Drag block (Macro Manager > Setup > Drag): press at (x1, y1), move to
(x2, y2) while held, release -- same window-client coords as the Click
block, run through Mouse.drag."""

import threading

import core.runner_blocks as blocks_module
from core.runner import MacroRunner


def _runner_with_drag_spy():
    runner = object.__new__(MacroRunner)
    runner.drag_calls = []
    runner.logged = []
    runner._mouse = type("Mouse", (), {
        "drag": lambda self, x1, y1, x2, y2, **kw: runner.drag_calls.append((x1, y1, x2, y2, kw)),
    })()
    runner._log = lambda message: runner.logged.append(message)
    return runner


def test_run_drag_block_drags_between_screen_points(monkeypatch):
    runner = _runner_with_drag_spy()
    monkeypatch.setattr(blocks_module.wm, "get_window_rect_screen", lambda hwnd: (100, 50, 0, 0))
    block = {"type": "drag", "params": {"x1": "10", "y1": "20", "x2": "30", "y2": "40"}}
    runner._run_drag_block(hwnd=123, stop_event=threading.Event(), block=block, block_num=1)
    # Client coords get the window origin added, exactly like the Click block.
    # steps/duration_ms unset -> the DRAG_DEFAULT_* floors (30 steps, 600ms).
    assert runner.drag_calls == [(110, 70, 130, 90, {"steps": 30, "duration": 0.6})]
    assert any("(10, 20) -> (30, 40)" in m for m in runner.logged)


def test_run_drag_block_forwards_custom_steps_and_duration(monkeypatch):
    runner = _runner_with_drag_spy()
    monkeypatch.setattr(blocks_module.wm, "get_window_rect_screen", lambda hwnd: (0, 0, 0, 0))
    block = {"type": "drag", "params": {
        "x1": "10", "y1": "20", "x2": "30", "y2": "40",
        "steps": "50", "duration_ms": "1200",
    }}
    runner._run_drag_block(hwnd=123, stop_event=threading.Event(), block=block, block_num=1)
    # duration_ms is converted to seconds for Mouse.drag.
    assert runner.drag_calls == [(10, 20, 30, 40, {"steps": 50, "duration": 1.2})]


def test_run_drag_block_bad_steps_fall_back_to_defaults(monkeypatch):
    runner = _runner_with_drag_spy()
    monkeypatch.setattr(blocks_module.wm, "get_window_rect_screen", lambda hwnd: (0, 0, 0, 0))
    block = {"type": "drag", "params": {"x1": "1", "y1": "2", "x2": "3", "y2": "4",
                                        "steps": "abc", "duration_ms": "xyz"}}
    runner._run_drag_block(hwnd=123, stop_event=threading.Event(), block=block, block_num=1)
    assert runner.drag_calls == [(1, 2, 3, 4, {"steps": 30, "duration": 0.6})]


def test_run_drag_block_skips_when_no_positions_set(monkeypatch):
    runner = _runner_with_drag_spy()
    monkeypatch.setattr(blocks_module.wm, "get_window_rect_screen", lambda hwnd: (0, 0, 0, 0))
    block = {"type": "drag", "params": {"x1": 0, "y1": 0, "x2": 0, "y2": 0}}
    runner._run_drag_block(hwnd=123, stop_event=threading.Event(), block=block, block_num=1)
    assert runner.drag_calls == []
    assert any("no positions set" in m for m in runner.logged)


def test_run_drag_block_skips_bad_coordinates():
    runner = _runner_with_drag_spy()
    block = {"type": "drag", "params": {"x1": "abc", "y1": "20", "x2": "30", "y2": "40"}}
    runner._run_drag_block(hwnd=123, stop_event=threading.Event(), block=block, block_num=1)
    assert runner.drag_calls == []
    assert any("bad coordinates" in m for m in runner.logged)
