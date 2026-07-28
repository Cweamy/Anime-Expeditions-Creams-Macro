import threading
from unittest.mock import MagicMock

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
