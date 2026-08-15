"""Native Expedition encounter handling (_handle_expedition_encounter)."""
import threading
import time
from unittest.mock import MagicMock

import pytest

from core.runner_expedition import ExpeditionOps
from core.runner_constants import (ENCOUNTER_COOLDOWN, ENCOUNTER_DIALOGUE_ADVANCE_CLICK,
                                   ENCOUNTER_DIALOGUE_CLICKS,
                                   ENCOUNTER_TELEPORT_SPAWN_CLICK)


class _Runner(ExpeditionOps):
    def __init__(self, task=None):
        self._current_task = task
        self._mouse = MagicMock()
        self._keyboard = MagicMock()
        self.logs = []
        self.clicked_images = []
        self.settings_closed = 0
        self.slept = []

    def _log(self, m):
        self.logs.append(m)

    def _set_status(self, **kw):
        pass

    def _checkpoint(self, stop_event):
        return False

    def _interruptible_sleep(self, seconds, stop_event=None):
        # Recorded, not slept: the settle must be DEFERRED rather than waited
        # on, so a test can assert this stays empty.
        self.slept.append(seconds)

    def _close_settings_if_open(self, hwnd, stop_event):
        # The runner's own Settings close -- clicks nav_settings_on, which
        # matches far more reliably than the panel's small red X.
        self.settings_closed += 1

    def _click_found_image(self, hwnd, name, timeout, stop_event=None, **kw):
        self.clicked_images.append(name)
        return {"cx": 1, "cy": 1, "score": 1.0}


def _settled():
    """State as it stands once the pre-menu settle has already elapsed -- the
    handler defers on first sighting, so tests of the ACTIONS start here."""
    return {"handled_at": 0.0, "seen_at": 1.0}


def _wire(monkeypatch, *, marker=True, prompt=True, events=(("w", "down", 0.0),), mapping=None,
          settings_stuck=False, blocking_modals=0, runner=None,
          closes_after=1, speak_rounds=1, dialogue_buttons=True, buttons_clicked=None):
    """runner: pass one to make modal serving PHASE-AWARE -- the handler clears
    modals both before opening Settings and again before the blind teleport
    click, and a double that empties on the first wait cannot show the second
    is guarded."""
    from core import runner_expedition as rx
    replayed = []
    state = {"modals": blocking_modals, "settings_checks": 0, "phase": 1, "speak_checks": 0}
    monkeypatch.setattr(rx.wm, "get_window_rect_screen", lambda h: (10, 20, 1152, 756))

    def find_image(h, n, **k):
        # Name-aware: the handler asks about three different things, and a
        # double that answers "found" to all of them hides real behaviour.
        if n == "expedition_encounter":
            return {"cx": 430, "cy": 80, "score": 0.96} if marker else None
        if n == "nav_settings_on":
            # First check is the "did Settings open" one. After that each check
            # is a close attempt, and it reports open until closes_after.
            state["settings_checks"] += 1
            if state["settings_checks"] == 1:
                return {"cx": 275, "cy": 76, "score": 1.0}
            if settings_stuck:
                return {"cx": 275, "cy": 76, "score": 1.0}
            closed_on = state["settings_checks"] - 1
            return None if closed_on >= closes_after else {"cx": 275, "cy": 76, "score": 1.0}
        if n == "expedition_speak":
            # Post-dialogue check: still open until speak_rounds have run.
            state["speak_checks"] += 1
            return None if state["speak_checks"] >= speak_rounds else {"cx": 600, "cy": 400, "score": 0.98}
        if n == "select upgrade card":
            # Phase 2 begins once Settings has been clicked; refill so the
            # teleport-click guard has something to clear too.
            phase = 2 if (runner is not None and "nav_settings" in runner.clicked_images) else 1
            if phase != state["phase"]:
                state["phase"] = phase
                state["modals"] = blocking_modals
            if state["modals"]:
                state["modals"] -= 1
                return {"cx": 576, "cy": 400, "score": 0.94}
            return None
        return None

    monkeypatch.setattr(rx.vision, "find_image", find_image)
    # No encounter Continue unless a test asks for one. Without this the
    # handler calls the REAL find_color_run, which captures the live screen:
    # slow (a four-second deadline of real captures per test) and genuinely
    # nondeterministic, since whatever is actually on screen can satisfy it.
    monkeypatch.setattr(rx.vision, "find_color_run", lambda *_a, **_k: None)
    monkeypatch.setattr(rx.vision, "click_match", lambda m, h, match, **k: r_clicks.append(match))
    r_clicks = []

    def wait_for_image(h, n, **k):
        # Name-aware for the same reason find_image is: the dialogue asks
        # about several different buttons, and a double that answers "found"
        # to every one of them cannot show which was actually clicked.
        if n == "expedition_speak":
            return {"cx": 600, "cy": 400, "score": 0.98} if prompt else None
        if n in ("dialogue_engage", "dialogue_yes", "click_anywhere_to_close"):
            if not dialogue_buttons:
                return None
            if buttons_clicked is not None:
                buttons_clicked.append(n)
            return {"cx": 500, "cy": 640, "score": 0.97}
        return None

    monkeypatch.setattr(rx.vision, "wait_for_image", wait_for_image)
    monkeypatch.setattr(rx.walk_paths, "load_shipped_encounter_walk_paths",
                        lambda: mapping if mapping is not None else {"Rose Kingdom": "RK route"})
    monkeypatch.setattr(rx.walk_paths, "load_path", lambda n: {"events": list(events)})
    monkeypatch.setattr(rx.walk_paths, "replay_events",
                        lambda e, kb, stop, sprint=False: replayed.append(True))
    # A clock that only moves when something sleeps. Without this, any
    # deadline loop in the handler busy-spins for its full real duration,
    # because sleep is a no-op here.
    now = {"t": 1000.0}
    monkeypatch.setattr(rx.time, "sleep", lambda s: now.__setitem__("t", now["t"] + max(s, 0.05)))
    monkeypatch.setattr(rx.time, "time", lambda: now["t"])
    return replayed


