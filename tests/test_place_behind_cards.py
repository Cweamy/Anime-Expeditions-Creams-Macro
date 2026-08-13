"""A mid-battle placement must not fire into a reward card.

A level-up "Select an upgrade!" card renders over the board, so it swallows
the placement click -- the unit is never placed, and the tile search may not
even find a highlight to aim at. Battle blocks tick BEFORE the poll loop's
own card dismissal, so a card landing on the same tick as a placement simply
wins.
"""
import threading
from unittest.mock import MagicMock

from core.runner import MacroRunner


def _runner(card_present):
    runner = MacroRunner(MagicMock(), MagicMock(), MagicMock())
    runner._log = lambda *_a, **_k: None
    runner._checkpoint = lambda _stop: False
    runner._battle_block_index = 0
    runner._battle_block_state = {}
    runner._quick_place_shift_down = False
    runner.placed = []
    runner.cards_taken = []

    def dismiss(_hwnd):
        if card_present and not runner.cards_taken:
            runner.cards_taken.append(1)
            return True
        return False

    runner._dismiss_reward_card_if_found = dismiss
    runner._run_place_unit_block = lambda *a, **k: runner.placed.append(1)
    # No Start Game popup unless a test says otherwise.
    runner._is_expedition_match = True
    runner._find_start_game_button = lambda _hwnd: (None, None)
    return runner


BLOCKS = [{"type": "place_unit", "hotkey": "1",
           "params": {"name": "senku", "x": 100, "y": 100}}]


def _tick(runner):
    runner._run_battle_blocks_tick(123, threading.Event(), BLOCKS, True, "EXPD")


def test_a_card_on_screen_takes_the_tick_instead_of_the_placement(monkeypatch):
    """The regression: without the guard the placement fires into the card."""
    runner = _runner(card_present=True)

    _tick(runner)

    assert runner.cards_taken == [1]
    assert runner.placed == [], "placed while a card was covering the board"
    assert runner._battle_block_index == 0, "the block must stay put and retry"


def test_the_placement_happens_on_the_next_poll(monkeypatch):
    """Deferred, not skipped -- the unit still goes down, one tick later."""
    runner = _runner(card_present=True)

    _tick(runner)   # card taken
    _tick(runner)   # board clear now

    assert runner.placed == [1]
    assert runner._battle_block_index == 1


def test_a_clear_board_places_immediately(monkeypatch):
    """No card means no added delay -- the common case costs one image
    search and nothing else."""
    runner = _runner(card_present=False)

    _tick(runner)

    assert runner.placed == [1]
    assert runner.cards_taken == []
    assert runner._battle_block_index == 1


def test_the_start_game_popup_also_defers_the_placement(monkeypatch):
    """The "Start Game?" confirmation can come back mid-run and covers the
    board the same way a card does. It is handled later in the same poll by
    _check_expedition_wave_result, so waiting a tick is enough."""
    runner = _runner(card_present=False)
    seen = {"n": 0}

    def start_game(_hwnd):
        seen["n"] += 1
        return ("nav_start_game", {"score": 1.0}) if seen["n"] == 1 else (None, None)

    runner._find_start_game_button = start_game

    _tick(runner)
    assert runner.placed == [], "placed while Start Game was covering the board"
    assert runner._battle_block_index == 0

    _tick(runner)   # the expedition handler cleared it between polls
    assert runner.placed == [1]


def test_the_start_game_guard_is_expedition_only(monkeypatch):
    """Only Expedition has a mid-run handler for that popup. Deferring on a
    mode with nobody to clear it would stall the block, not delay it."""
    runner = _runner(card_present=False)
    runner._is_expedition_match = False
    runner._find_start_game_button = lambda _hwnd: ("nav_start_game", {"score": 1.0})

    _tick(runner)

    assert runner.placed == [1]
