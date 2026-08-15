"""Wait for Wave reads a different crop on Expedition.

read_wave reports NO MAXIMUM when the OCR text has no slash in it, because
Infinite's HUD genuinely is just "6 wave". That makes a crop which clips the
badge's left side actively dangerous rather than merely lossy: "3 / 5 wave"
arrives as "5 wave" and parses as current=5 with no maximum -- the MAX read
as the CURRENT. A Wait for Wave block then unblocks on wave 1 while logging
that it reached wave 5, and every block behind it runs early.

Observed live on Expedition with the shared WAVE_REGION, which starts 50px
right of where Expedition draws the badge.
"""
import time
from unittest.mock import MagicMock

import numpy as np

from core import runner_blocks, wave as wave_module
from core.runner import MacroRunner
from core.runner_constants import (
    EXPEDITION_WAVE_REGION,
    WAIT_WAVE_NO_COUNTER_SETTLE,
    WAVE_REGION,
)

# What the Image Manager's region tool reported on a live Expedition frame.
BADGE = (417, 16, 110, 33)


def _bounds(region):
    x, y, w, h = region
    return x, y, x + w, y + h


def test_the_expedition_region_contains_the_whole_badge():
    """Containment, not equality -- padding is fine, clipping is not."""
    rx1, ry1, rx2, ry2 = _bounds(EXPEDITION_WAVE_REGION)
    bx1, by1, bx2, by2 = _bounds(BADGE)

    assert rx1 <= bx1, f"crops {bx1 - rx1}px off the left -- loses the current-wave digit"
    assert rx2 >= bx2, f"crops {bx2 - rx2}px off the right -- loses the maximum"
    assert ry1 <= by1 and ry2 >= by2, "crops the badge vertically"


def test_the_expedition_region_does_not_reach_the_units_chip():
    """The "<n> / <max> units" chip sits directly below and is the same
    digits-and-slash shape, so anything reaching into it feeds a second
    number to the same parse."""
    _, _, _, bottom = _bounds(EXPEDITION_WAVE_REGION)
    badge_bottom = BADGE[1] + BADGE[3]

    assert bottom <= badge_bottom + 8, (
        f"reaches {bottom - badge_bottom}px below the badge, into the units chip")


def test_the_shared_region_is_left_alone():
    """Story/Raid/Infinite have been reading their badge correctly from the
    shared box for a long time, and it has not been re-measured for them.
    This change is Expedition-only on purpose."""
    assert WAVE_REGION == (467, 21, 104, 61)
    assert EXPEDITION_WAVE_REGION != WAVE_REGION


def test_both_regions_stay_inside_the_reference_window():
    for region in (WAVE_REGION, EXPEDITION_WAVE_REGION):
        x, y, w, h = region
        assert x >= 0 and y >= 0
        assert x + w <= 1152 and y + h <= 756


# ---------------------------------------------------------------------------
# Which one a match actually uses
# ---------------------------------------------------------------------------

def _runner():
    runner = MacroRunner(MagicMock(), MagicMock(), MagicMock())
    runner._log = lambda *_a, **_k: None
    return runner


def test_a_fresh_runner_defaults_to_the_shared_region():
    """Settings > Debug's battle test never goes through _play_one_match, so
    the attribute has to resolve without one."""
    assert _runner()._wave_region == WAVE_REGION


def test_wait_for_wave_reads_from_the_runners_configured_region(monkeypatch):
    """The load-bearing bit: the block must honour _wave_region, not the
    module constant. Without this the Expedition override would exist and
    never be used."""
    runner = _runner()
    runner._checkpoint = lambda _stop: False
    runner._battle_block_state = {}
    runner._wave_region = EXPEDITION_WAVE_REGION
    captured = []

    monkeypatch.setattr(runner_blocks.vision, "capture_window_region_bgr",
                        lambda _hwnd, region: captured.append(region) or None)

    runner._run_wait_wave_tick(123, {"params": {"wave": 4}}, 1)

    assert captured == [EXPEDITION_WAVE_REGION]


# ---------------------------------------------------------------------------
# Gamemodes that have no wave counter at all
# ---------------------------------------------------------------------------