def test_encounter_is_walked_and_talked_to(monkeypatch):
    """The whole point: no template blocks, the run handles it itself."""
    replayed = _wire(monkeypatch)
    r = _Runner(task={"map": "Rose Kingdom"})

    out = r._handle_expedition_encounter(1, threading.Event(), _settled())

    assert replayed == [True], "the map's route must actually be walked"
    assert out["handled_at"] > 0.0, "the cooldown must start once it is handled"
    assert r.clicked_images == ["nav_settings"]
    assert r.settings_closed == 1
    assert r._keyboard.tap.called, "E opens the dialogue"
    clicks = [c.args for c in r._mouse.click.call_args_list]
    assert clicks[0] == (10 + ENCOUNTER_TELEPORT_SPAWN_CLICK[0], 20 + ENCOUNTER_TELEPORT_SPAWN_CLICK[1])
    # Everything after the teleport is the dialogue panel: the buttons are
    # clicked through vision.click_match at wherever they were found, so the
    # only raw coordinate left in the exchange is the panel itself.
    advance = (10 + ENCOUNTER_DIALOGUE_ADVANCE_CLICK[0], 20 + ENCOUNTER_DIALOGUE_ADVANCE_CLICK[1])
    assert clicks[1:] == [advance] * len(clicks[1:])
    assert len(clicks[1:]) >= 3, "the text, the roll and the closing line all need advancing"
    assert out["handled_at"] > 0.0


def test_no_marker_means_no_action(monkeypatch):
    replayed = _wire(monkeypatch, marker=False)
    r = _Runner(task={"map": "Rose Kingdom"})
    r._handle_expedition_encounter(1, threading.Event(), _settled())
    assert replayed == []
    r._mouse.click.assert_not_called()


