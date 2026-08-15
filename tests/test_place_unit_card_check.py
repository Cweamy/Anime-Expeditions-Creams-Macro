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
                        lambda h, s, n=None: {"in_hand": False, "affordable": None, "price_px": 6, "hue": -1})

    _place(placing)

    assert placing._placed_unit_positions[7] == (500, 400)
    assert any("placed at" in m for m in placing.logs)


def test_a_lit_but_affordable_card_is_trusted_as_placed(placing, monkeypatch):
    """A lit card means "this unit still has a copy you could place", NOT
    "this placement failed" -- a multi-copy unit keeps its price after every
    copy goes down. Live on East Town: Salmon Sorcerer 3 was called a failure
    while already on the board, and the retry pass kept placing more until the
    game refused with "Max placement limit reached!". Only an unaffordable
    card proves nothing was placed."""
    monkeypatch.setattr(runner_blocks.vision, "read_unit_card",
                        lambda h, s, n=None: {"in_hand": True, "affordable": True, "price_px": 660, "hue": 45})
    placing._unplaced_units = {}

    _place(placing)

    assert placing._placed_unit_positions[7] == (500, 400)
    assert not placing._unplaced_units, "queued a retry on an ambiguous card"
    assert not any("did NOT place" in m for m in placing.logs)


def test_being_unable_to_afford_it_says_so(placing, monkeypatch):
    monkeypatch.setattr(runner_blocks.vision, "read_unit_card",
                        lambda h, s, n=None: {"in_hand": True, "affordable": False, "price_px": 400, "hue": 0})

    _place(placing)

    assert any("cannot afford it yet" in m for m in placing.logs)


def test_a_pre_start_placement_is_checked_too(placing, monkeypatch):
    """Pre Start ran with verify=False and so logged every placement as a
    success on no evidence -- that is how the unaffordable unit in the live
    run was reported placed. The card read is cheap enough to always do."""
    monkeypatch.setattr(runner_blocks.vision, "read_unit_card",
                        lambda h, s, n=None: {"in_hand": True, "affordable": False, "price_px": 400, "hue": 0})
    placing._unplaced_units = {}

    _place(placing, verify=False)

    assert 7 not in placing._placed_unit_positions
    assert 4 in placing._unplaced_units, "an unaffordable unit still has to be followed up"


def test_a_pre_start_unit_still_gets_a_recorded_position(placing, monkeypatch):
    """Auto Upgrade Unit finds its target through _placed_unit_positions. When
    a lit card suppressed the recording, every Pre Start unit became invisible
    to it -- live, the three Senku placed in Pre Start went unupgraded while
    Kenpachi and Megumi, placed in Battle, upgraded fine."""
    monkeypatch.setattr(runner_blocks.vision, "read_unit_card",
                        lambda h, s, n=None: {"in_hand": True, "affordable": True, "price_px": 660, "hue": 45})
    placing._unplaced_units = {}

    _place(placing, verify=False)

    assert placing._placed_unit_positions[7] == (500, 400),         "no position recorded -- its Auto Upgrade block would silently skip"


def test_the_retry_clicks_are_kept(placing, monkeypatch):
    """A placement has been seen to land on the SECOND click, so the retries
    must survive the switch away from unit_exist. Clears on attempt 3."""
    reads = iter([
        {"in_hand": True, "affordable": True, "price_px": 660, "hue": 45},
        {"in_hand": True, "affordable": True, "price_px": 660, "hue": 45},
        {"in_hand": False, "affordable": None, "price_px": 5, "hue": -1},
    ])
    monkeypatch.setattr(runner_blocks.vision, "read_unit_card", lambda h, s, n=None: next(reads))

    _place(placing)

    assert placing._placed_unit_positions[7] == (500, 400)
    assert placing._mouse.click.call_count >= 2, "the retry clicks were dropped"


