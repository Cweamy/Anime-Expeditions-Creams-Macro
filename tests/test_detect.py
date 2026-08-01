import threading
from unittest.mock import MagicMock

import numpy as np

from core import detect
from core import runner_blocks as rb
from core.runner import MacroRunner


# --------------------------------------------------------------------------
# flatten
# --------------------------------------------------------------------------
def test_flatten_without_detect_stamps_ordinals_and_passes_blocks_through():
    blocks = [
        {"type": "place_unit", "params": {}},
        {"type": "wait_ms"},
        {"type": "place_unit", "params": {}},
    ]
    flat, nxt = detect.flatten(blocks, 1)
    assert [b["type"] for b in flat] == ["place_unit", "wait_ms", "place_unit"]
    assert [b.get("_ordinal") for b in flat] == [1, None, 2]
    assert nxt == 3


def test_flatten_does_not_mutate_the_source_blocks():
    src = [{"type": "place_unit", "params": {}}]
    detect.flatten(src, 5)
    assert "_ordinal" not in src[0]  # flatten stamps a copy, never the saved dict


def test_flatten_then_else_offsets_route_both_branches():
    blocks = [
        {"type": "detect", "image": "a",
         "then": [{"type": "place_unit", "params": {}}, {"type": "wait_ms"}],
         "else": [{"type": "place_unit", "params": {}}]},
        {"type": "place_unit", "params": {}},
    ]
    flat, nxt = detect.flatten(blocks, 1)
    types = [b["type"] for b in flat]
    assert types == ["detect", "place_unit", "wait_ms", "_jump", "place_unit", "place_unit"]
    # ordinals stamped by static position (detect itself takes no number):
    # then's unit is #1, else's is #2, the trailing unit is #3.
    assert [b.get("_ordinal") for b in flat] == [None, 1, None, None, 2, 3]
    assert nxt == 4
    detect_block, jump = flat[0], flat[3]
    # FALSE from the detect (index 0) lands on the first else block (index 4)
    assert 0 + detect_block["_else_offset"] == 4
    # After the then branch runs, the _jump (index 3) skips the else block (index 4) -> index 5
    assert 3 + jump["_offset"] == 5
    assert 0 + detect_block["_end_offset"] == 5


def test_loop_settings_normalize_limits_and_interval():
    assert detect.loop_settings({"loop": True, "loopAttempts": "3", "loopIntervalMs": "250"}) == (True, 3, 0.25)
    assert detect.loop_settings({"loop": True, "loopAttempts": 0, "loopIntervalMs": 1}) == (True, 0, 0.1)
    assert detect.loop_settings({"loop": False}) == (False, 0, 1.0)


def test_flatten_empty_then_still_jumps_correctly():
    flat, _ = detect.flatten([{"type": "detect", "image": "a", "then": [], "else": [{"type": "wait_ms"}]}], 1)
    assert [b["type"] for b in flat] == ["detect", "_jump", "wait_ms"]
    assert 0 + flat[0]["_else_offset"] == 2   # false -> first else block
    assert 1 + flat[1]["_offset"] == 3        # true path: jump past else


def test_flatten_nested_detect_ordinals():
    blocks = [{
        "type": "detect", "image": "a",
        "then": [{
            "type": "detect", "image": "b",
            "then": [{"type": "place_unit", "params": {}}],
            "else": [{"type": "place_unit", "params": {}}],
        }],
        "else": [{"type": "place_unit", "params": {}}],
    }]
    flat, nxt = detect.flatten(blocks, 1)
    # three place_units total, numbered in static then-before-else order
    ordinals = [b.get("_ordinal") for b in flat if b["type"] == "place_unit"]
    assert ordinals == [1, 2, 3]
    assert nxt == 4


def test_flatten_battle_continues_prestart_numbering():
    prestart = [{"type": "place_unit", "params": {}}, {"type": "place_unit", "params": {}}]
    _, start = detect.flatten(prestart, 1)
    assert start == 3
    battle, _ = detect.flatten([{"type": "place_unit", "params": {}}], start)
    assert battle[0]["_ordinal"] == 3


