"""Tests for core/runner_blocks.py (BlockOps mixin)."""
from unittest.mock import MagicMock, patch
import pytest

from core.runner_blocks import BlockOps


class DummyRunner(BlockOps):
    def __init__(self):
        self.logs = []

    def _log(self, msg):
        self.logs.append(msg)

    def _strip_auto_upgrade_for_expedition(self, blocks, task):
        return blocks


def test_load_battle_blocks_empty_macro():
    runner = DummyRunner()
    task = {}
    result = runner._load_battle_blocks(task)
    assert result == []


@patch("core.templates.load_template")
def test_load_battle_blocks_dict_format(mock_load):
    mock_load.return_value = {
        "blocks": {
            "battle": [{"type": "upgrade_unit", "slot": 1}]
        }
    }
    runner = DummyRunner()
    task = {"macro": "test_macro"}
    result = runner._load_battle_blocks(task)
    assert len(result) == 1
    assert result[0]["type"] == "upgrade_unit"


@patch("core.templates.load_template")
def test_load_battle_blocks_legacy_flat_list(mock_load):
    mock_load.return_value = {
        "blocks": [{"type": "place_unit"}]
    }
    runner = DummyRunner()
    task = {"macro": "old_macro"}
    result = runner._load_battle_blocks(task)
    assert result == []
    assert any("old format" in log for log in runner.logs)


@patch("core.templates.load_template")
def test_load_battle_blocks_legacy_three_phase(mock_load):
    mock_load.return_value = {
        "blocks": {
            "during": [{"type": "wait", "ms": 1000}],
            "after": [{"type": "walk", "path": "path1"}]
        }
    }
    runner = DummyRunner()
    task = {"macro": "legacy_macro"}
    result = runner._load_battle_blocks(task)
    assert len(result) == 2
    assert any("legacy during/after" in log for log in runner.logs)


def test_walk_block_replays_with_phase_label(monkeypatch):
    """The Walk block replays a recorded path and labels its log by phase --
    so the same block works in Pre Start (multiple allowed) and Battle."""
    from core import runner_blocks

    runner = DummyRunner()
    runner._keyboard = MagicMock()
    runner._set_status = lambda **k: None

    replayed = {}
    monkeypatch.setattr(runner_blocks.walk_paths, "load_path",
                        lambda name: {"events": [("w", "down", 0.0)]})
    monkeypatch.setattr(runner_blocks.walk_paths, "replay_events",
                        lambda events, kb, stop, sprint=False: replayed.setdefault("hit", True))

    import threading
    block = {"type": "walk", "params": {"path": "MyPath"}}
    runner._run_walk_block_tick(threading.Event(), block, 2, phase_label="Pre Start")

    assert replayed.get("hit") is True
    assert any("Pre Start block #2 (Walk)" in m for m in runner.logs)


def test_walk_block_no_path_is_skipped(monkeypatch):
    from core import runner_blocks
    import threading

    runner = DummyRunner()
    runner._keyboard = MagicMock()
    runner._set_status = lambda **k: None
    called = {"replay": False}
    monkeypatch.setattr(runner_blocks.walk_paths, "replay_events",
                        lambda *a, **k: called.__setitem__("replay", True))

    runner._run_walk_block_tick(threading.Event(), {"type": "walk", "params": {"path": ""}}, 1,
                                phase_label="Pre Start")

    assert called["replay"] is False
    assert any("no path selected" in m for m in runner.logs)
