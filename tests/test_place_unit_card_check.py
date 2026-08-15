"""A placement is confirmed by the unit's hotbar card, not by unit_exist.

unit_exist answered the wrong question. It is a whole-screen search for a
unit info panel, so it hit whenever ANY panel was open -- including one left
over from the placement before -- and a unit that never left the hand was
logged as "verified placed (score 1.00)". Seen live on Flower Forest: six
placements logged, four units actually on the board, no error for either of
the two that failed.

The card is a direct answer. A placed unit's card drops its price; one still
in hand keeps it. Measured on real 1152x756 captures:

                        card value   price px   price hue
  in hand, affordable     75-162      657-668    36-54  (gold)
  in hand, greyed out      47-60      327-501      0    (red)
  placed, or empty slot    22-26        4-10       --
"""
import threading
from unittest.mock import MagicMock

import numpy as np
import pytest

from core import runner_blocks, vision
from core.runner_blocks import BlockOps


# ---------------------------------------------------------------------------
# Which slot a block's hotkey means
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hotkey,slot", [
    ("1", 1), ("9", 9), ("10", 10),
    ("0", 10),          # the keyboard row ends 9, 0 -- 0 is the tenth slot
    (3, 3),             # already a number
    ("", None), (None, None), ("q", None), ("f5", None),
    ("11", None), ("-1", None),
])
def test_card_slot_for(hotkey, slot):
    assert BlockOps._card_slot_for(hotkey) == slot


# ---------------------------------------------------------------------------
# Reading a card
# ---------------------------------------------------------------------------

def _strip(hue_degrees=None, pixels=0):
    """A price strip with `pixels` saturated glyph pixels of a given hue."""
    bgr = np.zeros((vision.CARD_PRICE_STRIP_H, vision.CARD_W, 3), np.uint8)
    if pixels:
        hsv = np.zeros((1, pixels, 3), np.uint8)
        hsv[0, :, 0] = hue_degrees // 2      # OpenCV packs hue as 0-179
        hsv[0, :, 1] = 200
        hsv[0, :, 2] = 200
        import cv2
        row = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0]
        flat = bgr.reshape(-1, 3)
        flat[:pixels] = row
        bgr = flat.reshape(bgr.shape)
    return bgr


def _read(monkeypatch, strip, slot=1):
    monkeypatch.setattr(vision, "capture_game_bgr", lambda h, region=None: strip)
    return vision.read_unit_card(0, slot)


def test_a_gold_price_is_a_unit_still_in_hand_and_affordable(monkeypatch):
    card = _read(monkeypatch, _strip(45, 660))
    assert card["in_hand"] is True
    assert card["affordable"] is True


def test_a_red_price_is_in_hand_but_unaffordable(monkeypatch):
    """The greyed state. It MUST NOT read as placed -- that would tell the
    runner a unit went down while it is still sitting in the hotbar, which is
    the exact failure this check exists to catch."""
    card = _read(monkeypatch, _strip(0, 400))
    assert card["in_hand"] is True
    assert card["affordable"] is False


def test_no_price_at_all_is_placed_or_empty(monkeypatch):
    card = _read(monkeypatch, _strip(pixels=0))
    assert card["in_hand"] is False
    assert card["affordable"] is None


def test_a_handful_of_stray_pixels_is_still_placed(monkeypatch):
    """Measured 4-10 stray pixels on placed/empty slots against 327+ in hand,
    so the gap is enormous -- but the threshold still has to sit in it."""
    card = _read(monkeypatch, _strip(0, 10))
    assert card["in_hand"] is False


def test_hue_is_not_wrapped_by_uint8_overflow(monkeypatch):
    """OpenCV hue is 0-179 in a uint8 array. Doubling it in place wraps
    anything past 127 back into the red band, which would report an
    unaffordable card as affordable -- so a cyan price must not read red."""
    card = _read(monkeypatch, _strip(300, 400))
    assert card["hue"] == 300


def test_an_out_of_range_slot_reads_as_nothing(monkeypatch):
    monkeypatch.setattr(vision, "capture_game_bgr",
                        lambda h, region=None: pytest.fail("should not capture"))
    assert vision.read_unit_card(0, 0)["in_hand"] is False
    assert vision.read_unit_card(0, 11)["in_hand"] is False