def test_unmapped_map_is_left_alone(monkeypatch):
    """Adding maps stays additive -- an unmapped one must not walk blindly.

    An unmapped map now gets one look at the encounter's own Continue first,
    so the Continue is explicitly absent here; the invariant under test is
    that with nothing to click and nothing to walk, nothing is clicked.
    """
    from core import runner_expedition as rx
    replayed = _wire(monkeypatch, mapping={"Rose Kingdom": "RK route"})
    monkeypatch.setattr(rx.vision, "find_color_run", lambda *_a, **_k: None)
    r = _Runner(task={"map": "Some New Map"})

    r._handle_expedition_encounter(1, threading.Event(), _settled())

    assert replayed == []
    r._mouse.click.assert_not_called()
    assert any("leaving it alone" in m for m in r.logs)


def test_a_walk_that_lands_wrong_does_not_click_blindly(monkeypatch):
    """The interact prompt is the proof of arrival. Without it, clicking the
    dialogue coordinates would just be clicking at the world."""
    _wire(monkeypatch, prompt=False)
    r = _Runner(task={"map": "Rose Kingdom"})

    r._handle_expedition_encounter(1, threading.Event(), _settled())

    r._keyboard.tap.assert_not_called()
    dialogue = [(10 + x, 20 + y) for x, y in ENCOUNTER_DIALOGUE_CLICKS]
    assert not any(c.args in dialogue for c in r._mouse.click.call_args_list)
    assert any("no interact prompt" in m for m in r.logs)


def test_cooldown_prevents_re_entry_while_the_marker_fades(monkeypatch):
    replayed = _wire(monkeypatch)
    r = _Runner(task={"map": "Rose Kingdom"})
    just_now = time.time()

    state = r._handle_expedition_encounter(1, threading.Event(),
                                            {"handled_at": just_now, "seen_at": 0.0})
    assert state["handled_at"] == just_now
    assert replayed == []
    r._mouse.click.assert_not_called()


def test_missing_reference_image_leaves_the_feature_inert(monkeypatch):
    """No expedition_encounter.png -> behaves exactly as before this existed."""
    from core import runner_expedition as rx

    def raise_missing(h, n, **k):
        raise rx.vision.TemplateNotFound(n)

    _wire(monkeypatch)
    monkeypatch.setattr(rx.vision, "find_image", raise_missing)
    r = _Runner(task={"map": "Rose Kingdom"})

    r._handle_expedition_encounter(1, threading.Event(), _settled())
    r._mouse.click.assert_not_called()


def test_a_settings_menu_that_will_not_close_stops_before_walking(monkeypatch):
    """Walking with Settings open eats the movement keys and covers the world,
    so the route goes nowhere -- and the failure then reads as a bad recording
    rather than a menu that never shut."""
    replayed = _wire(monkeypatch, settings_stuck=True)
    r = _Runner(task={"map": "Rose Kingdom"})

    r._handle_expedition_encounter(1, threading.Event(), _settled())

    assert replayed == [], "must not walk with the menu open"
    r._keyboard.tap.assert_not_called()
    assert any("Settings is still open after" in m for m in r.logs)


# ---------------------------------------------------------------------------
# Lobby overlay: a modal over Play makes every Play click land on the modal
# ---------------------------------------------------------------------------

class _LobbyRunner:
    def __init__(self, overlay=None, missing=False):
        self._overlay, self._missing = overlay, missing
        self._mouse = MagicMock()
        self.logs = []

    def _log(self, m):
        self.logs.append(m)


def _lobby(monkeypatch, overlay=None, missing=False):
    from core import runner as rm
    from core.runner import MacroRunner

    r = _LobbyRunner(overlay, missing)
    r._dismiss_lobby_overlay = MacroRunner._dismiss_lobby_overlay.__get__(r, _LobbyRunner)

    def find_image_any(hwnd, names, **kw):
        if r._missing:
            raise rm.vision.TemplateNotFound(names[0])
        return (r._overlay, "update_log_close") if r._overlay else (None, None)

    monkeypatch.setattr(rm.vision, "find_image_any", find_image_any)
    monkeypatch.setattr(rm.vision, "click_match", lambda m, h, match, **k: r._mouse.click(match))
    monkeypatch.setattr(rm.time, "sleep", lambda s: None)
    return r