def _unreadable_wave(runner, monkeypatch):
    """Wire up a Wait for Wave tick whose OCR can never read a counter."""
    runner._checkpoint = lambda _stop: False
    runner._battle_block_state = {}
    # A real ndarray, not a stub: the tick checks image.size before parsing,
    # and anything without it raises into the generic OCR-failed branch --
    # which returns False too, so a stub would make these pass vacuously.
    frame = np.zeros((33, 110, 3), dtype=np.uint8)
    monkeypatch.setattr(runner_blocks.vision, "capture_window_region_bgr",
                        lambda _hwnd, _region: frame)
    # _run_wait_wave_tick imports core.wave inside the function, so it is
    # not an attribute of runner_blocks -- patch the module itself.
    monkeypatch.setattr(wave_module, "read_wave", lambda _img: (None, None))


def test_expedition_releases_once_the_battle_is_visibly_under_way(monkeypatch):
    """Payload gamemodes count enemies, not waves, so the badge never exists.
    Without this the block waits forever and every placement behind it never
    happens -- observed live as a Wait for Wave repeating until the run was
    stopped by hand."""
    runner = _runner()
    _unreadable_wave(runner, monkeypatch)
    runner._is_expedition_match = True
    runner._last_reward_card_at = time.time() - (WAIT_WAVE_NO_COUNTER_SETTLE + 1)
    runner._last_board_disruption_at = runner._last_reward_card_at

    assert runner._run_wait_wave_tick(123, {"params": {"wave": 4}}, 1) is True


def test_it_does_not_release_on_the_same_tick_the_card_is_being_clicked(monkeypatch):
    """The settle exists so the block does not fire while the card click is
    still resolving."""
    runner = _runner()
    _unreadable_wave(runner, monkeypatch)
    runner._is_expedition_match = True
    runner._last_reward_card_at = time.time()
    runner._last_board_disruption_at = runner._last_reward_card_at

    assert runner._run_wait_wave_tick(123, {"params": {"wave": 4}}, 1) is False


def test_no_card_yet_means_no_evidence_the_battle_started(monkeypatch):
    """Cards drop for kills, so one cannot appear before the fighting does.
    Releasing with none seen would fire the block during setup."""
    runner = _runner()
    _unreadable_wave(runner, monkeypatch)
    runner._is_expedition_match = True
    runner._last_reward_card_at = 0.0

    assert runner._run_wait_wave_tick(123, {"params": {"wave": 4}}, 1) is False


def test_story_keeps_waiting_for_a_real_number(monkeypatch):
    """Story/Raid always have a badge, so an unreadable one is a detection
    problem worth surfacing -- not something to work around."""
    runner = _runner()
    _unreadable_wave(runner, monkeypatch)
    runner._is_expedition_match = False
    runner._last_reward_card_at = time.time() - 999
    runner._last_board_disruption_at = runner._last_reward_card_at

    assert runner._run_wait_wave_tick(123, {"params": {"wave": 4}}, 1) is False


# ---------------------------------------------------------------------------
# A mid-run re-stage empties the board
# ---------------------------------------------------------------------------

def _restage_runner():
    runner = _runner()
    runner._battle_block_index = 4
    runner._battle_block_state = {"something": 1}
    runner._battle_replayed = False
    runner._last_board_disruption_at = 0.0
    return runner


def test_a_restage_replays_the_battle_phase_from_the_top(monkeypatch):
    """The units run off the board, so the placements have to run again."""
    runner = _restage_runner()

    runner._replay_battle_after_restage()

    assert runner._battle_block_index == 0
    assert runner._battle_block_state == {}


def test_a_restage_restarts_the_quiet_period(monkeypatch):
    """A Wait for Wave at the top of the phase must hold its full wait again
    -- the round is still re-staging and gold is still accruing."""
    runner = _restage_runner()

    runner._replay_battle_after_restage()

    assert time.time() - runner._last_board_disruption_at < 1.0


def test_only_the_first_restage_replays(monkeypatch):
    """Re-arming on every Start Game popup would let a chatty one rewind the
    phase indefinitely -- the placements would keep restarting and never
    finish."""
    runner = _restage_runner()

    runner._replay_battle_after_restage()
    assert runner._battle_block_index == 0

    runner._battle_block_index = 4          # phase has moved on again
    runner._replay_battle_after_restage()

    assert runner._battle_block_index == 4, "a later re-stage must not rewind"