def test_an_empty_capture_does_not_claim_the_unit_is_in_hand(monkeypatch):
    card = _read(monkeypatch, np.zeros((0, 0, 3), np.uint8))
    assert card["in_hand"] is False


@pytest.mark.parametrize("slot,x0", [(1, 230), (2, 300), (6, 580), (10, 860)])
def test_each_slot_reads_its_own_card(monkeypatch, slot, x0):
    """Slot 6 was measured at x 580-642 on a real capture; the rest follow
    from the pitch. Reading the wrong slot would verify a different unit."""
    seen = {}

    def capture(h, region=None):
        seen["region"] = region
        return _strip(45, 660)

    monkeypatch.setattr(vision, "capture_game_bgr", capture)
    vision.read_unit_card(0, slot)

    assert seen["region"][0] == x0
    assert seen["region"][2] == vision.CARD_W


# ---------------------------------------------------------------------------
# The placement block using it
# ---------------------------------------------------------------------------

class _Runner(BlockOps):
    def __init__(self):
        self.logs = []
        self._mouse = MagicMock()
        self._keyboard = MagicMock()
        self._placed_unit_positions = {}
        self._quick_place_shift_down = False

    def _log(self, msg):
        self.logs.append(msg)

    def _set_status(self, **_k):
        pass

    def _checkpoint(self, _stop):
        return False

    def _reset_unit_info_panel(self, _hwnd):
        pass


@pytest.fixture
def placing(monkeypatch):
    """A runner whose tile search always succeeds, so the only thing under
    test is what happens AFTER the click."""
    monkeypatch.setattr(runner_blocks.time, "sleep", lambda _s: None)
    monkeypatch.setattr(runner_blocks.vision, "find_image", lambda *a, **k: None)
    monkeypatch.setattr(BlockOps, "_find_valid_place_spot",
                        lambda self, *a, **k: (500, 400))
    monkeypatch.setattr(runner_blocks.keys, "key_name_to_vk", lambda name: 0x31)
    return _Runner()


def _place(runner, verify=True):
    block = {"type": "place_unit", "hotkey": "4", "params": {"name": "cell", "x": 500, "y": 400}}
    runner._run_place_unit_block(1, threading.Event(), 0, 0, block, 1, "m",
                                  unit_ordinal=7, verify=verify)


def test_a_cleared_card_counts_as_placed(placing, monkeypatch):
    monkeypatch.setattr(runner_blocks.vision, "read_unit_card",
                        lambda h, s: {"in_hand": False, "affordable": None, "price_px": 6, "hue": -1})

    _place(placing)

    assert placing._placed_unit_positions[7] == (500, 400)
    assert any("card 4 cleared" in m for m in placing.logs)


def test_a_card_still_in_hand_is_reported_as_a_failure(placing, monkeypatch):
    """The regression. This is what used to log "verified placed (score 1.00)"."""
    monkeypatch.setattr(runner_blocks.vision, "read_unit_card",
                        lambda h, s: {"in_hand": True, "affordable": True, "price_px": 660, "hue": 45})

    _place(placing)

    assert 7 not in placing._placed_unit_positions, "recorded a position for a unit never placed"
    assert any("did NOT place" in m for m in placing.logs)


def test_being_unable_to_afford_it_says_so(placing, monkeypatch):
    monkeypatch.setattr(runner_blocks.vision, "read_unit_card",
                        lambda h, s: {"in_hand": True, "affordable": False, "price_px": 400, "hue": 0})

    _place(placing)

    assert any("cannot afford it yet" in m for m in placing.logs)


def test_a_pre_start_placement_is_checked_too(placing, monkeypatch):
    """Pre Start ran with verify=False and so logged every placement as a
    success on no evidence -- that is how the unaffordable unit in the live
    run was reported placed. The card read is cheap enough to always do."""
    monkeypatch.setattr(runner_blocks.vision, "read_unit_card",
                        lambda h, s: {"in_hand": True, "affordable": False, "price_px": 400, "hue": 0})
    placing._unplaced_units = {}

    _place(placing, verify=False)

    assert 7 not in placing._placed_unit_positions
    assert 4 in placing._unplaced_units, "a staged unit still has to be followed up"


