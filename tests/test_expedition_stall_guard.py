"""The Expedition checkpoint stall guard.

Every step of the checkpoint chain is bounded on its own -- the follow-up
hunt gets EXPEDITION_WAVE_TIMEOUT, the confirm chain gets its own retries --
but nothing used to notice the whole chain repeating. A Continue that never
clears was re-found and re-clicked on every poll, and the run only escaped
at MATCH_RESULT_TIMEOUT, thirty minutes later. Reported from a real run:
the same two log lines, at the same two coordinates, for the entire match.
"""
import threading
from unittest.mock import MagicMock

from core import runner as runner_module
from core import runner_expedition as expedition_module
from core.runner import MacroRunner
from core.runner_constants import (
    EXPEDITION_INTERCEPT_TIMEOUT,
    EXPEDITION_STALL_TIMEOUT,
    MATCH_RESULT_TIMEOUT,
)


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _runner():
    runner = MacroRunner(MagicMock(), MagicMock(), MagicMock())
    runner._log = lambda *_a, **_k: None
    runner._set_status = lambda **_k: None
    runner._checkpoint = lambda _stop: False
    runner._tick_loop_phases = lambda *_a, **_k: None
    runner._infinite_wave_limit = lambda _task: None
    runner._save_debug_screenshot_unconditional = lambda *_a, **_k: None
    return runner


# ---------------------------------------------------------------------------
# The streak bookkeeping
# ---------------------------------------------------------------------------

def test_first_sighting_starts_the_clock_and_later_ones_do_not_restart_it(monkeypatch):
    """The clock has to date from the FIRST poll that saw the checkpoint.
    Restarting it on every sighting would push the deadline out forever,
    which is precisely the bug -- a stall that re-arms itself never expires."""
    runner = _runner()
    clock = _Clock()
    monkeypatch.setattr(runner_module.time, "time", clock.time)
    monkeypatch.setattr(expedition_module.time, "time", clock.time)

    runner._note_checkpoint_seen()
    started_at = runner._exp_checkpoint_since
    clock.advance(60.0)
    runner._note_checkpoint_seen()

    assert runner._exp_checkpoint_since == started_at
    assert runner._exp_checkpoint_streak == 2


def test_a_poll_with_no_checkpoint_clears_the_streak(monkeypatch):
    """A poll that finds no Continue is the only proof the last one actually
    cleared, so it is what resets the clock."""
    runner = _runner()
    clock = _Clock()
    monkeypatch.setattr(expedition_module.time, "time", clock.time)

    runner._note_checkpoint_seen()
    clock.advance(EXPEDITION_STALL_TIMEOUT * 2)
    assert runner._expedition_checkpoint_stalled() is True

    runner._note_checkpoint_cleared()

    assert runner._exp_checkpoint_streak == 0
    assert runner._exp_checkpoint_since == 0.0
    assert runner._expedition_checkpoint_stalled() is False


def test_stall_is_only_reported_once_the_full_timeout_has_passed(monkeypatch):
    runner = _runner()
    clock = _Clock()
    monkeypatch.setattr(expedition_module.time, "time", clock.time)

    runner._note_checkpoint_seen()
    clock.advance(EXPEDITION_STALL_TIMEOUT - 1.0)
    assert runner._expedition_checkpoint_stalled() is False

    clock.advance(1.0)
    assert runner._expedition_checkpoint_stalled() is True


def test_never_seeing_a_checkpoint_is_not_a_stall(monkeypatch):
    """A quiet mid-wave run must not trip the guard just by lasting a while."""
    runner = _runner()
    clock = _Clock()
    monkeypatch.setattr(expedition_module.time, "time", clock.time)

    clock.advance(EXPEDITION_STALL_TIMEOUT * 3)

    assert runner._expedition_checkpoint_stalled() is False


# ---------------------------------------------------------------------------
# The poll loop actually bailing out
# ---------------------------------------------------------------------------

def _drive_expedition_poll(monkeypatch, runner, clock, on_poll):
    """Run _wait_for_match_result in expedition mode, calling on_poll() for
    each poll to decide what the checkpoint check does that tick."""
    monkeypatch.setattr(runner_module.time, "time", clock.time)
    monkeypatch.setattr(runner_module.time, "sleep", lambda _s: None)
    monkeypatch.setattr(expedition_module.time, "time", clock.time)
    monkeypatch.setattr(runner_module.vision, "find_image", lambda *_a, **_k: None)
    runner._interruptible_sleep = lambda seconds, _stop=None: clock.advance(seconds)
    runner._check_expedition_wave_result = lambda _hwnd, _stop: on_poll()
    return runner._wait_for_match_result(123, threading.Event(), mode="expedition")


def test_a_checkpoint_that_never_clears_gives_up_instead_of_running_to_the_match_timeout(monkeypatch):
    """The regression. Before the guard this returned only after
    MATCH_RESULT_TIMEOUT (30 min); it must now bail at roughly the stall
    timeout instead, having spent nowhere near the match timeout."""
    runner = _runner()
    clock = _Clock()
    started_at = clock.now

    def stuck_checkpoint():
        # What the real chain costs per unproductive cycle: the follow-up
        # hunt plus its settle.
        clock.advance(10.0)
        runner._note_checkpoint_seen()
        return None

    result = _drive_expedition_poll(monkeypatch, runner, clock, stuck_checkpoint)
    elapsed = clock.now - started_at

    assert result is None
    assert elapsed >= EXPEDITION_STALL_TIMEOUT
    assert elapsed < MATCH_RESULT_TIMEOUT
    # And it is genuinely early, not merely under the wire.
    assert elapsed < MATCH_RESULT_TIMEOUT / 4