def test_unit_exist_is_no_longer_consulted(placing, monkeypatch):
    """Belt and braces: the old signal must be gone, not merely outvoted. A
    leftover info panel satisfying it is what caused the false successes."""
    monkeypatch.setattr(runner_blocks.vision, "read_unit_card",
                        lambda h, s, n=None: {"in_hand": False, "affordable": None, "price_px": 5, "hue": -1})
    monkeypatch.setattr(runner_blocks.vision, "wait_for_image",
                        lambda *a, **k: pytest.fail("unit_exist was searched for again"))

    _place(placing)


def test_a_non_numeric_hotkey_falls_back_instead_of_inventing_a_failure(placing, monkeypatch):
    """No numeric hotkey means no card to read. That is not evidence the
    placement failed, so the old trust-the-white-tile behaviour stands."""
    monkeypatch.setattr(runner_blocks.vision, "read_unit_card",
                        lambda h, s, n=None: pytest.fail("there is no slot to read"))
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
    monkeypatch.setattr(runner_blocks.vision, "read_unit_card", lambda h, s, n=None: mapping[s])


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
                        lambda h, s, n=None: pytest.fail("read a card with nothing pending"))
    _RetryRunner()._unplaced_units = {}
    _RetryRunner()._retry_unplaced_units(1, threading.Event())


def test_a_successful_placement_clears_any_pending_entry(placing, monkeypatch):
    """Otherwise a unit that failed once, then landed on the block's own
    retry, stays on the list and gets placed a second time."""
    monkeypatch.setattr(runner_blocks.vision, "read_unit_card", lambda h, s, n=None: CLEARED)
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


def test_a_quick_place_in_battle_is_not_called_staged(placing, monkeypatch):
    """"staged ... will confirm once the round starts" is Pre Start wording.
    It was keyed on skip_verify, which is ALSO true for a quick-place chain --
    so consecutive same-hotkey placements in BATTLE reported themselves as
    staged. Seen live on East Town for Salmon Sorcerer 2 and 3."""
    monkeypatch.setattr(runner_blocks.vision, "read_unit_card",
                        lambda h, s, n=None: {"in_hand": True, "affordable": False, "price_px": 400, "hue": 0})
    placing._unplaced_units = {}
    block = {"type": "place_unit", "hotkey": "2", "params": {"name": "Salmon Sorcerer 2", "x": 500, "y": 400}}

    # next_is_same_unit=True is what starts a quick-place chain.
    placing._run_place_unit_block(1, threading.Event(), 0, 0, block, 1, "m",
                                   unit_ordinal=5, next_is_same_unit=True, verify=True)

    assert not any("staged" in m for m in placing.logs), "Battle placement called itself staged"


def test_the_retry_gives_up_eventually(retrying, monkeypatch):
    """A lit card is not proof the unit is missing, so the retry has to be
    bounded. Uncapped, a multi-copy card that never clears was re-placed for
    the rest of the match -- live that ran until "Max placement limit
    reached!"."""
    from core.runner_constants import UNPLACED_RETRY_MAX_ATTEMPTS as CAP
    _cards(monkeypatch, {5: IN_HAND_RICH, 6: IN_HAND_RICH})
    del retrying._unplaced_units[6]                 # one slot, to keep the count clean

    for _ in range(CAP + 3):
        retrying._unplaced_next_retry_at = 0.0
        retrying._retry_unplaced_units(1, threading.Event())

    assert len(retrying.retried) == CAP, f"retried {len(retrying.retried)} times, cap is {CAP}"
    assert 5 not in retrying._unplaced_units
    assert any("giving up on it" in m for m in retrying.logs)


# ---------------------------------------------------------------------------
# Moving it around instead of giving up
# ---------------------------------------------------------------------------

