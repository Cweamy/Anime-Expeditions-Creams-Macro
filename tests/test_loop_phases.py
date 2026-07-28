import threading
from unittest.mock import MagicMock

from core import runner_blocks as rb
from core import templates as tpl
from core.runner import MacroRunner


def _runner():
    runner = MacroRunner(MagicMock(), MagicMock(), MagicMock())
    runner._battle_block_index = 0
    runner._battle_block_state = {}
    runner._log = lambda *a, **k: None
    return runner


def test_load_loop_blocks_continues_unit_numbering_after_prestart_and_battle(monkeypatch):
    template = {"blocks": {
        "prestart": [{"type": "place_unit", "params": {}}],
        "battle": [{"type": "place_unit", "params": {}}],
        "loop_a": [{"type": "place_unit", "params": {}}],
        "loop_b": [{"type": "place_unit", "params": {}}],
    }}
    monkeypatch.setattr(tpl, "load_template", lambda name: template)
    runner = _runner()
    loops = runner._load_loop_blocks({"macro": "m", "mode": "story", "map": "-"})
    # prestart=#1, battle=#2, so loop_a=#3, loop_b=#4 -- matches listPlacedUnits
    assert loops["loop_a"][0]["_ordinal"] == 3
    assert loops["loop_b"][0]["_ordinal"] == 4


def test_loop_phase_cycles_and_restarts_at_the_end(monkeypatch):
    runner = _runner()
    # Battle's own index/state must be untouched by loop ticking.
    runner._battle_block_index = 99
    runner._battle_block_state = {"battle": "keep"}
    recorded = []
    runner._run_send_key_tick = lambda block, num, phase_label="Battle": recorded.append(block.get("_tag"))

    flat, _ = rb.detect.flatten([{"type": "send_key", "_tag": "A"}, {"type": "send_key", "_tag": "B"}])
    runner._loop_runtime = {
        "loop_a": {"blocks": flat, "index": 0, "state": {}},
        "loop_b": {"blocks": [], "index": 0, "state": {}},
    }
    stop = threading.Event()
    for _ in range(3):
        runner._tick_loop_phases(0, stop, True, "m")

    assert recorded == ["A", "B", "A"]  # ran to the end, then looped back to the top
    assert runner._battle_block_index == 99
    assert runner._battle_block_state == {"battle": "keep"}


def test_loop_phase_evaluates_detect_each_cycle(monkeypatch):
    runner = _runner()
    recorded = []
    runner._run_send_key_tick = lambda block, num, phase_label="Battle": recorded.append(block.get("_tag"))
    monkeypatch.setattr(rb.detect, "evaluate", lambda r, h, b: (True, []))

    flat, _ = rb.detect.flatten([
        {"type": "detect", "image": "x",
         "then": [{"type": "send_key", "_tag": "hit"}],
         "else": [{"type": "send_key", "_tag": "miss"}]},
    ])
    runner._loop_runtime = {
        "loop_a": {"blocks": flat, "index": 0, "state": {}},
        "loop_b": {"blocks": [], "index": 0, "state": {}},
    }
    stop = threading.Event()
    # One cycle is 3 ticks: detect eval, then-branch send_key, then the
    # synthetic _jump that wraps back to the top. Six ticks = two full cycles.
    for _ in range(6):
        runner._tick_loop_phases(0, stop, True, "m")
    assert recorded == ["hit", "hit"]