# --------------------------------------------------------------------------
# evaluate
# --------------------------------------------------------------------------
def _patch_vision(monkeypatch, present, matches_by_name=None):
    """present: set of names that have a reference image. matches_by_name:
    name -> match dict (or None). find_image returns the match or None; a name
    not in `present` raises TemplateNotFound."""
    matches_by_name = matches_by_name or {}
    monkeypatch.setattr(detect.vision, "detect_template_dir", lambda name: "ui")

    def find_image(hwnd, name, region=None, threshold=None, template_dir=None):
        if name not in present:
            raise detect.vision.TemplateNotFound(name)
        return matches_by_name.get(name)
    monkeypatch.setattr(detect.vision, "find_image", find_image)

    def find_image_all(hwnd, name, region=None, threshold=None, template_dir=None, max_results=50):
        if name not in present:
            raise detect.vision.TemplateNotFound(name)
        m = matches_by_name.get(name)
        return [m] if m else []
    monkeypatch.setattr(detect.vision, "find_image_all", find_image_all)


def test_evaluate_single_found_and_not_found(monkeypatch):
    hit = {"cx": 100, "cy": 200, "score": 0.97, "x": 90, "y": 190, "w": 20, "h": 20}
    _patch_vision(monkeypatch, present={"boss", "empty"}, matches_by_name={"boss": hit, "empty": None})
    runner = MagicMock()
    found, matches = detect.evaluate(runner, 1, {"mode": "single", "image": "boss"})
    assert found is True and matches == [hit]
    found, matches = detect.evaluate(runner, 1, {"mode": "single", "image": "empty"})
    assert found is False and matches == []


def test_evaluate_multi_and_or(monkeypatch):
    a = {"cx": 1, "cy": 1, "score": 0.9}
    _patch_vision(monkeypatch, present={"a", "b"}, matches_by_name={"a": a, "b": None})
    runner = MagicMock()
    assert detect.evaluate(runner, 1, {"mode": "multi", "images": ["a", "b"], "logic": "and"})[0] is False
    assert detect.evaluate(runner, 1, {"mode": "multi", "images": ["a", "b"], "logic": "or"})[0] is True
    assert detect.evaluate(runner, 1, {"mode": "multi", "images": ["a"], "logic": "and"})[0] is True


def test_evaluate_show_all_returns_locations(monkeypatch):
    hit = {"cx": 5, "cy": 6, "score": 0.95}
    _patch_vision(monkeypatch, present={"a"}, matches_by_name={"a": hit})
    found, matches = detect.evaluate(MagicMock(), 1, {"mode": "single", "image": "a", "showAll": True})
    assert found is True and matches == [hit]


def test_evaluate_missing_image_is_not_found_and_warns(monkeypatch):
    _patch_vision(monkeypatch, present=set())
    logs = []
    runner = MagicMock()
    runner._log = logs.append
    found, matches = detect.evaluate(runner, 1, {"mode": "single", "image": "ghost"})
    assert found is False and matches == []
    assert any("no reference image" in m for m in logs)


def test_diagnose_frame_uses_one_full_frame_and_offsets_region_matches(monkeypatch):
    local_best = {"x": 2, "y": 3, "w": 8, "h": 6, "cx": 6, "cy": 6, "score": 0.84}
    monkeypatch.setattr(detect.vision, "detect_template_dir", lambda _name: "detect")
    monkeypatch.setattr(
        detect.vision,
        "find_in_gray_multiscale_diagnostic",
        lambda *_args, **_kwargs: {"match": None, "best": local_best},
    )
    frame = np.zeros((40, 50, 3), dtype=np.uint8)

    report = detect.diagnose_frame(
        frame,
        {"mode": "single", "image": "Defense", "region": {"x": 10, "y": 8, "w": 20, "h": 15},
         "threshold": 0.90},
    )

    assert report["found"] is False
    detail = report["details"][0]
    assert detail["name"] == "Defense"
    assert detail["score"] == 0.84
    assert detail["best_match"]["x"] == 12
    assert detail["best_match"]["y"] == 11
    assert detail["best_match"]["cx"] == 16
    assert detail["best_match"]["cy"] == 14