def test_no_valid_tile_is_queued_not_abandoned(placing, monkeypatch):
    """No click happened, so the unit is certainly not down -- and for a
    multi-copy unit this is the ONLY failure still detectable, since the card
    cannot tell placed from not. Dropping the block is why three Cells saved
    16px apart only ever landed one."""
    monkeypatch.setattr(BlockOps, "_find_valid_place_spot", lambda self, *a, **k: None)
    monkeypatch.setattr(runner_blocks.vision, "read_unit_card",
                        lambda h, s, n=None: pytest.fail("no click happened; no card to read"))
    placing._unplaced_units = {}

    _place(placing)

    assert placing._unplaced_units[4]["reason"] == "no_tile"
    assert any("queued to retry nearby" in m for m in placing.logs)


def test_the_first_retry_reuses_the_saved_spot():
    """Usually the only thing that changed is that gold arrived."""
    block = {"params": {"x": 500, "y": 400}}
    assert BlockOps._nudged_block(block, 1) is block


@pytest.mark.parametrize("attempt", [2, 3, 4, 5])
def test_later_retries_step_somewhere_new(attempt):
    from core.runner_constants import UNPLACED_RETRY_NUDGE as N
    block = {"params": {"x": 500, "y": 400}}
    p = BlockOps._nudged_block(block, attempt)["params"]
    assert (abs(p["x"] - 500), abs(p["y"] - 400)) in ((N, 0), (0, N))


def test_every_direction_is_tried_before_repeating():
    block = {"params": {"x": 500, "y": 400}}
    spots = {(BlockOps._nudged_block(block, a)["params"]["x"],
              BlockOps._nudged_block(block, a)["params"]["y"]) for a in range(1, 6)}
    assert len(spots) == 5, "a retry direction is wasted on a duplicate spot"


def test_nudging_never_mutates_the_template_block():
    """The block belongs to the loaded template. Moving its coordinate would
    shift the saved spot for the rest of the run and every later repeat."""
    block = {"params": {"x": 500, "y": 400}}
    BlockOps._nudged_block(block, 3)
    assert block["params"] == {"x": 500, "y": 400}


def test_a_retry_actually_aims_at_the_nudged_spot(retrying, monkeypatch):
    from core.runner_constants import UNPLACED_RETRY_NUDGE as N
    _cards(monkeypatch, {5: IN_HAND_RICH, 6: IN_HAND_RICH})
    del retrying._unplaced_units[6]
    aimed = []
    retrying._run_place_unit_block = (
        lambda h, s, l, t, b, i, m, o, **k: aimed.append((b["params"]["x"], b["params"]["y"])))

    for _ in range(3):
        retrying._unplaced_next_retry_at = 0.0
        retrying._retry_unplaced_units(1, threading.Event())

    assert aimed[0] == (1, 1), "first retry should redo the saved spot"
    assert len(set(aimed)) == 3, f"retries did not move: {aimed}"
    assert any(abs(x - 1) == N or abs(y - 1) == N for x, y in aimed[1:])


# ---------------------------------------------------------------------------
# Where the hotbar actually is
# ---------------------------------------------------------------------------
# The bar is CENTRED, and the slot count varies by mode -- an Expedition frame
# showed ten slots, a Story frame six. Assuming ten on a six-slot bar puts
# every read two slots left, and slots 1-2 land on bare map: on a real Story
# capture at Y600 it called Senku (Y550) unaffordable and reported two cards
# that were not there.

@pytest.mark.parametrize("slot,count,centre", [
    (6, 10, 611),   # measured: x 580-642
    (1, 6, 401),    # measured: x 362-439
    (6, 6, 751),    # measured: x 715-790
])
def test_card_geometry_matches_real_captures(slot, count, centre):
    got = vision.card_left_edge(slot, count) + vision.CARD_W / 2
    assert abs(got - centre) <= 2, f"slot {slot} of {count}: model {got}, measured {centre}"


def test_a_six_slot_bar_is_not_read_as_a_ten_slot_one():
    assert vision.card_left_edge(1, 6) != vision.card_left_edge(1, 10)


def test_a_slot_past_the_end_of_the_bar_reads_as_nothing(monkeypatch):
    monkeypatch.setattr(vision, "capture_game_bgr",
                        lambda h, region=None: pytest.fail("no such slot to capture"))
    assert vision.read_unit_card(0, 7, slot_count=6)["in_hand"] is False


