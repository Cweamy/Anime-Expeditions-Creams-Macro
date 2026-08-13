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
from unittest.mock import MagicMock

from core import runner_blocks
from core.runner import MacroRunner
from core.runner_constants import EXPEDITION_WAVE_REGION, WAVE_REGION

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