def test_diagnose_frame_preserves_multi_image_and_or_logic(monkeypatch):
    hit = {"x": 1, "y": 2, "w": 4, "h": 5, "cx": 3, "cy": 4, "score": 0.95}
    monkeypatch.setattr(detect.vision, "detect_template_dir", lambda _name: "detect")

    def diagnose(_frame, name, **_kwargs):
        match = hit if name == "Defense" else None
        return {"match": match, "best": match}

    monkeypatch.setattr(detect.vision, "find_in_gray_multiscale_diagnostic", diagnose)
    frame = np.zeros((20, 30, 3), dtype=np.uint8)

    found_or = detect.diagnose_frame(
        frame, {"mode": "multi", "images": ["Defense", "Elite"], "logic": "or"})
    found_and = detect.diagnose_frame(
        frame, {"mode": "multi", "images": ["Defense", "Elite"], "logic": "and"})

    assert found_or["found"] is True
    assert found_and["found"] is False
    assert [detail["name"] for detail in found_and["details"]] == ["Defense", "Elite"]


def test_render_diagnostic_draws_region_and_best_candidate():
    frame = np.zeros((40, 50, 3), dtype=np.uint8)
    report = {
        "found": False,
        "region": (5, 6, 20, 15),
        "details": [{
            "name": "Defense", "matched": False, "score": 0.84,
            "threshold": 0.90, "best_match": {
                "x": 8, "y": 9, "w": 6, "h": 5, "cx": 11, "cy": 11,
                "score": 0.84,
            }, "match": None, "matches": [],
        }],
    }

    rendered = detect.render_diagnostic(frame, report)

    assert rendered.shape == frame.shape
    assert np.any(rendered != frame)


# --------------------------------------------------------------------------
# raw condition expression -- allowlist
# --------------------------------------------------------------------------
class _FakeCtx:
    def __init__(self, present, counts=None):
        self.present = set(present)
        self.counts = counts or {}

    def find(self, name):
        return name in self.present

    def count(self, name):
        return self.counts.get(name, 0)


def test_eval_expression_allows_boolean_and_compare():
    ctx = _FakeCtx({"a"}, counts={"c": 3})
    assert detect._eval_expr("find('a') and not find('b')", ctx) is True
    assert detect._eval_expr("find('b') or find('a')", ctx) is True
    assert detect._eval_expr("count('c') >= 2", ctx) is True
    assert detect._eval_expr("count('c') > 5", ctx) is False


def test_eval_expression_blocks_dangerous_input_and_fails_safe():
    ctx = _FakeCtx(set())
    for bad in [
        "__import__('os').system('echo hi')",
        "find.__class__",
        "open('x')",
        "[find('a') for _ in range(3)]",
        "find('a'); find('b')",
        "1 if find('a') else 0",
    ]:
        logs = []
        assert detect._eval_expr(bad, ctx, log=logs.append) is False
        assert logs, f"expected a warning log for blocked expr: {bad}"


def test_eval_expression_empty_is_false():
    assert detect._eval_expr("", _FakeCtx(set())) is False


# --------------------------------------------------------------------------
# runner tick: detect routes the flat index into the taken branch
# --------------------------------------------------------------------------
def _drive_battle(runner, flat):
    stop = threading.Event()
    for _ in range(50):
        if runner._battle_block_index >= len(flat):
            break
        runner._run_battle_blocks_tick(0, stop, flat, True, "m")


def test_battle_tick_runs_then_branch_when_found(monkeypatch):
    runner = MacroRunner(MagicMock(), MagicMock(), MagicMock())
    runner._battle_block_index = 0
    runner._battle_block_state = {}
    runner._log = lambda *a, **k: None
    recorded = []
    runner._run_send_key_tick = lambda block, num, phase_label="Battle": recorded.append(block.get("_tag"))
    runner._run_wait_ms_tick = lambda stop, block, num, phase_label="Battle": recorded.append(block.get("_tag"))
    monkeypatch.setattr(rb.detect, "evaluate", lambda r, h, b: (True, []))

    flat, _ = rb.detect.flatten([
        {"type": "detect", "image": "x",
         "then": [{"type": "send_key", "_tag": "then"}],
         "else": [{"type": "send_key", "_tag": "else"}]},
        {"type": "wait_ms", "_tag": "after"},
    ])
    _drive_battle(runner, flat)
    assert recorded == ["then", "after"]