def test_expedition_keeps_the_ten_slot_bar():
    r = _Runner()
    r._is_expedition_match = True
    assert r._hotbar_slot_count("anything") == vision.CARD_DEFAULT_SLOTS


def test_story_uses_the_loadouts_highest_hotkey(monkeypatch):
    from core import templates
    r = _Runner()
    r._is_expedition_match = False
    monkeypatch.setattr(templates, "load_template", lambda n: {"blocks": {"prestart": [
        {"type": "place_unit", "hotkey": "1"}, {"type": "place_unit", "hotkey": "6"},
        {"type": "place_unit", "hotkey": "3"}, {"type": "wait_ms"}]}})

    assert r._hotbar_slot_count("t") == 6


def test_an_unreadable_template_falls_back_to_the_widest_bar(monkeypatch):
    from core import templates
    r = _Runner()
    r._is_expedition_match = False
    monkeypatch.setattr(templates, "load_template",
                        lambda n: (_ for _ in ()).throw(OSError("gone")))

    assert r._hotbar_slot_count("t") == vision.CARD_DEFAULT_SLOTS


# ---------------------------------------------------------------------------
# A deferred placement must not lose its priority
# ---------------------------------------------------------------------------
# The Auto Upgrade block sits right after its placement in the routine, so a
# placement that gets deferred (no gold yet) has its upgrade run one tick
# later, find no unit, and skip for good. Live on East Town: Salmon Sorcerer
# and Kenpachi were both placed by the retry and both finished the match with
# no priority set.

UPGRADE_BLOCK = {"type": "auto_upgrade_unit",
                 "params": {"index": "5", "priority": 4, "input": "click"}, "once": False}


@pytest.fixture
def deferring(retrying, monkeypatch):
    from core import templates
    monkeypatch.setattr(templates, "load_template",
                        lambda n: {"blocks": {"prestart": [UPGRADE_BLOCK]}})
    retrying.upgraded = []
    retrying._run_auto_upgrade_unit_tick = (
        lambda h, s, b, n: retrying.upgraded.append(b["params"]["priority"]))
    # the retry's placement succeeds -> the slot stops being pending
    def place(h, s, l, t, b, i, m, o, **k):
        retrying.retried.append(b["params"]["name"])
        retrying._unplaced_units.pop(5, None)
    retrying._run_place_unit_block = place
    return retrying


def test_a_unit_placed_on_retry_gets_its_priority(deferring, monkeypatch):
    _cards(monkeypatch, {5: IN_HAND_RICH, 6: IN_HAND_RICH})

    deferring._retry_unplaced_units(1, threading.Event())

    assert deferring.retried == ["cell"]
    assert deferring.upgraded == [4], "placed on retry but never given its priority"


def test_a_retry_that_did_not_place_does_not_upgrade_thin_air(retrying, monkeypatch):
    from core import templates
    monkeypatch.setattr(templates, "load_template",
                        lambda n: {"blocks": {"prestart": [UPGRADE_BLOCK]}})
    retrying.upgraded = []
    retrying._run_auto_upgrade_unit_tick = (
        lambda h, s, b, n: retrying.upgraded.append(b["params"]["priority"]))
    _cards(monkeypatch, {5: IN_HAND_RICH, 6: IN_HAND_RICH})   # stays pending

    retrying._retry_unplaced_units(1, threading.Event())

    assert retrying.upgraded == [], "upgraded a unit the retry failed to place"


def test_no_upgrade_block_for_that_unit_is_fine(deferring, monkeypatch):
    from core import templates
    monkeypatch.setattr(templates, "load_template",
                        lambda n: {"blocks": {"prestart": []}})
    deferring._upgrade_block_cache = {}
    _cards(monkeypatch, {5: IN_HAND_RICH, 6: IN_HAND_RICH})

    deferring._retry_unplaced_units(1, threading.Event())

    assert deferring.upgraded == []
    assert deferring.retried == ["cell"]
