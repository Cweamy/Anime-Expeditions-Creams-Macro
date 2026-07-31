# Unit test for Team Loadout parsing fixes
import pytest

# Test helper for team number resolution in runner logic
def parse_team_number(blocks: dict, task: dict):
    # Mimics the team resolution logic in runner._apply_team_loadout
    raw_team = blocks.get("team") if blocks.get("team") is not None and blocks.get("team") != "" else task.get("team")
    team = str(raw_team).strip() if raw_team is not None else ""
    if not team:
        return None
    try:
        return int(team)
    except (TypeError, ValueError):
        return None


def test_parse_team_number_integer():
    # Verify integer team number in blocks (e.g., 2) parses correctly
    blocks = {"team": 2, "equipment": "include"}
    task = {"macro": "test_template"}
    assert parse_team_number(blocks, task) == 2


def test_parse_team_number_string():
    # Verify string team number in blocks (e.g., "2") parses correctly
    blocks = {"team": "2", "equipment": "include"}
    task = {"macro": "test_template"}
    assert parse_team_number(blocks, task) == 2


def test_parse_team_number_task_fallback():
    # Verify task fallback when template team is empty
    blocks = {"team": "", "equipment": "include"}
    task = {"macro": "test_template", "team": 3}
    assert parse_team_number(blocks, task) == 3


def test_parse_team_number_invalid():
    # Verify invalid team string returns None
    blocks = {"team": "invalid"}
    task = {}
    assert parse_team_number(blocks, task) is None
