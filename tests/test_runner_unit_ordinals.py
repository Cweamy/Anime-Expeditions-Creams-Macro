"""Place Unit #ordinals must match what the Macro Manager shows.

ui/app.js's listPlacedUnits() numbers every place_unit block across both
phases, in order, 1-based, counted whether or not the block is marked "Once".
Upgrade / Sell / Auto Upgrade Unit blocks target a unit by that number, so if
the runner numbers them differently those blocks act on the wrong unit.

These drive the real _run_prestart_blocks and _run_battle_blocks_tick -- only
the outside-world seams (mouse, keyboard, vision, screen capture, window rect,
template loading) are faked.
"""
import threading

import numpy as np
import pytest

from core import runner_blocks, vision
from core import templates as tpl
from core import window as wm
from core.runner import MacroRunner

WALK = {"type": "walk_path", "params": {}, "once": True, "mode": "auto", "pathName": ""}


class _Mouse:
    def __init__(self):
        self.clicks = []

    def move_to(self, x, y):
        pass

    def nudge(self, dx=1, dy=0):
        pass

    def click(self, x=None, y=None, button="left", hold=0.05):
        if x is not None:
            self.clicks.append((int(x), int(y)))

    def double_click(self, x=None, y=None, **kw):
        pass

    def shuffle_click(self, x, y, **kw):
        self.clicks.append((int(x), int(y)))


class _Keyboard:
    def tap(self, *a, **k):
        pass

    def key_down(self, *a):
        pass

    def key_up(self, *a):
        pass


def _place(name, x, y, once=False):
    return {"type": "place_unit", "once": once, "hotkey": name[0],
            "params": {"name": name, "x": x, "y": y}}


def _sell(index):
    return {"type": "sell_unit", "once": False, "params": {"index": index}}


@pytest.fixture
def sim(monkeypatch):
    """A runner wired to a fake game: the window sits at (0, 0) so every
    recorded click coordinate IS a reference-space coordinate, and the screen
    always reads as a valid (white) placement tile."""
    monkeypatch.setattr(wm, "get_window_rect_screen", lambda hwnd: (0, 0, 1152, 756))
    monkeypatch.setattr(runner_blocks.wm, "get_window_rect_screen", lambda hwnd: (0, 0, 1152, 756))

    # The placement path is full of real settle delays (hotkey settle, pixel
    # re-scan, post-click settle). They exist for the live game and are not
    # what these tests exercise -- left in, six tests take ~45s instead of
    # well under one.
    monkeypatch.setattr(runner_blocks.time, "sleep", lambda s: None)

    white = np.full((38, 38, 3), 255, np.uint8)
    monkeypatch.setattr(
        runner_blocks.vision,
        "capture_window_region_bgr",
        lambda _hwnd, region: white[:region[3], :region[2]],
    )

    def found(name):
        return {"x": 0, "y": 0, "w": 10, "h": 10, "cx": 5, "cy": 5, "score": 0.99}

    # Only unit_exist is "on screen": placement verification passes, and the
    # optional max_placement_reached check finds nothing.
    monkeypatch.setattr(vision, "find_image",
                        lambda hwnd, name, *a, **k: found(name) if name == "unit_exist" else None)
    monkeypatch.setattr(vision, "wait_for_image",
                        lambda hwnd, name, *a, **k: found(name) if name == "unit_exist" else None)
    monkeypatch.setattr(vision, "wait_for_image_any",
                        lambda hwnd, names, *a, **k: (found(names[0]), names[0])
                        if "unit_exist" in names else (None, None))
    monkeypatch.setattr(vision, "save_match_debug", lambda *a, **k: None)

    runner = MacroRunner(_Mouse(), _Keyboard(), lambda msg: None)
    runner._run_walk_path_block = lambda *a, **k: None
    return runner


