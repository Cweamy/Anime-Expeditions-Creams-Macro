"""Expedition keeps playing when extraction will not take.

Extraction is not entirely the macro's to decide. In a matchmaking lobby the
run carries on while other players keep going, so the confirm can simply
never register no matter how cleanly it is clicked.

The old behaviour retried the whole extract chain at EVERY later checkpoint
-- once the sighting count is past accept-at it is always past it -- so a
host who never extracts cost minutes of clicking at something that was not
going to happen, repeated to the end of the match.
"""
import threading
from unittest.mock import MagicMock

from core import runner_expedition as rx
from core.runner import MacroRunner
from core.runner_constants import (
    EXPEDITION_EXTRACT_ATTEMPTS_BEFORE_PLAYING_ON as GIVE_UP_AFTER,
)


def _runner(monkeypatch, *, extract_succeeds=False):
    """A runner parked on a checkpoint that is offering Extract."""
    r = MacroRunner(MagicMock(), MagicMock(), MagicMock())
    r.logs = []
    r._log = r.logs.append
    r._checkpoint = lambda _stop: False
    r._interruptible_sleep = lambda *_a, **_k: None
    r._coords = {"screen_middle_x": 576, "screen_middle_y": 378}
    r._expedition_extract_count = 5          # well past accept-at
    r._expedition_extract_accept_at = 2
    r._exp_failed_extracts = 0
    r._exp_last_sighting_at = 0.0

    r.extract_attempts = []
    r._extract_via_mirrored_button = (
        lambda *a, **k: r.extract_attempts.append(1) or extract_succeeds)

    monkeypatch.setattr(rx.wm, "get_window_rect_screen", lambda h: (0, 0, 1152, 756))
    monkeypatch.setattr(rx.time, "sleep", lambda _s: None)
    # Offering Extract == the Continue sits well right of the centreline.
    monkeypatch.setattr(rx.vision, "find_color_run",
                        lambda h, band, mask, run: {"cx": 637, "cy": 588})
    return r


def _tick(r):
    return r._check_expedition_checkpoint_by_color(1, threading.Event())


def test_a_failing_extract_is_retried_while_attempts_remain(monkeypatch):
    r = _runner(monkeypatch)

    for _ in range(GIVE_UP_AFTER):
        _tick(r)

    assert len(r.extract_attempts) == GIVE_UP_AFTER
    assert r._exp_failed_extracts == GIVE_UP_AFTER


def test_past_the_limit_it_stops_asking_and_plays_on(monkeypatch):
    """The regression: without this the extract chain runs again at every
    remaining checkpoint, for the rest of the match."""
    r = _runner(monkeypatch)

    for _ in range(GIVE_UP_AFTER + 5):
        _tick(r)

    assert len(r.extract_attempts) == GIVE_UP_AFTER, "kept trying past the limit"
    assert any("plays on" in m for m in r.logs)


def test_playing_on_still_advances_the_checkpoint(monkeypatch):
    """Giving up on extracting must not mean giving up on the run -- the
    checkpoint still has to be continued or the match stalls there."""
    r = _runner(monkeypatch)
    for _ in range(GIVE_UP_AFTER + 1):
        _tick(r)

    clicks = [c.args for c in r._mouse.click.call_args_list]
    assert (637, 588) in clicks, "the Continue itself was never clicked"


def test_a_successful_extract_still_wins(monkeypatch):
    """The give-up path must not get in the way of extraction working."""
    r = _runner(monkeypatch, extract_succeeds=True)

    assert _tick(r) == "win"
    assert r._exp_failed_extracts == 0


def test_the_counter_is_per_match(monkeypatch):
    """A lobby that would not extract must not poison the next match."""
    r = _runner(monkeypatch)
    for _ in range(GIVE_UP_AFTER + 2):
        _tick(r)
    assert len(r.extract_attempts) == GIVE_UP_AFTER

    r._exp_failed_extracts = 0               # what _play_one_match does
    _tick(r)

    assert len(r.extract_attempts) == GIVE_UP_AFTER + 1