def test_lobby_overlay_is_closed_so_play_is_clickable(monkeypatch):
    r = _lobby(monkeypatch, overlay={"cx": 700, "cy": 160, "score": 0.97})
    assert r._dismiss_lobby_overlay(1) is True
    assert r._mouse.click.called
    assert any("covering Play" in m for m in r.logs)


def test_no_lobby_overlay_means_no_click(monkeypatch):
    r = _lobby(monkeypatch, overlay=None)
    assert r._dismiss_lobby_overlay(1) is False
    r._mouse.click.assert_not_called()


def test_lobby_overlay_check_is_optional(monkeypatch):
    """No update_log_close.png -> the check is inert, never an error."""
    r = _lobby(monkeypatch, missing=True)
    assert r._dismiss_lobby_overlay(1) is False
    r._mouse.click.assert_not_called()


def test_queued_upgrade_modals_are_cleared_before_reaching_for_settings(monkeypatch):
    """Level-up modals render over the settings gear and queue up after a wave,
    so one dismissal is not enough -- the next is already on screen."""
    _wire(monkeypatch, blocking_modals=3)
    r = _Runner(task={"map": "Rose Kingdom"})

    r._handle_expedition_encounter(1, threading.Event(), _settled())

    assert any("Cleared 3 upgrade card" in m for m in r.logs)
    assert "nav_settings" in r.clicked_images, "Settings must still be reached afterwards"


def test_a_modal_over_the_teleport_button_does_not_eat_the_click(monkeypatch):
    """The teleport coordinate is BLIND. A card appearing between Settings
    opening and that click swallows it with no sign, so no teleport happens and
    the route replays from wherever the player was -- which is how a run ends
    up nowhere near the objective. The screen must be clear at that moment too,
    not just before Settings was opened."""
    r = _Runner(task={"map": "Rose Kingdom"})
    _wire(monkeypatch, blocking_modals=2, runner=r)

    r._handle_expedition_encounter(1, threading.Event(), _settled())

    cleared = [m for m in r.logs if "Cleared" in m]
    assert any("Teleport To Spawn" in m for m in cleared), \
        f"the teleport click must be guarded too, got: {cleared}"


def test_settings_that_never_opened_does_not_click_blind(monkeypatch):
    """If something took the Settings click, the teleport coordinate would land
    on whatever is actually there."""
    from core import runner_expedition as rx
    _wire(monkeypatch)
    monkeypatch.setattr(rx.vision, "find_image",
                        lambda h, n, **k: {"cx": 430, "cy": 80, "score": 0.96}
                        if n == "expedition_encounter" else None)
    r = _Runner(task={"map": "Rose Kingdom"})

    r._handle_expedition_encounter(1, threading.Event(), _settled())

    r._mouse.click.assert_not_called()
    assert any("didn't actually open" in m for m in r.logs)


# ---------------------------------------------------------------------------
# Teleport wait: sitting on the lobby is not "still loading"
# ---------------------------------------------------------------------------

class _TeleportRunner:
    def __init__(self):
        self.logs = []

    def _log(self, m):
        self.logs.append(m)

    def _set_status(self, **kw):
        pass


def _teleport(monkeypatch, screen):
    """screen: what each poll sees -- 'lobby', 'loading', or 'in_game'."""
    from core import runner as rm
    from core.runner import MacroRunner

    r = _TeleportRunner()
    r._wait_for_teleport_result = MacroRunner._wait_for_teleport_result.__get__(r, _TeleportRunner)
    state = {"poll": -1}

    def find_image(hwnd, name, **kw):
        # One poll asks about nav_unitmanager and (every Nth) nav_play, so the
        # script advances on the unit-manager check, not on every call.
        if name == "nav_unitmanager":
            state["poll"] += 1
        i = min(state["poll"], len(screen) - 1)
        now = screen[i] if i >= 0 else "loading"
        if name == "nav_unitmanager":
            return {"score": 1.0} if now == "in_game" else None
        if name == "nav_play":
            return {"score": 0.95} if now == "lobby" else None
        return None

    monkeypatch.setattr(rm.vision, "find_image", find_image)
    monkeypatch.setattr(rm.time, "sleep", lambda s: None)
    return r


