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

    assert runner._run_wait_wave_tick(123, {"params": {"wave": 4}}, 1) is True


def test_it_does_not_release_on_the_same_tick_the_card_is_being_clicked(monkeypatch):
    """The settle exists so the block does not fire while the card click is
    still resolving."""
    runner = _runner()
    _unreadable_wave(runner, monkeypatch)
    runner._is_expedition_match = True
    runner._last_reward_card_at = time.time()

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

    assert runner._run_wait_wave_tick(123, {"params": {"wave": 4}}, 1) is False