def test_a_run_that_keeps_progressing_is_never_abandoned(monkeypatch):
    """Checkpoints that clear between waves must not accumulate toward the
    stall, no matter how long the match runs. This one goes the full
    MATCH_RESULT_TIMEOUT and still exits via the normal timeout path."""
    runner = _runner()
    clock = _Clock()
    polls = {"n": 0}

    def healthy_checkpoint():
        polls["n"] += 1
        clock.advance(10.0)
        # A checkpoint every so often, and mid-wave polls in between.
        if polls["n"] % 5 == 0:
            runner._note_checkpoint_seen()
        else:
            runner._note_checkpoint_cleared()
        return None

    result = _drive_expedition_poll(monkeypatch, runner, clock, healthy_checkpoint)

    assert result is None  # fell out of the loop on MATCH_RESULT_TIMEOUT
    assert clock.now - 1000.0 >= MATCH_RESULT_TIMEOUT


def test_back_to_back_checkpoints_are_tolerated_while_the_run_still_advances(monkeypatch):
    """Two or three polls in a row can legitimately find a Continue -- the
    banner lingers. Only a checkpoint that stays up past the whole timeout
    counts, so a run whose streak keeps getting broken survives."""
    runner = _runner()
    clock = _Clock()
    polls = {"n": 0}

    def lingering_banner():
        polls["n"] += 1
        clock.advance(10.0)
        # Three sightings, then it clears -- repeatedly, for the whole match.
        if polls["n"] % 4 == 3:
            runner._note_checkpoint_cleared()
        else:
            runner._note_checkpoint_seen()
        return None

    result = _drive_expedition_poll(monkeypatch, runner, clock, lingering_banner)

    assert result is None
    assert clock.now - 1000.0 >= MATCH_RESULT_TIMEOUT


# ---------------------------------------------------------------------------
# Popups and reward cards, which take the poll before the checkpoint is read
# ---------------------------------------------------------------------------

def test_an_intercepted_poll_holds_the_checkpoint_clock_instead_of_aging_it(monkeypatch):
    """A card in the way means nobody LOOKED at the checkpoint -- it may well
    have cleared meanwhile. Ageing the stall clock through those polls would
    abandon a run for a checkpoint that is not there."""
    runner = _runner()
    clock = _Clock()
    monkeypatch.setattr(expedition_module.time, "time", clock.time)

    runner._note_checkpoint_seen()
    for _ in range(100):                       # 100 * 10s = far past the timeout
        clock.advance(10.0)
        runner._note_checkpoint_intercepted()

    assert runner._expedition_checkpoint_stalled() is False


def test_intercepts_do_not_reset_the_checkpoint_clock_either(monkeypatch):
    """Held, not reset. Time the checkpoint was genuinely OBSERVED up before
    the cards started still counts once they stop -- so a stall that a burst
    of level-ups interrupts is still caught, just later."""
    runner = _runner()
    clock = _Clock()
    monkeypatch.setattr(expedition_module.time, "time", clock.time)

    # 290s of the checkpoint actually being seen, one poll at a time.
    runner._note_checkpoint_seen()
    for _ in range(29):
        clock.advance(10.0)
        runner._note_checkpoint_seen()
    assert runner._expedition_checkpoint_stalled() is False

    # 600s of cards taking every poll -- none of it ages the clock.
    for _ in range(60):
        clock.advance(10.0)
        runner._note_checkpoint_intercepted()
    assert runner._expedition_checkpoint_stalled() is False

    # One more observed poll tips it over the 300s of real evidence.
    clock.advance(10.0)
    runner._note_checkpoint_seen()

    assert runner._expedition_checkpoint_stalled() is True


def test_endless_intercepts_are_their_own_stall(monkeypatch):
    """Handling cards IS useful work, so this must not fire on a burst -- but
    a run that never gets past them is not progressing either."""
    runner = _runner()
    clock = _Clock()
    monkeypatch.setattr(expedition_module.time, "time", clock.time)

    runner._note_checkpoint_intercepted()
    clock.advance(EXPEDITION_INTERCEPT_TIMEOUT - 1.0)
    assert runner._expedition_intercepts_stalled() is False

    clock.advance(1.0)
    assert runner._expedition_intercepts_stalled() is True


def test_reaching_the_checkpoint_search_ends_a_run_of_intercepts(monkeypatch):
    """A normal burst of level-ups clears and the run carries on."""
    runner = _runner()
    clock = _Clock()
    monkeypatch.setattr(expedition_module.time, "time", clock.time)

    for _ in range(20):
        clock.advance(10.0)
        runner._note_checkpoint_intercepted()
    runner._note_checkpoint_cleared()

    assert runner._exp_intercept_streak == 0
    assert runner._expedition_intercepts_stalled() is False


def test_a_card_that_never_clears_still_ends_the_match(monkeypatch):
    """The whole point of the separate cap: an undismissable card used to be
    an unbounded loop, and must not become one again now that it holds the
    checkpoint clock rather than ageing it."""
    runner = _runner()
    clock = _Clock()
    started_at = clock.now

    def always_intercepted():
        clock.advance(10.0)
        runner._note_checkpoint_intercepted()
        return None

    result = _drive_expedition_poll(monkeypatch, runner, clock, always_intercepted)
    elapsed = clock.now - started_at

    assert result is None
    assert elapsed >= EXPEDITION_INTERCEPT_TIMEOUT
    assert elapsed < MATCH_RESULT_TIMEOUT / 4
