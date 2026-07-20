"""Loads Assets/stage_data.json (see tools/fetch_stage_data.py) -- the game's
full stage reward/boss/mission table, scraped from the wiki's own Lua data
module -- and looks up what a given map/act/difficulty is expected to
reward, so the macro can log that next to what OCR actually read off the
Victory screen.

Purely a reference/sanity-check, not validation: OCR is still the source of
truth for what a specific run actually dropped (chance Drops and
FirstClear-only bonuses vary run to run), this just answers "roughly what
should I be seeing here" for a human skimming the log.
"""
import json
import os

from . import constants

_DATA_PATH = os.path.join(constants.ASSETS_DIR, "stage_data.json")
_cache = None


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            with open(_DATA_PATH, "r", encoding="utf-8") as f:
                _cache = json.load(f)
        except (OSError, json.JSONDecodeError):
            _cache = {}
    return _cache


def list_maps() -> list:
    """Every map name actually present in Assets/stage_data.json -- e.g. for
    populating a debug UI's map picker so it stays in sync with whatever's
    been fetched instead of a hardcoded list."""
    return sorted(_load().get("Maps", {}).keys())


def _find_map(map_name: str):
    maps = _load().get("Maps", {})
    if map_name in maps:
        return maps[map_name]
    wanted = (map_name or "").strip().lower()
    for name, game_map in maps.items():
        if name.strip().lower() == wanted:
            return game_map
    return None


def get_stage(map_name: str, stage: str, difficulty: str = "Normal"):
    """stage is '1'..'5' (Story acts), 'Infinite', or 'Mastery' -- same
    values as ui/app.js's TASK_DATA.story.stages. Returns None if this
    map/stage/difficulty isn't in the scraped data (wrong name, or
    Assets/stage_data.json hasn't been generated yet -- see
    tools/fetch_stage_data.py)."""
    game_map = _find_map(map_name)
    if game_map is None:
        return None
    if stage == "Infinite":
        return game_map.get("Infinite")
    if stage == "Mastery":
        return game_map.get("Mastery")
    story = game_map.get("Story", {})
    mode = story.get("Hard" if difficulty == "Hard" else "Normal", {})
    return mode.get(f"Act {stage}")


def expected_rewards(map_name: str, stage: str, difficulty: str = "Normal") -> list:
    """"123x Item Name" strings for what this stage normally rewards on a
    non-first clear (currency/EXP + guaranteed items) -- not exhaustive,
    chance Drops and FirstClear-only bonuses are left out since those don't
    happen every run and would make this look "wrong" when it isn't."""
    stage_info = get_stage(map_name, stage, difficulty)
    if not stage_info:
        return []
    rewards = stage_info.get("Rewards", {})
    base = rewards.get("Normal") or rewards.get("Wave") or {}
    lines = [f"{amount}x {name}" for name, amount in base.items() if name != "Every"]
    for item in rewards.get("Items", []):
        lines.append(f"{item.get('Amount', '?')}x {item.get('Item', '?')}")
    return lines


def expected_item_amounts(map_name: str, stage: str, difficulty: str = "Normal") -> dict:
    """Name -> guaranteed Amount for everything this stage's Rewards
    dict promises on a non-first clear (currency/EXP + guaranteed Items) --
    the quantity half of what used to come from OCRing the tiny "125x"
    badge on the reward screen (see core.rewards.read_reward_row). These
    are fixed game data, not per-run RNG, so once an icon is IDENTIFIED
    (still done live, by color -- see core.rewards.identify_item_name) its
    quantity doesn't need to be read off the screen at all, just looked up
    here. Chance Drops are deliberately left out (no fixed amount exists
    for them to look up), so an item identified as a Drop still falls back
    to reporting "?" for its quantity rather than a wrong guess."""
    stage_info = get_stage(map_name, stage, difficulty)
    if not stage_info:
        return {}
    rewards = stage_info.get("Rewards", {})
    base = rewards.get("Normal") or rewards.get("Wave") or {}
    amounts = {name: amount for name, amount in base.items() if name != "Every"}
    for item in rewards.get("Items", []):
        if item.get("Item"):
            amounts[item["Item"]] = item.get("Amount", "?")
    return amounts


def expected_item_names(map_name: str, stage: str, difficulty: str = "Normal") -> list:
    """Just the names (currency/EXP + guaranteed Items + chance Drops) --
    for narrowing reward-icon identification down to what this stage can
    actually produce (see core.rewards.identify_item_name's allowed_names).
    Deliberately includes chance Drops here even though expected_rewards
    doesn't display them: a narrowing filter that excluded a real (if rare)
    possible drop would make THAT item unidentifiable whenever it legitimately
    shows up, which is worse than being slightly less narrow."""
    stage_info = get_stage(map_name, stage, difficulty)
    if not stage_info:
        return []
    rewards = stage_info.get("Rewards", {})
    base = rewards.get("Normal") or rewards.get("Wave") or {}
    names = [name for name in base.keys() if name != "Every"]
    for item in rewards.get("Items", []):
        if item.get("Item"):
            names.append(item["Item"])
    for drop in stage_info.get("Drops", []):
        if drop.get("Item"):
            names.append(drop["Item"])
    return names