def test_pre_start_does_not_cry_wolf(placing, monkeypatch):
    """Pre Start stages rather than deploys: every card keeps its price for
    the whole phase and clears when the round starts, even for units that do
    go down. Calling that a failure made all three Pre Start placements
    report one on every single live run."""
    monkeypatch.setattr(runner_blocks.vision, "read_unit_card",
                        lambda h, s: {"in_hand": True, "affordable": True, "price_px": 660, "hue": 45})
    placing._unplaced_units = {}

    _place(placing, verify=False)

    assert not any("did NOT place" in m for m in placing.logs)
    assert any("staged" in m for m in placing.logs)


def test_the_retry_clicks_are_kept(placing, monkeypatch):
    """A placement has been seen to land on the SECOND click, so the retries
    must survive the switch away from unit_exist. Clears on attempt 3."""
    reads = iter([
        {"in_hand": True, "affordable": True, "price_px": 660, "hue": 45},
        {"in_hand": True, "affordable": True, "price_px": 660, "hue": 45},
        {"in_hand": False, "affordable": None, "price_px": 5, "hue": -1},
    ])
    monkeypatch.setattr(runner_blocks.vision, "read_unit_card", lambda h, s: next(reads))

    _place(placing)

    assert placing._placed_unit_positions[7] == (500, 400)
    assert placing._mouse.click.call_count >= 2, "the retry clicks were dropped"


def test_unit_exist_is_no_longer_consulted(placing, monkeypatch):
    """Belt and braces: the old signal must be gone, not merely outvoted. A
    leftover info panel satisfying it is what caused the false successes."""
    monkeypatch.setattr(runner_blocks.vision, "read_unit_card",
                        lambda h, s: {"in_hand": False, "affordable": None, "price_px": 5, "hue": -1})
    monkeypatch.setattr(runner_blocks.vision, "wait_for_image",
                        lambda *a, **k: pytest.fail("unit_exist was searched for again"))

    _place(placing)


def test_a_non_numeric_hotkey_falls_back_instead_of_inventing_a_failure(placing, monkeypatch):
    """No numeric hotkey means no card to read. That is not evidence the
    placement failed, so the old trust-the-white-tile behaviour stands."""
    monkeypatch.setattr(runner_blocks.vision, "read_unit_card",
                        lambda h, s: pytest.fail("there is no slot to read"))
    block = {"type": "place_unit", "hotkey": "q", "params": {"name": "cell", "x": 500, "y": 400}}

    placing._run_place_unit_block(1, threading.Event(), 0, 0, block, 1, "m", unit_ordinal=7)

    assert placing._placed_unit_positions[7] == (500, 400)


# ---------------------------------------------------------------------------
# Going back for units that never went down
# ---------------------------------------------------------------------------
# A placement can fail for a reason that stops being true later -- gold, most
# obviously. Live on Flower Forest: cards 5 (Y1,350) and 6 (Y1,150) still in
# hand at Y1,979, both affordable by then, Battle block list exhausted, so
# nothing went back for them. Finished 4/13 units down.

class _RetryRunner(_Runner):
    def __init__(self):
        super().__init__()
        self._unplaced_units = {}
        self._unplaced_next_retry_at = 0.0
        self.retried = []

    def _run_place_unit_block(self, hwnd, stop, left, top, block, index, macro, ordinal, **k):
        self.retried.append(block["params"]["name"])


@pytest.fixture
def retrying(monkeypatch):
    monkeypatch.setattr(runner_blocks.wm, "get_window_rect_screen", lambda h: (0, 0, 1152, 756))
    runner = _RetryRunner()
    for slot, name in ((5, "cell"), (6, "puppet")):
        runner._remember_unplaced(
            {"type": "place_unit", "hotkey": str(slot), "params": {"name": name, "x": 1, "y": 1}},
            slot, "m", slot, name, slot)
    return runner


def _cards(monkeypatch, mapping):
    monkeypatch.setattr(runner_blocks.vision, "read_unit_card", lambda h, s: mapping[s])


