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