def test_sitting_on_the_lobby_stops_waiting_for_a_teleport(monkeypatch):
    """Matchmaking left, cancelled, or never took. No teleport is coming, and
    the matchmaking timeout is five minutes -- waiting it out achieves
    nothing."""
    import threading

    r = _teleport(monkeypatch, ["lobby"] * 40)
    assert r._wait_for_teleport_result(1, threading.Event(), 60.0) == "lobby"


def test_the_lobby_still_drawn_as_a_teleport_begins_is_not_a_failure(monkeypatch):
    """The lobby lingers for a frame while the teleport starts, so one sighting
    must not abandon a teleport that is actually happening."""
    import threading

    r = _teleport(monkeypatch, ["lobby"] * 6 + ["loading"] * 6 + ["in_game"])
    assert r._wait_for_teleport_result(1, threading.Event(), 60.0) == "ok"


def test_a_normal_slow_teleport_is_still_given_its_full_time(monkeypatch):
    import threading

    r = _teleport(monkeypatch, ["loading"] * 8 + ["in_game"])
    assert r._wait_for_teleport_result(1, threading.Event(), 60.0) == "ok"


def test_settings_that_closes_on_the_second_attempt_still_proceeds(monkeypatch):
    """One close click was not reliably enough -- observed closing on the
    second attempt about as often as the first."""
    replayed = _wire(monkeypatch, closes_after=2)
    r = _Runner(task={"map": "Rose Kingdom"})

    r._handle_expedition_encounter(1, threading.Event(), _settled())

    assert r.settings_closed >= 2, "the close must be retried, not given up on"
    assert replayed == [True], "and the route still walks once it is shut"


def test_dialogue_is_clicked_through_until_the_prompt_clears(monkeypatch):
    """The exchange is several boxes. One pass of the click sequence left the
    run standing in an open dialogue."""
    _wire(monkeypatch, speak_rounds=3)
    r = _Runner(task={"map": "Rose Kingdom"})

    r._handle_expedition_encounter(1, threading.Event(), _settled())

    assert any("clicking through again" in m for m in r.logs)
    assert any("Encounter handled" in m for m in r.logs)
    assert len(r._mouse.click.call_args_list) > 1 + len(ENCOUNTER_DIALOGUE_CLICKS)


def test_a_dialogue_that_never_clears_stops_instead_of_clicking_on(monkeypatch):
    _wire(monkeypatch, speak_rounds=99)
    r = _Runner(task={"map": "Rose Kingdom"})

    r._handle_expedition_encounter(1, threading.Event(), _settled())

    assert any("still open after clicking through" in m for m in r.logs)
    assert not any("Encounter handled" in m for m in r.logs)




# ---------------------------------------------------------------------------
# The dialogue exchange
# ---------------------------------------------------------------------------

def test_the_exchange_runs_engage_then_the_roll_then_yes(monkeypatch):
    """The observed shape: Engage, advance, a d20 roll, click the result
    away, Yes, one closing line. The buttons are found by image because the
    menu is four options wide and they recolour between encounters -- a
    fixed position is a one-in-four guess that fails silently."""
    pressed = []
    _wire(monkeypatch, buttons_clicked=pressed)
    r = _Runner(task={"map": "Rose Kingdom"})

    r._handle_expedition_encounter(1, threading.Event(), _settled())

    assert pressed == ["dialogue_engage", "click_anywhere_to_close", "dialogue_yes"]