def test_battle_tick_runs_else_branch_when_not_found(monkeypatch):
    runner = MacroRunner(MagicMock(), MagicMock(), MagicMock())
    runner._battle_block_index = 0
    runner._battle_block_state = {}
    runner._log = lambda *a, **k: None
    recorded = []
    runner._run_send_key_tick = lambda block, num, phase_label="Battle": recorded.append(block.get("_tag"))
    runner._run_wait_ms_tick = lambda stop, block, num, phase_label="Battle": recorded.append(block.get("_tag"))
    monkeypatch.setattr(rb.detect, "evaluate", lambda r, h, b: (False, []))

    flat, _ = rb.detect.flatten([
        {"type": "detect", "image": "x",
         "then": [{"type": "send_key", "_tag": "then"}],
         "else": [{"type": "send_key", "_tag": "else"}]},
        {"type": "wait_ms", "_tag": "after"},
    ])
    _drive_battle(runner, flat)
    assert recorded == ["else", "after"]


def test_looped_detect_retries_then_runs_then_once(monkeypatch):
    runner = MacroRunner(MagicMock(), MagicMock(), MagicMock())
    runner._battle_block_index = 0
    runner._battle_block_state = {}
    runner._log = lambda *a, **k: None
    recorded = []
    runner._run_send_key_tick = lambda block, num, phase_label="Battle": recorded.append(block.get("_tag"))
    outcomes = iter([(False, []), (True, [])])
    monkeypatch.setattr(rb.detect, "evaluate", lambda r, h, b: next(outcomes))
    clock = iter([100.0, 100.2])
    monkeypatch.setattr(rb.time, "time", lambda: next(clock))

    flat, _ = rb.detect.flatten([{
        "type": "detect", "image": "x", "loop": True,
        "loopAttempts": 3, "loopIntervalMs": 100,
        "then": [{"type": "send_key", "_tag": "then"}],
        "else": [{"type": "send_key", "_tag": "else"}],
    }])
    completed = set()
    runner._run_battle_blocks_tick(0, threading.Event(), flat, True, "m", persistent_detects=completed)
    assert runner._battle_block_index == 0
    runner._run_battle_blocks_tick(0, threading.Event(), flat, True, "m", persistent_detects=completed)
    _drive_battle(runner, flat)
    assert recorded == ["then"]
    assert completed == {0}

    # Loop A/B starts the flat list over, but the completed Detect skips its
    # whole construct instead of firing Then again while the image remains.
    runner._battle_block_index = 0
    runner._battle_block_state = {}
    runner._run_battle_blocks_tick(0, threading.Event(), flat, True, "m", persistent_detects=completed)
    assert runner._battle_block_index == len(flat)
    assert recorded == ["then"]


def test_looped_detect_uses_else_once_after_search_limit(monkeypatch):
    runner = MacroRunner(MagicMock(), MagicMock(), MagicMock())
    runner._battle_block_index = 0
    runner._battle_block_state = {}
    runner._log = lambda *a, **k: None
    recorded = []
    runner._run_send_key_tick = lambda block, num, phase_label="Battle": recorded.append(block.get("_tag"))
    monkeypatch.setattr(rb.detect, "evaluate", lambda r, h, b: (False, []))
    clock = iter([200.0, 200.2])
    monkeypatch.setattr(rb.time, "time", lambda: next(clock))

    flat, _ = rb.detect.flatten([{
        "type": "detect", "image": "x", "loop": True,
        "loopAttempts": 2, "loopIntervalMs": 100,
        "then": [{"type": "send_key", "_tag": "then"}],
        "else": [{"type": "send_key", "_tag": "else"}],
    }])
    completed = set()
    runner._run_battle_blocks_tick(0, threading.Event(), flat, True, "m", persistent_detects=completed)
    runner._run_battle_blocks_tick(0, threading.Event(), flat, True, "m", persistent_detects=completed)
    _drive_battle(runner, flat)
    assert recorded == ["else"]
    assert completed == {0}


def test_prestart_looped_detect_waits_until_found(monkeypatch):
    runner = MacroRunner(MagicMock(), MagicMock(), MagicMock())
    runner._log = lambda *a, **k: None
    runner._log_detect_outcome = lambda *a, **k: None
    runner._checkpoint = lambda _stop: False
    sleeps = []
    runner._interruptible_sleep = lambda seconds, _stop: sleeps.append(seconds)
    outcomes = iter([(False, []), (True, [])])
    monkeypatch.setattr(rb.detect, "evaluate", lambda r, h, b: next(outcomes))

    result = runner._run_prestart_detect(
        0, threading.Event(),
        {"type": "detect", "loop": True, "loopAttempts": 0, "loopIntervalMs": 100},
        1,
    )
    assert result is True
    assert sleeps == [0.1]