IN_HAND_RICH = {"in_hand": True, "affordable": True, "price_px": 660, "hue": 45}
IN_HAND_BROKE = {"in_hand": True, "affordable": False, "price_px": 400, "hue": 0}
CLEARED = {"in_hand": False, "affordable": None, "price_px": 5, "hue": -1}


def test_an_affordable_pending_unit_is_retried(retrying, monkeypatch):
    _cards(monkeypatch, {5: IN_HAND_RICH, 6: IN_HAND_RICH})

    retrying._retry_unplaced_units(1, threading.Event())

    assert retrying.retried == ["cell"], "only one placement per tick"


def test_a_unit_still_too_expensive_is_left_alone(retrying, monkeypatch):
    """It stays pending -- gold accrues, so this is 'not yet', not 'never'."""
    _cards(monkeypatch, {5: IN_HAND_BROKE, 6: IN_HAND_BROKE})

    retrying._retry_unplaced_units(1, threading.Event())

    assert retrying.retried == []
    assert set(retrying._unplaced_units) == {5, 6}


def test_the_affordable_one_is_retried_past_the_broke_one(retrying, monkeypatch):
    _cards(monkeypatch, {5: IN_HAND_BROKE, 6: IN_HAND_RICH})

    retrying._retry_unplaced_units(1, threading.Event())

    assert retrying.retried == ["puppet"]


def test_a_card_that_cleared_itself_is_dropped_not_replaced(retrying, monkeypatch):
    """The re-stage replay can put a unit down behind this. Clicking again
    would place a SECOND copy somewhere unintended."""
    _cards(monkeypatch, {5: CLEARED, 6: IN_HAND_RICH})

    retrying._retry_unplaced_units(1, threading.Event())

    assert retrying.retried == []
    assert 5 not in retrying._unplaced_units
    assert any("cleared on its own" in m for m in retrying.logs)


def test_retries_are_rate_limited(retrying, monkeypatch):
    """Without this a pending unit is re-read every poll for the whole match."""
    _cards(monkeypatch, {5: IN_HAND_RICH, 6: IN_HAND_RICH})

    retrying._retry_unplaced_units(1, threading.Event())
    retrying._retry_unplaced_units(1, threading.Event())

    assert retrying.retried == ["cell"], "second call inside the interval still fired"


def test_the_interval_does_expire(retrying, monkeypatch):
    _cards(monkeypatch, {5: IN_HAND_RICH, 6: IN_HAND_RICH})
    retrying._retry_unplaced_units(1, threading.Event())
    retrying._unplaced_next_retry_at = 0.0

    retrying._retry_unplaced_units(1, threading.Event())

    assert len(retrying.retried) == 2


def test_nothing_pending_costs_nothing(monkeypatch):
    monkeypatch.setattr(runner_blocks.vision, "read_unit_card",
                        lambda h, s: pytest.fail("read a card with nothing pending"))
    _RetryRunner()._unplaced_units = {}
    _RetryRunner()._retry_unplaced_units(1, threading.Event())


def test_a_successful_placement_clears_any_pending_entry(placing, monkeypatch):
    """Otherwise a unit that failed once, then landed on the block's own
    retry, stays on the list and gets placed a second time."""
    monkeypatch.setattr(runner_blocks.vision, "read_unit_card", lambda h, s: CLEARED)
    placing._unplaced_units = {4: {"name": "megumi"}}

    _place(placing)

    assert 4 not in placing._unplaced_units


def test_the_battle_tick_goes_back_for_them_once_the_list_is_done(monkeypatch):
    """The hook. Without it the retry logic is dead code -- which is exactly
    the state the live run was in: it knew both units had failed and never
    went back, finishing 4/13 with Y1,979 unspent."""
    runner = _RetryRunner()
    called = []
    monkeypatch.setattr(BlockOps, "_retry_unplaced_units",
                        lambda self, hwnd, stop: called.append(True))
    runner._battle_block_index = 3          # past the end of the list below
    runner._battle_block_state = {}

    runner._run_battle_blocks_tick(1, threading.Event(), [{"type": "wait_ms"}], False)

    assert called, "the exhausted Battle list never triggered a retry"