def test_the_roll_is_waited_out_rather_than_clicked_through(monkeypatch):
    """The d20 spins for a few seconds and clicks during it do nothing, so
    the settle has to actually be requested."""
    from core.runner_constants import ENCOUNTER_DICE_ROLL_SETTLE
    _wire(monkeypatch)
    r = _Runner(task={"map": "Rose Kingdom"})

    r._handle_expedition_encounter(1, threading.Event(), _settled())

    assert ENCOUNTER_DICE_ROLL_SETTLE in r.slept


def test_without_the_crops_it_falls_back_to_the_old_fixed_clicks(monkeypatch):
    """Nobody who has no dialogue_* images installed should be worse off
    than before -- they get exactly the sequence they had."""
    _wire(monkeypatch, dialogue_buttons=False)
    r = _Runner(task={"map": "Rose Kingdom"})

    r._handle_expedition_encounter(1, threading.Event(), _settled())

    clicks = [c.args for c in r._mouse.click.call_args_list]
    assert clicks[1:] == [(10 + x, 20 + y) for x, y in ENCOUNTER_DIALOGUE_CLICKS]
    assert any("falling back" in m for m in r.logs)


# ---------------------------------------------------------------------------
# The encounter's own Continue button
# ---------------------------------------------------------------------------

def _with_continue(monkeypatch, present):
    """Make the colour engine report an encounter Continue, or not."""
    from core import runner_expedition as rx
    monkeypatch.setattr(rx.vision, "find_color_run",
                        lambda h, band, mask, run: {"cx": 575, "cy": 588} if present else None)


def test_a_continue_ends_the_encounter_without_walking(monkeypatch):
    """Strictly better than the walk when it is there: milliseconds, works on
    any map rather than the four with a bundled route, and cannot strand the
    character somewhere a recording did not expect."""
    replayed = _wire(monkeypatch)
    _with_continue(monkeypatch, True)
    r = _Runner(task={"map": "Rose Kingdom"})

    out = r._handle_expedition_encounter(1, threading.Event(), _settled())

    assert replayed == [], "walked even though Continue was available"
    assert r.clicked_images == [], "opened Settings even though Continue was available"
    assert out["handled_at"] > 0.0, "the encounter must still be marked handled"
    assert any("no walk needed" in m for m in r.logs)


def test_without_a_continue_it_still_walks_the_route(monkeypatch):
    """The walk stays as the fallback -- the Continue is a recent addition
    and the older flow is the one known to work."""
    replayed = _wire(monkeypatch)
    _with_continue(monkeypatch, False)
    r = _Runner(task={"map": "Rose Kingdom"})

    r._handle_expedition_encounter(1, threading.Event(), _settled())

    assert replayed == [True]
    assert r.clicked_images == ["nav_settings"]


# ---------------------------------------------------------------------------
# Maps with no bundled route
# ---------------------------------------------------------------------------

def test_an_unmapped_map_is_handled_by_its_continue(monkeypatch):
    """Only four maps have a bundled route; everywhere else the encounter
    used to be logged and abandoned. The Continue needs nothing recorded, so
    it works where the walk cannot."""
    replayed = _wire(monkeypatch, mapping={})          # nothing mapped at all
    _with_continue(monkeypatch, True)
    r = _Runner(task={"map": "Somewhere Unmapped"})

    out = r._handle_expedition_encounter(1, threading.Event(), _settled())

    assert replayed == [], "there is no route to walk"
    assert out["handled_at"] > 0.0, "the encounter must be marked handled"
    assert any("no walk needed" in m for m in r.logs)
    assert not any("leaving it alone" in m for m in r.logs)


def test_an_unmapped_map_with_no_continue_is_still_left_alone(monkeypatch):
    """Nothing to walk and nothing to click means there is genuinely nothing
    to do -- and it must say so rather than clicking at the world."""
    replayed = _wire(monkeypatch, mapping={})
    _with_continue(monkeypatch, False)
    r = _Runner(task={"map": "Somewhere Unmapped"})

    out = r._handle_expedition_encounter(1, threading.Event(), _settled())

    assert replayed == []
    assert out["handled_at"] > 0.0, "still marked handled so it is not retried forever"
    assert any("leaving it alone" in m for m in r.logs)