def _sell_target(sim, monkeypatch, prestart, battle, target, repeats=2):
    """Run `repeats` entries into a stage and report which position the
    Sell Unit block clicked on each one."""
    template = {"name": "sim", "blocks": {"prestart": prestart, "battle": battle + [_sell(target)]}}
    monkeypatch.setattr(tpl, "load_template", lambda name: template)

    task = {"macro": "sim", "mode": "story", "map": "-", "difficulty": "-"}
    stop = threading.Event()
    reset = (sim._coords["unit_info_reset_x"], sim._coords["unit_info_reset_y"])
    clicked = []

    for rep in range(1, repeats + 1):
        first = rep == 1
        sim._mouse.clicks.clear()
        sim._run_prestart_blocks(1, stop, task, first_repeat=first)
        blocks = sim._load_battle_blocks(task)
        sim._battle_block_index = 0
        sim._battle_block_state = {}
        for _ in range(len(blocks) + 4):
            if sim._battle_block_index >= len(blocks):
                break
            sim._run_battle_blocks_tick(1, stop, blocks, first, macro_name="sim")
        real = [c for c in sim._mouse.clicks if c != reset]
        clicked.append(real[-1] if real else None)
    return clicked


ARCHER, MAGE, KNIGHT, HEALER = (100, 100), (200, 200), (300, 300), (400, 400)


def test_once_battle_unit_does_not_shift_later_ordinals(sim, monkeypatch):
    """The regression: a "Once" place_unit in the BATTLE phase is skipped on a
    repeat. If it isn't counted, every later unit's number shifts down by one
    and Sell Unit #3 starts clicking the unit the UI calls #4."""
    clicked = _sell_target(
        sim, monkeypatch,
        prestart=[WALK, _place("Archer", *ARCHER), _place("Mage", *MAGE)],
        battle=[_place("Knight", *KNIGHT, once=True), _place("Healer", *HEALER)],
        target=3,
    )
    assert clicked == [KNIGHT, KNIGHT], f"expected Knight on both entries, got {clicked}"


def test_two_once_battle_units_do_not_shift_the_slot_between_them(sim, monkeypatch):
    """With two skipped units the shift is two, and the slot that gets
    overwritten is the FIRST skipped one -- a target after both can look
    correct by accident because its slot keeps the value from entry 1."""
    clicked = _sell_target(
        sim, monkeypatch,
        prestart=[WALK, _place("Archer", *ARCHER)],
        battle=[_place("Mage", *MAGE, once=True), _place("Knight", *KNIGHT, once=True),
                _place("Healer", *HEALER)],
        target=2,
    )
    assert clicked == [MAGE, MAGE], f"expected Mage on both entries, got {clicked}"


def test_drift_does_not_grow_with_more_repeats(sim, monkeypatch):
    clicked = _sell_target(
        sim, monkeypatch,
        prestart=[WALK, _place("Archer", *ARCHER), _place("Mage", *MAGE)],
        battle=[_place("Knight", *KNIGHT, once=True), _place("Healer", *HEALER)],
        target=3, repeats=3,
    )
    assert clicked == [KNIGHT, KNIGHT, KNIGHT], f"got {clicked}"


def test_once_prestart_unit_was_already_correct(sim, monkeypatch):
    """_run_prestart_blocks already counted before its own "once" skip -- this
    guards that half from regressing the other way."""
    clicked = _sell_target(
        sim, monkeypatch,
        prestart=[WALK, _place("Archer", *ARCHER, once=True), _place("Mage", *MAGE)],
        battle=[_place("Knight", *KNIGHT), _place("Healer", *HEALER)],
        target=3,
    )
    assert clicked == [KNIGHT, KNIGHT], f"got {clicked}"


def test_no_once_blocks_is_unaffected(sim, monkeypatch):
    clicked = _sell_target(
        sim, monkeypatch,
        prestart=[WALK, _place("Archer", *ARCHER), _place("Mage", *MAGE)],
        battle=[_place("Knight", *KNIGHT), _place("Healer", *HEALER)],
        target=4,
    )
    assert clicked == [HEALER, HEALER], f"got {clicked}"


def test_once_on_a_non_place_unit_block_does_not_count(sim, monkeypatch):
    """Only place_unit blocks take a number -- a skipped wait_ms must not
    consume one."""
    clicked = _sell_target(
        sim, monkeypatch,
        prestart=[WALK, _place("Archer", *ARCHER), _place("Mage", *MAGE)],
        battle=[{"type": "wait_ms", "once": True, "params": {"ms": 1}},
                _place("Knight", *KNIGHT), _place("Healer", *HEALER)],
        target=3,
    )
    assert clicked == [KNIGHT, KNIGHT], f"got {clicked}"