def test_the_quiet_period_restarts_on_every_disruption(monkeypatch):
    """Deliberate: a card means the round is still churning, so the clock
    measures 'nothing has happened lately', not 'time since it began'."""
    runner = _runner()
    _unreadable_wave(runner, monkeypatch)
    runner._is_expedition_match = True
    runner._last_reward_card_at = time.time() - 999      # battle is live
    runner._last_board_disruption_at = time.time() - 1   # but something just happened

    assert runner._run_wait_wave_tick(123, {"params": {"wave": 4}}, 1) is False

    # Fresh block state: the tick sets its own next_check after a failed
    # read, so a second call in the same instant would short-circuit on the
    # poll gate rather than on the thing under test.
    runner._battle_block_state = {}
    runner._last_board_disruption_at = time.time() - (WAIT_WAVE_NO_COUNTER_SETTLE + 1)
    assert runner._run_wait_wave_tick(123, {"params": {"wave": 4}}, 1) is True


def test_each_match_gets_its_own_replay(monkeypatch):
    """Per match, not per session -- every Repeat Stage is a fresh match and
    deserves its own replay. _play_one_match clears the flag; this pins that
    a cleared flag re-enables it."""
    runner = _restage_runner()
    runner._replay_battle_after_restage()
    runner._battle_block_index = 4

    runner._battle_replayed = False          # what _play_one_match does
    runner._replay_battle_after_restage()

    assert runner._battle_block_index == 0


# ---------------------------------------------------------------------------
# The hard ceiling: never hold forever
# ---------------------------------------------------------------------------

def test_an_unreadable_counter_releases_after_the_ceiling_even_with_no_card(monkeypatch):
    """The regression, and the reason the ceiling exists.

    The quiet-period release is gated on a reward card having been seen,
    because a card proves the fighting started. Cards drop for KILLS -- so a
    run going badly produces none, the gate never arms, and the deferred
    placements never run. That is circular on Expedition: the team is short
    the units that would earn the kills that would produce the card.

    Observed live as two runs that sat on "couldn't read the wave counter"
    for forty seconds, placed three of six units, and lost in about a
    minute, against ten-minute wins whenever the counter did read.
    """
    from core.runner_constants import WAIT_WAVE_UNREADABLE_CEILING
    runner = _runner()
    _unreadable_wave(runner, monkeypatch)
    runner._is_expedition_match = True
    runner._last_reward_card_at = 0.0          # no card has EVER been seen
    runner._last_board_disruption_at = 0.0
    runner._battle_block_state = {"unreadable_since": time.time() - (WAIT_WAVE_UNREADABLE_CEILING + 1)}

    assert runner._run_wait_wave_tick(123, {"params": {"wave": 4}}, 1) is True


def test_it_does_not_release_before_the_ceiling(monkeypatch):
    from core.runner_constants import WAIT_WAVE_UNREADABLE_CEILING
    runner = _runner()
    _unreadable_wave(runner, monkeypatch)
    runner._is_expedition_match = True
    runner._last_reward_card_at = 0.0
    runner._last_board_disruption_at = 0.0
    runner._battle_block_state = {"unreadable_since": time.time() - (WAIT_WAVE_UNREADABLE_CEILING - 5)}

    assert runner._run_wait_wave_tick(123, {"params": {"wave": 4}}, 1) is False


def test_story_is_not_released_by_the_ceiling(monkeypatch):
    """Story/Raid always have a badge, so an unreadable one there is a
    detection problem worth surfacing rather than working around."""
    from core.runner_constants import WAIT_WAVE_UNREADABLE_CEILING
    runner = _runner()
    _unreadable_wave(runner, monkeypatch)
    runner._is_expedition_match = False
    runner._battle_block_state = {"unreadable_since": time.time() - (WAIT_WAVE_UNREADABLE_CEILING * 3)}

    assert runner._run_wait_wave_tick(123, {"params": {"wave": 4}}, 1) is False


def test_a_successful_read_restarts_the_ceiling_clock(monkeypatch):
    """A counter that reads, then goes unreadable later, must get its own
    full allowance rather than inheriting failures from earlier in the run."""
    runner = _runner()
    runner._checkpoint = lambda _stop: False
    runner._is_expedition_match = True
    runner._battle_block_state = {"unreadable_since": 1.0}
    frame = np.zeros((33, 110, 3), dtype=np.uint8)
    monkeypatch.setattr(runner_blocks.vision, "capture_window_region_bgr",
                        lambda _hwnd, _region: frame)
    monkeypatch.setattr(wave_module, "read_wave", lambda _img: (1, 5))

    runner._run_wait_wave_tick(123, {"params": {"wave": 4}}, 1)

    assert "unreadable_since" not in runner._battle_block_state
