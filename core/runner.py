"""The macro's actual run loop -- Dashboard > Start. Currently covers the
launch sequence (confirm the task queue isn't empty, confirm we're on the
lobby screen, open Play > Story), picking the first task's map and stage,
Pre Start (camera setup, Team Loadout, a per-map default walk, the task's
Macro Operation template's Pre Start blocks), pressing Start Game, and then
watching the battle for Battle-phase blocks (Upgrade/Sell/Auto Upgrade Unit)
and a Victory/Defeat screen -- which is recorded to run history/win-loss
counts and, if a webhook is configured, reported to Discord as a screenshot
of the result screen plus a rendered win/loss card (see
_handle_match_result/_send_result_webhook -- the stats/reward OCR this used
to do per match now lives only behind Settings > Debug's on-demand "Read
Rewards"/"Read Game Stats" buttons). Equipment include/exclude, and the
rest of the Battle-phase block types (Walk/Wait/Setting), plug in once those
exist.
"""
import os
import threading
import time
from datetime import datetime, timezone

from . import camera
from . import keys
from . import stage_select
from . import vision
from . import window as wm
from .runner_constants import *  # noqa: F401,F403 -- see runner_constants' docstring
from .runner_blocks import BlockOps
from .runner_challenge import ChallengeOps
from .runner_expedition import ExpeditionOps
class MacroRunner(ChallengeOps, ExpeditionOps, BlockOps):
    """One run's worth of state -- module-level singleton via main.Api, same
    pattern as core.paths._recorder, since only one run can realistically be
    active at a time (one physical game window, one macro)."""

    def __init__(self, mouse, keyboard, log, set_status=None, record_result=None,
                 get_challenge_settings=None, mark_challenge_stage_played=None, get_run_stats=None):
        self._mouse = mouse
        self._keyboard = keyboard
        self._log = log
        # Live click-point overrides -- replaced with the user's saved values
        # at the top of _run; defaults here so the Settings > Debug test
        # paths (which never go through _run) still resolve every key.
        self._coords = dict(DEFAULT_COORDS)
        # Set at the top of _run when nav_unitmanager is already visible
        # (Start pressed from inside a stage); consumed one-shot by
        # _run_task to skip the first task's lobby/stage entry.
        self._skip_first_task_setup = False
        # Set by _handle_match_result when an event farm task's Victory dropped
        # a Crow Relic and the task opted into auto-clearing Act 4; read (and
        # cleared) by _run_task, which runs the divert. See _run_act4_diversion.
        self._act4_wants_in = False
        # Expedition checkpoint engine choice (see the EXP_COLOR_* block) +
        # the sighting debounce clock it uses; the real values arrive via
        # start()/_run, these are just never-ran-yet defaults. Same for the
        # Expedition camera's O-zoom hold (Settings > Debug).
        self._expedition_color_buttons = True
        self._exp_last_sighting_at = 0.0
        self._expedition_camera_o_ms = 100.0
        # Wrapped to remember the most recent action text locally: the
        # stop path (_checkpoint) reports "Stopped. (was: <action>)" so a
        # user stopping a visibly-hung run gets told what it was stuck on
        # -- the runner never sees main.Api's own status dict, so it keeps
        # its own copy of just the action string.
        _raw_set_status = set_status or (lambda **kw: None)
        self._last_action = ""

        def _tracking_set_status(**kw):
            if "action" in kw:
                self._last_action = kw["action"]
            _raw_set_status(**kw)

        self._set_status = _tracking_set_status
        # (result: "win"|"loss", map_name, duration_str) -> persists to run
        # history / win-loss counters (see main.Api._record_match_result).
        self._record_result = record_result or (lambda *a, **kw: None)
        # Challenge tab settings live in settings.json, owned by main.Api
        # (same reason record_result is a callback, not a direct import --
        # avoids core/ reaching back into main.py). None (the default, e.g.
        # in tests/CLI mode) just makes _run_challenges a no-op.
        self._get_challenge_settings = get_challenge_settings
        self._mark_challenge_stage_played = mark_challenge_stage_played or (lambda *a, **kw: None)
        # Returns a fresh session/all-time win-loss + session_start + version
        # snapshot for the match-result webhook (see _send_result_webhook).
        # None (tests/CLI) just yields an empty snapshot, so those fields are
        # quietly omitted rather than the webhook failing.
        self._get_run_stats = get_run_stats or (lambda: {})
        self._thread = None
        self._stop_event = None
        self._pause_event = threading.Event()
        self._paused_logged = False
        self._stop_logged = False  # one "Stopped." per stop -- see _checkpoint
        self._debug_screenshots = False
        self._current_hwnd = None       # set at the top of _run -- lets _checkpoint reach Leave Stage on stop
        self._hwnd_getter = None        # set at the top of _run -- lets _attempt_rejoin find a re-docked hwnd
        self._left_stage_this_run = False
        # Placed-unit screen positions from THIS match's Pre Start (see
        # _run_place_unit_block), keyed by the unit's #ordinal among place_unit
        # blocks (same numbering ui/app.js's listPlacedUnits() uses for the
        # Upgrade/Sell Unit pickers) -- lets Battle-phase Upgrade/Sell Unit
        # blocks click the right spot without needing their own recorded
        # position. Only overwritten when a placement actually runs, so a
        # block skipped via "Once" on a repeat keeps whatever position its
        # first placement recorded rather than losing it.
        self._placed_unit_positions = {}
        # Whether Left Shift is currently being held down for a "quick
        # place" chain (see _run_place_unit_block) -- true from right
        # before the FIRST of a run of consecutive same-unit Place Unit
        # blocks is clicked, through the last one, then released. Reset
        # False any time a run/test starts fresh so a leftover held key
        # from an interrupted previous run can never bleed into a new one.
        self._quick_place_shift_down = False
        # The running #ordinal counter place_unit blocks share -- Pre Start
        # blocks number first, Battle-phase place_unit blocks (see
        # _run_battle_blocks_tick) continue counting from wherever Pre
        # Start left off, matching ui/app.js's listPlacedUnits() (which
        # numbers place_unit blocks across BOTH phases in one sequence).
        # Reset to 0 once per match in _run_prestart.
        self._last_unit_ordinal = 0
        # Battle-phase block state (see _run_battle_blocks_tick) -- reset at
        # the start of each match in _play_one_match.
        self._battle_block_index = 0
        self._battle_block_state = {}
        # How many times exp_extract has shown up THIS match, and which
        # sighting actually accepts it (see _check_expedition_wave_result)
        # -- both reset alongside the battle block state in _play_one_match.
        self._expedition_extract_count = 0
        self._expedition_extract_accept_at = 1
        # Consecutive-loss fail-safe (see MAX_CONSECUTIVE_LOSSES_SAME_MAP):
        # how many losses in a row on _consecutive_loss_map so far -- reset
        # to 0 on any win, or restarted at 1 for a new map, so only a real
        # unbroken streak on the SAME map ever counts toward it.
        self._consecutive_losses = 0
        self._consecutive_loss_map = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def is_paused(self) -> bool:
        return self._pause_event.is_set()

    def start(self, hwnd_getter, get_tasks, scroll_power: int = None, coords: dict = None,
              scroll_nudges: int = None, debug_screenshots: bool = False, default_walk_paths: dict = None,
              webhook: dict = None, expedition_color_buttons: bool = True,
              expedition_camera_o_ms: float = 100) -> dict:
        if self.is_running():
            return {"ok": False, "reason": "already_running"}
        self._stop_event = threading.Event()
        self._pause_event.clear()
        self._paused_logged = False
        self._stop_logged = False
        self._debug_screenshots = bool(debug_screenshots)
        self._expedition_color_buttons = bool(expedition_color_buttons)
        try:
            self._expedition_camera_o_ms = max(0.0, float(expedition_camera_o_ms))
        except (TypeError, ValueError):
            self._expedition_camera_o_ms = 100.0
        self._current_hwnd = None
        self._left_stage_this_run = False
        self._consecutive_losses = 0
        self._consecutive_loss_map = None
        self._thread = threading.Thread(
            target=self._run_session,
            args=(hwnd_getter, get_tasks, self._stop_event, scroll_power, coords, scroll_nudges,
                  default_walk_paths, webhook),
            daemon=True)
        self._thread.start()
        return {"ok": True}

    def start_debug_test(self, hwnd_getter, mode: str, macro_name: str, debug_screenshots: bool = False,
                           coords: dict = None) -> dict:
        """Settings > Debug > "Test Pre Start"/"Test Battle" -- runs a
        chosen Macro Operation's Pre Start or Battle blocks against Roblox
        as it is right now, WITHOUT going through the whole lobby/gamemode/
        map/stage/teleport setup a real task needs first. Goes through the
        exact same self._thread/self._stop_event start() itself uses (just
        targeting _run_debug_test instead of _run) specifically so this
        shows up as "running" the same way a real run does and F2/Stop
        actually interrupts it, instead of being a one-shot blocking call
        nothing can cancel (see debug_check_expedition_wave/
        debug_force_rejoin, which are that simpler kind on purpose -- this
        one's meant to run for a while, e.g. Battle blocks ticking
        indefinitely, so it needs the real thing)."""
        if self.is_running():
            return {"ok": False, "reason": "already_running"}
        if mode not in ("prestart", "battle"):
            return {"ok": False, "reason": "bad_mode"}
        if not macro_name:
            return {"ok": False, "reason": "no_macro"}
        self._stop_event = threading.Event()
        self._pause_event.clear()
        self._paused_logged = False
        self._stop_logged = False
        self._debug_screenshots = bool(debug_screenshots)
        # Same saved click-point overrides a real run gets (see _run) --
        # without this, a debug test of blocks that click through
        # unit-info resets etc. would use the defaults the user may have
        # specifically re-picked away from.
        self._coords = {**DEFAULT_COORDS, **(coords or {})}
        self._current_hwnd = None
        self._left_stage_this_run = True  # nothing to Leave Stage from -- there's no real match here
        self._thread = threading.Thread(
            target=self._run_debug_test,
            args=(hwnd_getter, mode, macro_name, self._stop_event),
            daemon=True)
        self._thread.start()
        return {"ok": True}

    def _run_debug_test(self, hwnd_getter, mode: str, macro_name: str, stop_event: threading.Event) -> None:
        hwnd = hwnd_getter() if hwnd_getter else None
        if not hwnd or not wm.is_window(hwnd):
            self._log("[Debug] No Roblox window found -- can't test.")
            self._set_status(action="Idle")
            return
        self._current_hwnd = hwnd
        # A click/keypress has to actually reach the game, not whatever else
        # happened to have focus (this is fired from a Settings button click,
        # not guaranteed to be Roblox already) -- same focus-fix _run() does
        # before a real Start.
        wm.show_window(hwnd)
        if not wm.activate_window(hwnd):
            self._log("[Debug] Couldn't confirm Roblox actually took focus -- clicks may not register "
                       "until it does. Continuing anyway.")
        # Enough of a task shape for _run_prestart_blocks/_load_battle_blocks
        # to work with -- both only ever read task["macro"] and (for
        # _strip_auto_upgrade_for_expedition) task["mode"]/task["map"], not
        # anything about a real match in progress.
        task = {"macro": macro_name, "mode": "story", "map": "-", "difficulty": "-"}
        try:
            if mode == "prestart":
                self._log(f'[Debug] Testing Pre Start blocks from "{macro_name}"...')
                self._run_prestart_blocks(hwnd, stop_event, task, first_repeat=True)
            else:
                self._log(f'[Debug] Testing Battle blocks from "{macro_name}"...')
                battle_blocks = self._load_battle_blocks(task)
                if not battle_blocks:
                    self._log(f'[Debug] Template "{macro_name}" has no Battle blocks.')
                else:
                    self._battle_block_index = 0
                    self._battle_block_state = {}
                    self._last_unit_ordinal = 0
                    self._quick_place_shift_down = False
                    # Ticks continuously (same as a real match's own poll
                    # loop) instead of running through the block list once
                    # and stopping -- Battle blocks are built to keep
                    # running for the length of a match (Upgrade Unit
                    # retrying over time, etc.), so this needs to keep
                    # ticking until Stop is pressed to actually exercise
                    # that, not just fire once.
                    while not self._checkpoint(stop_event):
                        self._run_battle_blocks_tick(hwnd, stop_event, battle_blocks, first_repeat=True,
                                                       macro_name=macro_name)
                        time.sleep(MATCH_RESULT_POLL_INTERVAL)
        finally:
            # Release this thread's screen-capture handle (see core.mss_manager)
            # when the debug test ends -- same cleanup a real run does below.
            vision.close_mss()
            if not (stop_event is not None and stop_event.is_set()):
                self._log("[Debug] Test finished.")
            self._set_status(action="Idle")

    def stop(self) -> dict:
        # Setting the event is enough on its own -- _checkpoint (called
        # between every step in _run) picks it up on the very next check,
        # and it also breaks any in-progress wait_for_image poll immediately
        # rather than letting that poll run its full timeout out. Clearing
        # pause here too so Stop always actually stops instead of leaving a
        # paused thread parked forever waiting on a resume that isn't coming.
        if self._stop_event is not None:
            self._stop_event.set()
        self._pause_event.clear()
        return {"ok": True}

    def pause(self) -> dict:
        if self.is_running():
            self._pause_event.set()
        return {"ok": True}

    def resume(self) -> dict:
        self._pause_event.clear()
        return {"ok": True}

    def _checkpoint(self, stop_event: threading.Event) -> bool:
        """Call between every major step. Blocks here while paused (so Pause
        works everywhere a stop check already happens, for free), then
        reports whether the caller should bail out. Centralizing the
        stop-check here (instead of the same 4-line block repeated after
        every step) is what makes it trivial to keep it consistent."""
        while self._pause_event.is_set() and not stop_event.is_set():
            if not self._paused_logged:
                self._log("[Macro] Paused.")
                self._set_status(action="Paused")
                self._paused_logged = True
            time.sleep(0.15)
        if self._paused_logged and not self._pause_event.is_set():
            self._log("[Macro] Resumed.")
            self._paused_logged = False
        if stop_event.is_set():
            self._try_leave_stage()
            # Say WHAT was in flight when the stop landed, not just
            # "Stopped." -- someone stopping a run that's visibly hung
            # (e.g. sitting on "Waiting for gamemode menu...") is exactly
            # the person who needs to know which step/image it was stuck
            # on, and the timeout message that would have named it never
            # gets to fire once they stop first. The action text is
            # whatever _set_status last showed on the Dashboard. Logged
            # once per stop (_stop_logged): every layer of the run calls
            # _checkpoint on the way out, which used to print a bare
            # "Stopped." per layer.
            if not self._stop_logged:
                self._stop_logged = True
                was = self._last_action
                if was and was not in ("Idle", "Paused"):
                    self._log(f"[Macro] Stopped. (was: {was})")
                else:
                    self._log("[Macro] Stopped.")
            self._set_status(action="Idle")
            return True
        return False

    def _interruptible_sleep(self, seconds: float, stop_event: threading.Event) -> None:
        """time.sleep(), but bails out immediately once stop_event fires
        instead of blocking it for the full duration -- F2/Stop is supposed
        to stay instant (see _checkpoint/_try_leave_stage's own comment on
        this), which a plain time.sleep(5.0) settle delay quietly breaks
        for however long is left on it. Used for the multi-second Expedition
        settle delays (EXTRACT_CONFIRM_SETTLE, EXPEDITION_CONTINUE_COOLDOWN)
        -- short delays elsewhere (SETTLE_DELAY and smaller) aren't worth
        the same treatment, they're not long enough to actually notice."""
        deadline = time.time() + seconds
        while True:
            remaining = deadline - time.time()
            if remaining <= 0 or (stop_event is not None and stop_event.is_set()):
                return
            time.sleep(min(0.15, remaining))

    def _try_leave_stage(self) -> None:
        # F2/Stop must stay instant (see main.py's hotkey wiring), so this is
        # a single one-shot check, not a wait -- no match just means either
        # Leave Stage isn't on screen right now (not mid-match) or the image
        # hasn't been added, either way nothing to click. Guarded so a stop
        # mid-run only ever attempts this once, not on every _checkpoint call
        # after the stop_event is already set.
        if self._left_stage_this_run or self._current_hwnd is None:
            return
        self._left_stage_this_run = True
        try:
            match = vision.find_image(self._current_hwnd, "leave_stage")
        except vision.TemplateNotFound:
            return
        if match is not None:
            self._log(f"[Macro] Stopping -- clicking Leave Stage (score {match['score']:.2f}) to quit to menu.")
            vision.click_match(self._mouse, self._current_hwnd, match)
            time.sleep(0.5)
            self._click_return_to_lobby_if_found(self._current_hwnd)

    def _click_return_to_lobby_if_found(self, hwnd, stop_event: threading.Event = None) -> bool:
        # Leave Stage can bring up its own "Return to Lobby" confirmation
        # (return.png) rather than backing out on its own -- optional/
        # best-effort like nav_disband and friends, so a real miss (it never
        # shows) is cheap. But a single instant find_image right after the
        # Leave Stage click was firing before the confirmation had even
        # animated in, so a real popup was being missed too -- reported as
        # "clicks Leave Stage, then just sits there" even though Return to
        # Lobby was genuinely up on screen a moment later. Short poll
        # instead of one-shot fixes that without meaningfully slowing down
        # the common case where it never appears.
        try:
            match = vision.wait_for_image(
                hwnd, "return", timeout=RETURN_TO_LOBBY_CHECK_TIMEOUT, stop_event=stop_event)
        except vision.TemplateNotFound:
            return False
        if match is None:
            return False

        for attempt in range(1, RETURN_TO_LOBBY_CLICK_RETRY_ATTEMPTS + 1):
            debug_path = self._debug_save(hwnd, "return", match)
            suffix = f" Debug: {debug_path}" if debug_path else ""
            retry = (f" (attempt {attempt}/{RETURN_TO_LOBBY_CLICK_RETRY_ATTEMPTS})"
                     if attempt > 1 else "")
            self._log(f"[Macro] Found \"Return to Lobby\" (score {match['score']:.2f})"
                      f"{retry} -- clicking it.{suffix}")
            if not wm.activate_window(hwnd):
                self._log("[Macro] Couldn't confirm focus before clicking \"Return to Lobby\" "
                          "-- click may not register.")
            vision.click_match(self._mouse, hwnd, match)
            self._interruptible_sleep(RETURN_TO_LOBBY_VERIFY_SETTLE, stop_event)
            if stop_event is not None and stop_event.is_set():
                return True

            try:
                still_there = vision.find_image(hwnd, "return")
            except vision.TemplateNotFound:
                still_there = None
            if still_there is None:
                return True
            match = still_there

        self._log(f'[Macro] "Return to Lobby" still showing after '
                  f'{RETURN_TO_LOBBY_CLICK_RETRY_ATTEMPTS} clicks -- continuing anyway.')
        return True

    def _click_close_popup_if_found(self, hwnd) -> None:
        # Spirit City Act 3 (Raid) can throw up a "Click anywhere to close"
        # popup (a boss/cutscene intro) mid-battle -- one-shot/best-effort
        # like nav_disband, checked every poll tick while watching for the
        # match result (see watch_close_popup in _wait_for_match_result).
        # Its visual variants all live in Assets/ui/click_anywhere_to_close/
        # and are tried automatically per search (see
        # vision.template_variant_paths).
        try:
            match = vision.find_image(hwnd, "click_anywhere_to_close")
        except vision.TemplateNotFound:
            return
        if match is None:
            return
        debug_path = self._debug_save(hwnd, "click_anywhere_to_close", match)
        suffix = f" Debug: {debug_path}" if debug_path else ""
        self._log(f"[Macro] Found \"Click anywhere to close\" (score {match['score']:.2f}) -- clicking it.{suffix}")
        vision.click_match(self._mouse, hwnd, match)

    def _dismiss_reward_card_if_found(self, hwnd) -> bool:
        """A level-up "Select an upgrade!" reward-card modal can show up at
        several different moments in Expedition -- mid-battle right on top
        of the exp_extract choice, or (confirmed from a real stuck report)
        immediately after Victory, before the Repeat/Leave Stage result
        panel has even rendered, blocking repeat_stage from ever matching
        for the ENTIRE search window. Middle-screen click picks whatever
        card is there. Returns whether one was actually found (so callers
        can loop until it's actually gone, not just fire once)."""
        try:
            match = vision.find_image(hwnd, "select upgrade card")
        except vision.TemplateNotFound:
            return False
        if match is None:
            return False
        debug_path = self._debug_save(hwnd, "select upgrade card", match)
        suffix = f" Debug: {debug_path}" if debug_path else ""
        self._log(f'[Macro] Found "select upgrade card" (score {match["score"]:.2f}) -- clicking it.{suffix}')
        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        self._mouse.click(left + self._coords["screen_middle_x"], top + self._coords["screen_middle_y"])
        return True


    def _debug_save(self, hwnd, name: str, match: dict) -> str:
        """Gated behind Settings > Debug > "Debug Match Screenshots" (off by
        default): a full-window screenshot on every single match found while
        running -- Play, Story, every stage row, ... -- adds up fast, so this
        only actually writes when the toggle is on. Returns None otherwise,
        which callers fold into their log line's " Debug: ..." suffix."""
        return vision.save_match_debug(hwnd, name, match) if self._debug_screenshots else None

    def _run_session(self, *args) -> None:
        """Thread entry for a real run. Wraps _run only to guarantee this
        thread's screen-capture handle (see core.mss_manager) is released when
        the run ends -- however it ends -- without threading a try/finally
        through _run's whole early-returning body."""
        try:
            self._run(*args)
        finally:
            vision.close_mss()

    def _run(self, hwnd_getter, get_tasks, stop_event: threading.Event, scroll_power: int = None,
              coords: dict = None, scroll_nudges: int = None, default_walk_paths: dict = None,
              webhook: dict = None) -> None:
        coords = {**DEFAULT_COORDS, **(coords or {})}
        # Also kept on self: most click sites live in methods coords was
        # never threaded through -- one shared dict beats adding a parameter
        # to a dozen call chains (see _cxy).
        self._coords = coords
        default_walk_paths = default_walk_paths or {}
        webhook = webhook or {}

        hwnd = hwnd_getter()
        if not hwnd or not wm.is_window(hwnd):
            self._log("[Macro] Roblox isn't docked yet -- can't start.")
            self._set_status(action="Idle")
            return
        self._current_hwnd = hwnd
        # Kept for _attempt_rejoin -- after a rejoin relaunches Roblox, the
        # dock watchdog (main.py) re-docks it under a NEW hwnd on its own;
        # this is how the runner finds out what that new hwnd actually is.
        self._hwnd_getter = hwnd_getter

        # A click has to actually reach the game, not this panel -- same
        # focus-fix every other live-input action in this app uses.
        wm.show_window(hwnd)
        if not wm.activate_window(hwnd):
            self._log("[Macro] Couldn't confirm Roblox actually took focus -- clicks may not register "
                       "until it does. Continuing anyway.")

        # SendInput cannot reach a window owned by a HIGHER-privilege
        # process than this one -- Windows drops it with no error at all,
        # which looks exactly like "finds the button, clicks it, nothing
        # happens, cursor doesn't even move" (a persistent user report even
        # after several unrelated click-reliability fixes). Checked once
        # here rather than every click since elevation doesn't change
        # mid-run, and this is advisory only -- it can't fix a mismatch,
        # only explain one if it exists.
        if wm.is_process_elevated(hwnd) and not wm.is_self_elevated():
            self._log("[Macro] Roblox appears to be running as Administrator, but this macro isn't -- "
                       "Windows silently blocks simulated clicks/keys from a non-elevated app to an "
                       "elevated one. If clicks aren't registering, close both and relaunch this macro "
                       "as Administrator too (right-click > Run as administrator).")

        # Already standing in a match? nav_unitmanager only renders once
        # you're actually in-game (it's the same image _wait_teleport_in
        # treats as the "we teleported in" confirmation), so seeing it now
        # means the user pressed Start from INSIDE a stage -- all the lobby
        # navigation (Play, gamemode, map, stage, matchmaking, teleport)
        # would be clicking at screens that aren't there. The first task
        # skips straight to Pre Start instead (see _run_task's
        # _skip_first_task_setup), picking the run up from where the user
        # already is.
        self._skip_first_task_setup = False
        try:
            if vision.find_image(hwnd, "nav_unitmanager") is not None:
                self._skip_first_task_setup = True
                self._log("[Macro] Unit Manager is visible -- already in-game, so the first task "
                           "skips stage entry and starts from Pre Start.")
        except vision.TemplateNotFound:
            pass

        # Challenge runs ONCE per Start (not once per task-queue pass, see
        # the while loop below) -- if it's enabled, every ready stage slot
        # gets attempted before the Task Queue ever starts. Skipped when
        # starting already in-game: its navigation begins at the lobby,
        # which is exactly where we aren't -- ready slots still get their
        # chance at the between-repeats check once the current stage ends.
        if self._checkpoint(stop_event):
            return
        if not self._skip_first_task_setup:
            self._run_challenges(hwnd, stop_event, coords, default_walk_paths, webhook)
        if self._checkpoint(stop_event):
            return

        loop_pass = 1
        while True:
            # Re-read the queue every pass instead of once up front -- a run
            # loops indefinitely (see below), potentially for hours, and the
            # user may edit the Task screen (add/remove/reorder) between
            # passes expecting the NEXT pass to pick up their changes rather
            # than keep replaying a stale snapshot from when the run started.
            tasks = get_tasks()
            if not tasks:
                self._log("[Macro] Task queue is empty -- add a task on the Task screen first.")
                self._set_status(action="Idle")
                return

            if loop_pass == 1:
                self._log(f"[Macro] Starting run -- {len(tasks)} task(s) queued.")
            else:
                self._log(f"[Macro] Task queue finished -- restarting from task 1 (pass {loop_pass}).")

            for task_index, task in enumerate(tasks, start=1):
                if self._checkpoint(stop_event):
                    return

                map_name = task.get("map")
                # Event mode has no map to pick (just an Act) -- it's the one
                # mode where a missing map is expected, not a misconfigured
                # task, so don't skip it over that.
                if not map_name and (task.get("mode") or "story") != "event":
                    self._log(f"[Macro] Task {task_index}/{len(tasks)} has no map set -- skipping it.")
                    continue

                # A disconnect/rejoin during a previous task (see
                # _attempt_rejoin) may have re-docked Roblox under a new
                # hwnd -- pick that up before starting the next task.
                if self._current_hwnd and wm.is_window(self._current_hwnd):
                    hwnd = self._current_hwnd

                # A mid-task failure (a stuck battle, a missed click, ...)
                # doesn't kill the whole overnight run -- _run_task recovers to
                # the lobby and retries internally, only returning False when
                # stop_event actually fired.
                if not self._run_task(hwnd, stop_event, task, task_index, len(tasks), coords, scroll_power,
                                        scroll_nudges, default_walk_paths, webhook):
                    self._set_status(action="Idle")
                    return

            # The queue always loops back to task 1 once it finishes rather
            # than going Idle -- Stop (F2) is the only way to actually end
            # an unattended run now.
            if self._checkpoint(stop_event):
                return
            loop_pass += 1





    def _run_task(self, hwnd, stop_event: threading.Event, task: dict, task_index: int, task_count: int,
                   coords: dict, scroll_power: int, scroll_nudges: int, default_walk_paths: dict,
                   webhook: dict) -> bool:
        """Runs one task's full repeat count end to end. A mid-task failure
        backs out to the lobby and retries this task's setup from scratch
        (see _recover_to_lobby), up to TASK_RECOVERY_ATTEMPTS times, before
        giving up on just this task and letting the run move on to the next
        one -- so one stuck battle doesn't end an unattended overnight run.
        Returns False only when stop_event actually fired (the whole run
        should stop); True in every other case, including "gave up on this
        task after repeated failures".
        """
        map_name = task.get("map")
        mode = task.get("mode") or "story"
        repeat_total = max(1, int(task.get("repeat") or 1))

        for recovery_attempt in range(1, TASK_RECOVERY_ATTEMPTS + 1):
            if self._checkpoint(stop_event):
                return False
            # A disconnect handled during the previous attempt (see
            # _attempt_rejoin) may have re-docked Roblox under a NEW hwnd --
            # self._current_hwnd is what tracks that, so every retry picks
            # up wherever the game actually ended up rather than continuing
            # to act on a hwnd that might already be dead.
            if self._current_hwnd and wm.is_window(self._current_hwnd):
                hwnd = self._current_hwnd
            if recovery_attempt > 1:
                self._log(f'[Macro] Retrying task {task_index}/{task_count} from the lobby '
                           f'(attempt {recovery_attempt}/{TASK_RECOVERY_ATTEMPTS})...')
            self._log(f'[Macro] Task {task_index}/{task_count}: "{map_name}" x{repeat_total}.')
            # Everything beyond current_task/current_repeat/map/action is
            # new -- for the Dashboard's Status Readout hover-expand (shows
            # the fuller picture of what's actually running, not just the
            # always-visible summary row). mode/stage/difficulty don't
            # apply the same way to every mode (Expedition has no "stage",
            # Raid/Infinite/Mastery lock difficulty, Challenge has neither
            # in the Task Queue sense) -- placeholder "-" rather than
            # leaving a PREVIOUS task's value stale on screen.
            self._set_status(current_task=f"{task_index} / {task_count}", current_repeat=f"1 / {repeat_total}",
                              map=map_name, action="Starting...", mode=mode, stage=str(task.get("stage") or "-"),
                              difficulty=task.get("difficulty") or "-", play_mode=task.get("play_mode") or "solo",
                              macro=task.get("macro") or "-")

            # Everything from the lobby through the first teleport-in runs
            # ONCE per task -- every repeat after that re-enters the same
            # stage directly via Repeat Stage (see _handle_match_result),
            # skipping the lobby/gamemode/map/stage picks entirely.
            # Consumed one-shot: the started-already-in-game case (see _run's
            # nav_unitmanager check) skips even that first entry -- the user
            # is standing in the stage right now, so the run picks up at Pre
            # Start. Any retry/recovery after that goes through the real
            # setup, since a recovery lands back in the lobby.
            skip_setup = getattr(self, "_skip_first_task_setup", False)
            self._skip_first_task_setup = False
            if skip_setup:
                self._log(f'[Macro] Already in-game -- treating the current stage as "{map_name}" '
                           f'and skipping stage entry.')
            elif not self._run_task_setup(hwnd, stop_event, task, mode, map_name, coords, scroll_power,
                                            scroll_nudges, webhook):
                if stop_event.is_set():
                    return False
                if not self._recover_to_lobby(hwnd, stop_event):
                    return not stop_event.is_set()
                continue

            task_failed = False
            # True on the genuine first repeat, AND on any later repeat that
            # just went through a full fresh _run_task_setup re-entry
            # (matchmaking's own repeat re-entry, or resuming after a
            # Challenge interleave below) -- either way that's a real fresh
            # entry into the stage, needing Team Loadout and the Walk Path
            # block to run again exactly like the actual first repeat does,
            # not "repeat_index == 1" alone (which used to wrongly skip both
            # on every repeat past the first even when _run_task_setup had
            # just fully re-run, confirmed from a real report: Walk Path
            # silently skipped resuming a task after a Challenge interleave).
            fresh_entry = True
            for repeat_index in range(1, repeat_total + 1):
                self._set_status(current_repeat=f"{repeat_index} / {repeat_total}")
                battle_started = time.time()
                result = self._play_one_match(hwnd, stop_event, task, default_walk_paths,
                                                first_repeat=fresh_entry, webhook=webhook)
                fresh_entry = False
                if result is None:
                    if stop_event.is_set():
                        return False
                    task_failed = True
                    break
                duration = self._format_duration(time.time() - battle_started)

                # Consecutive-loss fail-safe: a genuine unbroken loss streak
                # on THIS map (not just losses somewhere in the run) usually
                # means something's actually wrong -- a bad loadout, a stuck
                # client, a map that's too hard -- rather than plain bad
                # luck, so it's worth a full Roblox restart instead of just
                # keep feeding it more attempts. Any win, or a loss on a
                # DIFFERENT map (Challenge/task interleaving can switch maps
                # between repeats), resets the count.
                if result == "loss" and self._consecutive_loss_map == map_name:
                    self._consecutive_losses += 1
                elif result == "loss":
                    self._consecutive_loss_map = map_name
                    self._consecutive_losses = 1
                else:
                    self._consecutive_losses = 0
                    self._consecutive_loss_map = None
                restart_needed = self._consecutive_losses >= MAX_CONSECUTIVE_LOSSES_SAME_MAP

                is_last_repeat = repeat_index == repeat_total
                # Challenge used to only ever get checked once, right at the
                # very start of a Start press, before the Task Queue even
                # began -- a task with a huge repeat count (effectively
                # "forever") meant Challenge's own cooldown resetting
                # partway through a run was never revisited at all. Checked
                # here too now, between every repeat: if a slot's ready,
                # this repeat forces a real Leave Stage (same as the task's
                # own last repeat would) instead of Repeat Stage, so the
                # task cleanly steps out of its own stage before Challenge's
                # navigation (which starts from the lobby) runs.
                challenge_wants_in = (not is_last_repeat) and self._challenge_has_ready_stage()
                if not self._handle_match_result(hwnd, stop_event, task, result, duration, webhook,
                                                  repeat=(not is_last_repeat) and not challenge_wants_in
                                                  and not restart_needed):
                    if stop_event.is_set():
                        return False
                    task_failed = True
                    break
                if self._checkpoint(stop_event):
                    return False

                if restart_needed:
                    self._log(f'[Macro] Lost {self._consecutive_losses}x in a row on "{map_name}" -- '
                               f'restarting Roblox as a fail-safe.')
                    screenshot_path = self._save_debug_screenshot_unconditional(hwnd, "consecutive_loss_restart")
                    self._send_event_webhook(
                        webhook, task, "Consecutive Losses -- Restarting Roblox",
                        f'Lost {self._consecutive_losses}x in a row on "{map_name}" -- restarting Roblox '
                        f"before retrying.", 0xE05A6D, screenshot_path)
                    self._consecutive_losses = 0
                    self._consecutive_loss_map = None
                    if not self._attempt_rejoin(hwnd, stop_event):
                        if stop_event.is_set():
                            return False
                        task_failed = True
                        break
                    if self._current_hwnd and wm.is_window(self._current_hwnd):
                        hwnd = self._current_hwnd
                    if not is_last_repeat:
                        # Leave Stage above already left the stage entirely
                        # (repeat=False), and the restart just left the
                        # lobby fresh too -- re-enter the task from scratch
                        # exactly like the very first repeat did, same as
                        # the Challenge-interleave case right below.
                        if not self._run_task_setup(hwnd, stop_event, task, mode, map_name, coords,
                                                      scroll_power, scroll_nudges, webhook):
                            if stop_event.is_set():
                                return False
                            task_failed = True
                            break
                        fresh_entry = True
                    continue

                if self._act4_wants_in:
                    # A Crow Relic dropped this repeat (see _handle_match_result,
                    # which already forced a Leave Stage so we're back on the
                    # lobby). Go clear Act 4 with the task's OWN Act 4 Macro
                    # Operation, then re-enter the farm task from scratch --
                    # same interleave shape as Challenge just below.
                    self._act4_wants_in = False
                    self._run_act4_diversion(hwnd, stop_event, task, coords, scroll_power,
                                              scroll_nudges, default_walk_paths, webhook)
                    if self._checkpoint(stop_event):
                        return False
                    if self._current_hwnd and wm.is_window(self._current_hwnd):
                        hwnd = self._current_hwnd
                    self._log(f'[Macro] Act 4 divert finished -- resuming "{map_name}".')
                    if not is_last_repeat:
                        if not self._run_task_setup(hwnd, stop_event, task, mode, map_name, coords,
                                                      scroll_power, scroll_nudges, webhook):
                            if stop_event.is_set():
                                return False
                            task_failed = True
                            break
                        fresh_entry = True
                    continue

                if challenge_wants_in:
                    self._log(f'[Macro] Challenge stage ready -- pausing "{map_name}" to run it '
                               f'before continuing.')
                    self._run_challenges(hwnd, stop_event, coords, default_walk_paths, webhook)
                    if self._checkpoint(stop_event):
                        return False
                    self._log(f'[Macro] Challenge pass finished -- resuming "{map_name}".')
                    # Left the stage entirely for Challenge (repeat=False
                    # above already did Leave Stage + Return to Lobby), so
                    # this repeat re-enters the task from scratch exactly
                    # like the very first one did, not a quick Repeat Stage
                    # requeue -- there's no stage left to requeue INTO.
                    if not self._run_task_setup(hwnd, stop_event, task, mode, map_name, coords,
                                                  scroll_power, scroll_nudges, webhook):
                        if stop_event.is_set():
                            return False
                        task_failed = True
                        break
                    fresh_entry = True
                    continue

                if not is_last_repeat:
                    if task.get("play_mode") == "matchmaking":
                        # Leave Stage (see _handle_match_result -- matchmaking
                        # always leaves, never Repeat Stage) puts us back on
                        # the lobby, not mid-match -- the next repeat needs
                        # the FULL lobby -> map -> stage -> Enter Matchmaking
                        # sequence again, not just a teleport-in wait.
                        if not self._run_task_setup(hwnd, stop_event, task, mode, map_name, coords,
                                                      scroll_power, scroll_nudges, webhook):
                            if stop_event.is_set():
                                return False
                            task_failed = True
                            break
                        fresh_entry = True
                    elif not self._wait_teleport_in(hwnd, stop_event, webhook, task):
                        if stop_event.is_set():
                            return False
                        task_failed = True
                        break

            if not task_failed:
                return True  # finished every repeat cleanly

            self._log(f'[Macro] Task {task_index}/{task_count} hit a problem mid-run -- recovering to the lobby.')
            if not self._recover_to_lobby(hwnd, stop_event):
                return not stop_event.is_set()  # couldn't even get back to the lobby -- nothing left to retry

        self._log(f'[Macro] Task {task_index}/{task_count} still failing after '
                   f'{TASK_RECOVERY_ATTEMPTS} attempts -- giving up on it.')
        screenshot_path = self._save_debug_screenshot_unconditional(hwnd, "task_gave_up")
        self._send_event_webhook(
            webhook, task, "Task Gave Up",
            f"Task {task_index}/{task_count} still failing after {TASK_RECOVERY_ATTEMPTS} recovery "
            f"attempts -- moving on to the next task.", 0xE05A6D, screenshot_path)
        return True

    def _recover_to_lobby(self, hwnd, stop_event: threading.Event) -> bool:
        """Best-effort recovery after a mid-task failure -- tries Leave
        Stage first (in case a match/Pre Start is still up), then spams
        Back (in case it's stuck on a menu instead), then confirms the
        lobby is actually reached. Returns whether it actually got there."""
        self._log("[Macro] Attempting to recover to the lobby...")
        self._set_status(action="Recovering...")
        if self._checkpoint(stop_event):
            return False
        try:
            leave_match = vision.find_image(hwnd, "leave_stage")
        except vision.TemplateNotFound:
            leave_match = None
        if leave_match is not None:
            self._log(f"[Macro] Found Leave Stage (score {leave_match['score']:.2f}) -- clicking it.")
            vision.click_match(self._mouse, hwnd, leave_match)
            time.sleep(SETTLE_DELAY)
            self._click_return_to_lobby_if_found(hwnd, stop_event)
        if self._checkpoint(stop_event):
            return False
        self._spam_back_until_gone(hwnd, stop_event)
        if self._checkpoint(stop_event):
            return False
        return self._ensure_lobby(hwnd, stop_event)

    def _run_task_setup(self, hwnd, stop_event: threading.Event, task: dict, mode: str, map_name: str,
                          coords: dict, scroll_power: int, scroll_nudges: int, webhook: dict = None) -> bool:
        """Lobby -> Play -> Story/Raid -> map -> stage/act -> difficulty ->
        confirm -> matchmaking/solo -> teleport-in. Runs once per TASK, not
        once per repeat -- see the repeat loop in _run. Event mode takes its
        own lobby entry (nav_event -> event_gamemode -> Act) with no map or
        difficulty, then rejoins the shared confirm/Solo/Matchmaking tail."""
        if mode == "event":
            # Event is reached straight from the lobby (nav_event), not
            # through Play/gamemode/map, and has no difficulty picker -- so
            # it reaches the chosen Act and then falls straight through to
            # the shared confirm + Solo/Matchmaking tail below. Same
            # retried-from-the-lobby loop as the map path, for the same
            # reason (a failed attempt leaves nothing safe to assume).
            reached_event = False
            for attempt in range(1, MAP_SELECT_RETRY_ATTEMPTS + 1):
                if self._checkpoint(stop_event):
                    return False
                if attempt > 1:
                    self._log(f"[Macro] Retrying Event entry from the lobby "
                               f"(attempt {attempt}/{MAP_SELECT_RETRY_ATTEMPTS})...")
                if self._reach_event_act_selected(hwnd, stop_event, task.get("stage") or "1",
                                                   scroll_power, scroll_nudges):
                    reached_event = True
                    break
                if stop_event.is_set():
                    return False
            if not reached_event:
                self._log(f'[Macro] Couldn\'t reach the Event Act after {MAP_SELECT_RETRY_ATTEMPTS} '
                           f'attempts -- stopping.')
                return False
            if self._checkpoint(stop_event):
                return False
        else:
            # Lobby -> Play -> Story/Raid -> map search, retried wholesale from
            # the lobby if the map search fails and backing out succeeds (see
            # _spam_back_until_gone) -- a failed search leaves nothing about
            # "already on the gamemode menu" safe to assume anymore, so each
            # attempt re-checks from scratch rather than resuming partway
            # through.
            reached_map = False
            for attempt in range(1, MAP_SELECT_RETRY_ATTEMPTS + 1):
                if self._checkpoint(stop_event):
                    return False
                if attempt > 1:
                    self._log(f"[Macro] Retrying from the lobby (attempt {attempt}/{MAP_SELECT_RETRY_ATTEMPTS})...")
                if self._reach_map_selected(hwnd, stop_event, map_name, mode, scroll_power, scroll_nudges):
                    reached_map = True
                    break
                if stop_event.is_set():
                    return False
            if not reached_map:
                self._log(f'[Macro] Couldn\'t reach map "{map_name}" after {MAP_SELECT_RETRY_ATTEMPTS} attempts -- stopping.')
                return False
            if self._checkpoint(stop_event):
                return False

            if mode == "expedition":
                # No stage-row picker to click through -- just the difficulty
                # stepper, straight after the map.
                time.sleep(DIFFICULTY_CLICK_DELAY)
                self._select_expedition_difficulty(hwnd, stop_event, task.get("difficulty") or "1")
            else:
                stage = task.get("stage") or "1"
                if not self._select_stage(hwnd, stop_event, stage, mode):
                    return False
                if self._checkpoint(stop_event):
                    return False

                # Raid's Acts are locked to Hard in-game, same as Story's
                # Infinite/Mastery (see TASK_DATA.raid.fixedDifficulty) -- no
                # difficulty picker exists for it, so no click happens for it.
                if mode == "raid":
                    self._log('[Macro] Raid is locked to Hard in-game -- no difficulty click needed.')
                elif stage in SPECIAL_STAGES_NO_DIFFICULTY:
                    self._log(f'[Macro] "{stage}" is locked to Hard in-game -- no difficulty click needed.')
                else:
                    # _select_stage already settled (DIFFICULTY_CLICK_DELAY)
                    # right after its own double-click, so the panel/toggle is
                    # done animating in by the time we get here.
                    self._select_difficulty(hwnd, task.get("difficulty") or "Normal", coords)
            if self._checkpoint(stop_event):
                return False

        # nav_select_stage is a confirm button that finalizes the stage/
        # difficulty pick -- Start/Enter Matchmaking doesn't actually
        # appear/work until it's pressed, so it needs an actual (verified,
        # retried) click, not just a wait. Solo-only: matchmaking goes
        # straight to Enter Matchmaking instead, since this doesn't
        # reliably show up the same way for it.
        if task.get("play_mode") != "matchmaking":
            confirm_image = "exp_select_stage" if mode == "expedition" else "nav_select_stage"
            self._set_status(action="Clicking Select Stage...")
            if not self._click_and_verify_gone(hwnd, stop_event, confirm_image, STAGE_SCREEN_TIMEOUT):
                self._log(f'[Macro] "{confirm_image}" never showed up -- stopping.')
                return False
        if self._checkpoint(stop_event):
            return False

        # Solo has its own direct Start button -- Enter Matchmaking is
        # multiplayer-only and was never going to appear in Solo mode, which
        # is exactly why it kept sitting there waiting on it and looking
        # like it was "going to matchmaking" regardless of this setting.
        if task.get("play_mode") == "matchmaking":
            if not self._click_enter_matchmaking(hwnd, stop_event, coords, mode):
                return False
            if self._checkpoint(stop_event):
                return False
            self._log(f"[Macro] Waiting for the lobby to fill (up to {MATCHMAKING_TELEPORT_TIMEOUT / 60:.0f} "
                       f"min) -- matchmaking has to find real players before it teleports in.")
            if not self._wait_teleport_in(hwnd, stop_event, webhook, task, timeout=MATCHMAKING_TELEPORT_TIMEOUT):
                return False
        else:
            self._log("[Macro] Solo mode -- clicking Start (retrying up to "
                       f"{SOLO_START_RETRY_ATTEMPTS} times if it doesn't teleport).")
            if not self._click_start_and_wait_teleport(hwnd, stop_event, webhook, task):
                return False
        return not self._checkpoint(stop_event)

    def _play_one_match(self, hwnd, stop_event: threading.Event, task: dict, default_walk_paths: dict,
                          first_repeat: bool = True, webhook: dict = None):
        """Assumes teleport-in already happened -- the initial one from
        _run_task_setup, or a repeat's re-teleport after Repeat Stage (see
        _handle_match_result). Start Game settings check, Pre Start, the
        actual Start Game click, then watches for Victory/Defeat. Runs once
        per repeat. first_repeat gates the default walk and any "Once"
        Pre Start block so they only fire on the task's first entry into
        this stage, not on every repeat (see _run_prestart). Returns
        "win"/"loss", or None on failure/stop."""
        if not self._start_game_or_reset_via_settings(hwnd, stop_event, task.get("play_mode")):
            return None
        if self._checkpoint(stop_event):
            return None

        if not self._run_prestart(hwnd, stop_event, task, default_walk_paths, first_repeat):
            return None
        if self._checkpoint(stop_event):
            return None

        self._log("[Macro] Pre Start finished -- starting the round.")
        self._set_status(action="Starting the round...")
        if self._checkpoint(stop_event):
            return None
        # Start Game genuinely applies to Expedition too (it can show up
        # more than once, similar to Infinite mode) -- not skipped here.
        self._wait_out_start_game_warning(hwnd, stop_event)
        if self._checkpoint(stop_event):
            return None
        start_name, start_match = self._find_start_game_button(hwnd, stop_event, START_GAME_BUTTON_WAIT_TIMEOUT)
        if start_match is None:
            # Not fatal: Start Game may already have been pressed by the
            # leader (or Auto Vote Start already handles it) earlier in
            # _start_game_or_reset_via_settings -- its absence here just
            # means the round is already starting on its own.
            self._log("[Macro] Start Game not found -- already started, continuing.")
        else:
            for attempt in range(1, START_GAME_CLICK_RETRY_ATTEMPTS + 1):
                debug_path = self._debug_save(hwnd, start_name, start_match)
                suffix = f" Debug: {debug_path}" if debug_path else ""
                self._log(f"[Macro] Found Start Game ({start_name}, score {start_match['score']:.2f}) -- "
                           f"clicking it (attempt {attempt}/{START_GAME_CLICK_RETRY_ATTEMPTS}).{suffix}")
                if not wm.activate_window(hwnd):
                    self._log("[Macro] Couldn't confirm focus before clicking Start Game -- "
                               "click may not register.")
                # Z first -- same deselect pressed before every unit placement:
                # if Pre Start's last block left a unit selected/placing, the
                # cursor still owns that state and the Start Game click gets
                # eaten by it instead of landing on the button. (After
                # activate_window, so the tap actually reaches the game.)
                self._keyboard.tap(ord("Z"))
                time.sleep(0.1)
                vision.click_match(self._mouse, hwnd, start_match)
                time.sleep(START_GAME_CLICK_VERIFY_SETTLE)
                if self._checkpoint(stop_event):
                    return None

                start_name, start_match = self._find_start_game_button(hwnd)
                if start_match is None:
                    break
                if attempt == START_GAME_CLICK_RETRY_ATTEMPTS:
                    self._log(f"[Macro] Start Game still showing after {START_GAME_CLICK_RETRY_ATTEMPTS} "
                               f"clicks -- the round may not have actually started. Continuing anyway.")
                    screenshot_path = self._save_debug_screenshot_unconditional(hwnd, "start_game_click_stuck")
                    self._send_event_webhook(
                        webhook, task, "Start Game Click Not Registering",
                        f"Clicked Start Game {START_GAME_CLICK_RETRY_ATTEMPTS} times but it's still showing -- "
                        f"the round may not have actually started.", 0xE05A6D, screenshot_path)
        if self._checkpoint(stop_event):
            return None

        # Team Loadout (including its Include/Exclude equipment choice) is
        # applied earlier in Pre Start -- see _apply_team_loadout.
        self._set_status(action="Battle...")
        self._log("[Macro] Moving into Battle.")

        battle_blocks = self._load_battle_blocks(task)
        self._battle_block_index = 0
        self._battle_block_state = {}
        self._release_quick_place_shift()  # safety net -- never enter a match with Shift stuck down from before
        # exp_extract is a recurring checkpoint choice (Extract AND Continue
        # offered side by side), not a one-shot terminal event -- confirmed
        # from a real test run where extract_after=2 still extracted on the
        # very first sighting instead of continuing through 2 checkpoints
        # first. So this DOES need real counting: decline (click the
        # "exp_extract_continue" choice on this same screen) every sighting
        # up to extract_after, only accept the one right after that.
        self._expedition_extract_count = 0
        self._expedition_extract_accept_at = max(0, int(task.get("extract_after") or 0)) + 1
        self._exp_last_sighting_at = 0.0  # fresh match, fresh sighting-debounce clock (see EXP_COLOR_SIGHTING_DEBOUNCE)
        # Spirit City Act 3's boss/cutscene "Click anywhere to close" popup
        # (see _click_close_popup_if_found) only ever shows up there.
        watch_close_popup = (task.get("mode") == "raid" and task.get("map") == "Spirit City"
                              and str(task.get("stage")) == "3")
        return self._wait_for_match_result(hwnd, stop_event, battle_blocks, first_repeat, task.get("macro"),
                                             task.get("mode"), watch_close_popup, webhook, task)



    def _wait_for_match_result(self, hwnd, stop_event: threading.Event, battle_blocks: list = None,
                                 first_repeat: bool = True, macro_name: str = None, mode: str = None,
                                 watch_close_popup: bool = False, webhook: dict = None, task: dict = None) -> str:
        self._log("[Macro] Battle in progress -- watching for Victory/Defeat...")
        self._set_status(action="Battle in progress...")
        battle_blocks = battle_blocks or []
        deadline = time.time() + MATCH_RESULT_TIMEOUT
        while time.time() < deadline:
            if self._checkpoint(stop_event):
                return None
            if battle_blocks:
                self._run_battle_blocks_tick(hwnd, stop_event, battle_blocks, first_repeat, macro_name)
                if self._checkpoint(stop_event):
                    return None

            # Roblox's own Reconnect/Retry prompt can show up mid-battle too,
            # not just during the teleport-in wait -- this used to only be
            # checked there, so a disconnect that happened AFTER teleporting
            # in successfully was invisible to the macro entirely: it just
            # kept polling for Victory/Defeat/exp_continue/exp_extract
            # against a dead screen until MATCH_RESULT_TIMEOUT gave up,
            # instead of recognizing the disconnect and rejoining promptly.
            for name in RECONNECT_IMAGE_NAMES:
                try:
                    reconnect_match = vision.find_image(hwnd, name)
                except vision.TemplateNotFound:
                    continue
                if reconnect_match is not None:
                    self._handle_disconnect(hwnd, stop_event, webhook, task, "disconnected")
                    return None

            if watch_close_popup:
                self._click_close_popup_if_found(hwnd)

            if mode == "expedition":
                result = self._check_expedition_wave_result(hwnd, stop_event)
                if result is not None:
                    return result
                time.sleep(MATCH_RESULT_POLL_INTERVAL)
                continue

            try:
                victory_match = vision.find_image(hwnd, "victory")
            except vision.TemplateNotFound as exc:
                self._log(f"[Macro] {exc}")
                return None
            if victory_match is not None:
                self._log(f"[Macro] Victory! (score {victory_match['score']:.2f})")
                return "win"
            try:
                defeat_match = vision.find_image(hwnd, "defeat")
            except vision.TemplateNotFound as exc:
                self._log(f"[Macro] {exc}")
                return None
            if defeat_match is not None:
                self._log(f"[Macro] Defeat. (score {defeat_match['score']:.2f})")
                return "loss"
            time.sleep(MATCH_RESULT_POLL_INTERVAL)
        self._log(f'[Macro] Neither "victory" nor "defeat" matched within {MATCH_RESULT_TIMEOUT / 60:.0f} min. '
                   f'If the result screen was actually showing, its reference image isn\'t matching your '
                   f'setup -- add your own crop via Settings > General > Image Manager.')
        self._save_debug_screenshot_unconditional(hwnd, "match_result_timeout")
        return None





    def debug_force_rejoin(self, hwnd, hwnd_getter=None) -> bool:
        """Settings > Debug > "Force Rejoin" -- manually fires the same
        deep-link rejoin _handle_disconnect uses, on demand, with no real
        disconnect needed first. Point of this: resetting Roblox back to a
        known state (the lobby) between test iterations after a code
        change used to mean alt-tabbing over and closing/reopening it by
        hand every time. hwnd_getter is set to the caller's own live
        game_hwnd lookup for the duration of the call (mirrors what a real
        run wires up in _run) so the poll loop can follow Roblox through a
        full relaunch onto whatever new hwnd main.py's own dock watchdog
        re-docks it under, then restored back to whatever it was before --
        there's no run in progress to own this permanently."""
        self._log("[Debug] Forcing a rejoin (deep link)...")
        previous_getter = self._hwnd_getter
        if hwnd_getter is not None:
            self._hwnd_getter = hwnd_getter
        try:
            ok = self._attempt_rejoin(hwnd, threading.Event())
        finally:
            self._hwnd_getter = previous_getter
        self._log(f"[Debug] Force rejoin {'succeeded' if ok else 'failed'}.")
        return ok

    def _save_debug_screenshot_unconditional(self, hwnd, name: str) -> str:
        """A full-window screenshot saved regardless of Settings > Debug >
        "Debug Match Screenshots" -- for failures rare and diagnostically
        useful enough (a long timeout, a menu that never opened) that it's
        worth it unconditionally rather than only when that toggle happened
        to already be on, so a user's bug report actually comes with
        evidence of what was on screen instead of a blind guess. Returns
        the saved path (also attachable to a Discord webhook, see
        _send_event_webhook), or None if it couldn't be saved."""
        try:
            left, top, right, bottom = wm.get_window_rect_screen(hwnd)
            path = vision.save_region_debug(hwnd, name, (0, 0, right - left, bottom - top))
            self._log(f"[Macro] Saved a screenshot for troubleshooting: {path}")
            return path
        except Exception as exc:
            self._log(f"[Macro] Couldn't save a debug screenshot: {exc}")
            return None










    @staticmethod
    def _format_duration(seconds: float) -> str:
        seconds = int(seconds)
        m, s = divmod(seconds, 60)
        return f"{m}m {s}s" if m else f"{s}s"

    def _handle_match_result(self, hwnd, stop_event: threading.Event, task: dict, result: str, duration: str,
                              webhook: dict, repeat: bool) -> bool:
        label = "Victory" if result == "win" else "Defeat"

        # A Battle-phase quick-place chain (see _run_place_unit_block) could
        # still be holding Shift down right up to the moment Victory/Defeat
        # actually landed -- released here, before anything else, so it's
        # never still held into the result screen and beyond.
        self._release_quick_place_shift()

        # The reward row streams its items in one at a time, so give it a beat
        # to finish before either the screenshot or the relic check reads the
        # screen (see RESULT_CAPTURE_DELAY) -- otherwise both can catch a
        # half-populated row.
        time.sleep(RESULT_CAPTURE_DELAY)

        # Only the CAPTURE (one screenshot) has to happen while the result
        # screen is actually still up -- the run's result is now reported by
        # SENDING that image to the webhook (plus a rendered win/loss card),
        # not by OCRing the stats/reward panels off the screen, so there's
        # just the one grab left here and the send itself runs on its own
        # thread instead of holding up Repeat/Leave Stage. Only captured when
        # a webhook is actually configured to receive it.
        self._set_status(action=f"Capturing {label} screen...")
        webhook_wants_shot = bool(webhook and webhook.get("enabled") and webhook.get("url"))
        result_screenshot = self._capture_result_screenshot(hwnd) if webhook_wants_shot else None

        # Relic-drop auto-divert (event farm tasks that opted in, wins only --
        # Crow Relics don't drop on a loss). If one's sitting on the reward
        # row, flag Act 4 to be cleared once we've cleanly Left Stage here, and
        # force a Leave (not Repeat) below so the divert's own navigation
        # starts from the lobby. The Act 4 run itself happens back in _run_task
        # (see the _act4_wants_in branch there).
        self._act4_wants_in = False
        if (result == "win" and task.get("mode") == "event" and task.get("act4_on_drop")
                and self._relic_dropped(hwnd)):
            self._act4_wants_in = True
            repeat = False
            self._log("[Macro] Crow Relic dropped -- leaving this stage to go clear Act 4.")

        map_name = task.get("map") or "-"
        threading.Thread(
            target=self._finish_match_result_background,
            args=(result, map_name, duration, task, webhook, result_screenshot),
            daemon=True,
        ).start()
        self._log(f"[Macro] {label} ({duration}) -- reporting in the background.")

        # The cursor is moved to the same near-empty corner
        # _reset_unit_info_panel uses first, so a leftover hover
        # state/tooltip from whatever was under the cursor can't throw off
        # whichever button gets clicked next.
        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        self._mouse.move_to(left + self._coords["unit_info_reset_x"], top + self._coords["unit_info_reset_y"])
        time.sleep(0.1)

        # A level-up reward-card modal can land exactly as the match ends
        # (confirmed from a real stuck report: "Couldn't find repeat_stage"
        # after the FULL 8s search window, twice, both times ~9s after
        # Extracted -- consistent with something blocking the result panel
        # the whole time, not just a slow render), before the Repeat/Leave
        # Stage panel has even rendered. Dismissed here too, not just
        # mid-battle, looping until it's actually gone (it can re-show
        # between multiple level-ups) or REWARD_CARD_CLEAR_TIMEOUT runs out.
        card_deadline = time.time() + REWARD_CARD_CLEAR_TIMEOUT
        dismissed_a_card = False
        while self._dismiss_reward_card_if_found(hwnd) and time.time() < card_deadline:
            dismissed_a_card = True
            if stop_event is not None and stop_event.is_set():
                break
            time.sleep(0.5)
        if dismissed_a_card:
            # The dismiss click above lands dead center of the screen --
            # right where an item/reward tooltip hovers in and covers the
            # Repeat/Leave Stage buttons (reported from real testing). Reset
            # back to the same near-empty corner used above before this
            # loop ever ran, so that hover state doesn't linger into the
            # repeat_stage/leave_stage search below.
            self._mouse.move_to(left + self._coords["unit_info_reset_x"], top + self._coords["unit_info_reset_y"])
            time.sleep(0.1)

        # Matchmaking never uses Repeat Stage, even with more repeats left --
        # a matchmade lobby is a one-shot party for that specific match, not
        # something "repeat the same stage" can just re-queue into the way
        # it can for Story/Solo, so every matchmaking repeat has to leave
        # and go through Enter Matchmaking again from the lobby (see
        # _run_task's repeat loop, which re-runs _run_task_setup instead of
        # _wait_teleport_in whenever this is why it's about to see Leave
        # Stage clicked with more repeats still left).
        is_matchmaking = task.get("play_mode") == "matchmaking"
        if repeat and not is_matchmaking:
            # More repeats left on this task -- Repeat Stage re-queues the
            # same stage directly, skipping the lobby/gamemode/map/stage
            # picks entirely (see _run_task_setup, which only runs once per
            # task, not once per repeat).
            self._set_status(action=f"{label} -- clicking Repeat Stage...")
            if not self._click_and_verify_gone(hwnd, stop_event, "repeat_stage", NAV_CLICK_TIMEOUT):
                self._log('[Macro] "Repeat Stage" not found -- can\'t continue this task\'s repeats, stopping.')
                return False
            # _click_and_verify_gone only confirms the repeat_stage BUTTON
            # image is gone, not that the whole Victory/Defeat results panel
            # actually closed -- confirmed from a real capture: the button
            # itself can visually change/disappear (so the check above
            # reports success) while the full result modal is still up on
            # screen behind it, and Pre Start went on to place units right
            # through/behind it. The banner ribbon is the more reliable
            # "actually closed" signal, so wait for THAT to clear too before
            # treating the repeat as ready to continue into. Best-effort --
            # a still-showing banner after the timeout just gets logged, not
            # treated as a hard failure, since the repeat may have gone
            # through fine underneath anyway.
            self._wait_for_image_gone(hwnd, (label.lower(),), REPEAT_STAGE_MODAL_CLEAR_TIMEOUT, stop_event)
            return True

        # Last repeat of this task (or the whole queue) -- back out to the
        # lobby so the next task's setup (or a clean stop) starts from a
        # known state instead of sitting on the result screen. Verified/
        # retried, not a one-shot click -- a dropped click here used to
        # just leave the run sitting on the result screen forever with
        # nothing else ever noticing or retrying.
        self._set_status(action=f"{label} -- clicking Leave Stage...")
        if not self._click_and_verify_gone(
                hwnd, stop_event, "leave_stage", NAV_CLICK_TIMEOUT, success_name="return"):
            self._log('[Macro] "Leave Stage" not found -- stopping.')
            return False
        self._click_return_to_lobby_if_found(hwnd, stop_event)
        return True

    def _finish_match_result_background(self, result: str, map_name: str, duration: str, task: dict,
                                          webhook: dict, result_screenshot: str = None) -> None:
        self._record_result(result, map_name, duration)
        try:
            self._send_result_webhook(webhook, result, task, duration, result_screenshot)
        finally:
            # A temp file per match -- deleted once it's been sent (or the
            # send was skipped), whatever the outcome, so they don't pile up.
            if result_screenshot:
                try:
                    os.remove(result_screenshot)
                except OSError:
                    pass

    def _capture_result_screenshot(self, hwnd) -> str:
        """Full-window screenshot of the Victory/Defeat screen, saved to a
        temp PNG for the match-result webhook to attach. Captured
        synchronously while the result screen is still up (the send itself,
        which composites the win/loss card under this and uploads it, then
        runs on the background thread, which deletes this file afterward).
        Returns the path, or None if it couldn't be captured."""
        try:
            import tempfile
            fd, path = tempfile.mkstemp(prefix="ae_result_", suffix=".png")
            os.close(fd)
            saved = vision.save_window_screenshot(hwnd, path)
            if not saved:
                try:
                    os.remove(path)
                except OSError:
                    pass
                return None
            return saved
        except Exception as exc:
            self._log(f"[Macro] Couldn't capture the result screenshot for the webhook: {exc}")
            return None

    @staticmethod
    def _tree_rows(rows) -> str:
        """Tree-style lines for one inline embed field (the field's own name
        is the header, so no bracketed header here): each (label, value) as a
        box-drawing branch with a bold label and a code value, last row on
        the └ corner. e.g. ├ **Elapsed:** `11h 27m` / └ ..."""
        rows = [r for r in rows if r is not None]
        lines = []
        for i, (label, value) in enumerate(rows):
            branch = "└" if i == len(rows) - 1 else "├"
            lines.append(f"{branch} **{label}:** `{value}`")
        return "\n".join(lines)

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        """Session runtime as "11h 27m" / "27m 4s" / "45s" -- longer scale
        than _format_duration (a single match), so it leads with hours."""
        seconds = max(0, int(seconds))
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}h {m}m"
        if m:
            return f"{m}m {s}s"
        return f"{s}s"

    def _send_result_webhook(self, webhook: dict, result: str, task: dict, duration: str,
                              screenshot_path: str = None) -> None:
        url = (webhook or {}).get("url")
        if not url or not webhook.get("enabled"):
            return
        from . import webhook as webhook_module

        is_win = result == "win"
        map_name = task.get("map") or "-"
        mode = task.get("mode") or "story"
        raw_stage = task.get("stage") or "-"
        # Raid/Event pick Acts; Story's Infinite/Mastery are named; the rest
        # are numbered stages.
        if mode in ("raid", "event"):
            stage = f"Act {raw_stage}" if raw_stage != "-" else "-"
        elif str(raw_stage).isdigit():
            stage = f"Stage {raw_stage}"
        else:
            stage = str(raw_stage)
        # Raid and Infinite/Mastery stages are locked to Hard in-game (see
        # _run_task_setup's identical check) -- the task's own difficulty
        # setting never actually applies there, so reporting it verbatim
        # was showing e.g. "Normal" for a run that was really Hard. Event has
        # no difficulty at all.
        if mode == "raid" or raw_stage in SPECIAL_STAGES_NO_DIFFICULTY:
            difficulty = "Hard"
        elif mode == "event":
            difficulty = "-"
        else:
            difficulty = task.get("difficulty") or "-"

        # Fresh session/all-time snapshot -- already includes THIS match, since
        # the webhook is sent right after _record_result bumped the counters.
        try:
            snap = self._get_run_stats() or {}
        except Exception:
            snap = {}
        sw, sl = snap.get("session_wins", 0), snap.get("session_losses", 0)
        aw, al = snap.get("all_time_wins", 0), snap.get("all_time_losses", 0)
        session_time = self._format_elapsed(time.time() - snap["session_start"]) if snap.get("session_start") else "-"

        where = " · ".join(p for p in (map_name, stage, difficulty) if p and p != "-")
        session_rate = f"{round(sw / (sw + sl) * 100)}%" if (sw + sl) else "-"
        all_time_rate = f"{round(aw / (aw + al) * 100)}%" if (aw + al) else "-"
        tuc = snap.get("time_until_challenge", "-")
        runs_per_hour = snap.get("runs_per_hour", "-")

        # Inline, tree-style fields (bold labels, code values) side by side --
        # Match / Session / All Time. The visual win/loss tiles + activity
        # grid live in the separate status-card image below.
        fields = [
            {"name": "⚔️ Match", "value": self._tree_rows([
                ("Result", "Victory \U0001F3C6" if is_win else "Defeat \U0001F480"),
                ("Duration", duration or "-"),
            ] + ([("Stage", where)] if where else [])), "inline": True},
            {"name": "\U0001F4CA Session", "value": self._tree_rows([
                ("Elapsed", session_time),
                ("Record", f"{sw}W · {sl}L"),
                ("Rate", session_rate),
                ("Runs/h", runs_per_hour),
                ("Challenge", tuc),
            ]), "inline": True},
            {"name": "\U0001F3C6 All Time", "value": self._tree_rows([
                ("Record", f"{aw}W · {al}L"),
                ("Rate", all_time_rate),
            ]), "inline": False},
        ]
        # A plain incoming webhook can't render real buttons (those need a
        # bot/app-owned webhook) -- so the links go here as clickable masked
        # markdown links, which render everywhere. Full width, at the bottom.
        links = (f"[\U0001F4AC Discord]({DISCORD_INVITE_URL})   •   "
                 f"[\U0001F4FA YouTube]({YOUTUBE_URL})   •   "
                 f"[\U00002B50 GitHub]({GITHUB_REPO_URL})")
        fields.append({"name": "\U0001F517 Links", "value": links, "inline": False})
        result_word = "Victory" if is_win else "Defeat"
        description = (f"{result_word} on **{where}** — session match **#{sw + sl}**."
                       if where else f"{result_word} — session match **#{sw + sl}**.")

        version = snap.get("version")
        footer = "Cream's Macro | Anime Expeditions" + (f" · v{version}" if version else "")
        main_embed = {
            "title": "Victory! \U0001F3C6" if is_win else "Defeat \U0001F480",
            "color": 0x3FBF6F if is_win else 0xE05A6D,
            "description": description,
            "fields": fields,
            "footer": {"text": footer},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        mention_id = (webhook or {}).get("mention_id")
        content = f"<@{mention_id}>" if mention_id else ""
        silent = bool(webhook.get("silent"))

        # Two SEPARATE images (each in its own embed, so Discord shows them as
        # distinct pictures rather than one stacked image): the rendered
        # status card, and the live result screenshot. Best-effort -- a failed
        # render/read just drops that one image, never the whole notification.
        embeds = [main_embed]
        files = []
        try:
            import cv2
            from . import status_card
            card = status_card.render_status_card_bgr(
                is_win=is_win, action="Match Finished", last_run_duration=duration,
                last_run_win=is_win, time_until_challenge=tuc,
                session_wins=sw, session_losses=sl, all_time_wins=aw, all_time_losses=al,
                runs_per_hour=runs_per_hour, results=snap.get("results"))
            if card is not None:
                ok, buf = cv2.imencode(".png", card)
                if ok:
                    files.append(("card.png", buf.tobytes()))
                    main_embed["image"] = {"url": "attachment://card.png"}
        except Exception as exc:
            self._log(f"[Macro] Couldn't render the status card: {exc}")
        if screenshot_path and os.path.isfile(screenshot_path):
            try:
                with open(screenshot_path, "rb") as f:
                    files.append(("shot.png", f.read()))
                embeds.append({"color": main_embed["color"], "image": {"url": "attachment://shot.png"}})
            except OSError:
                pass

        try:
            send_result = webhook_module.send_rich(
                url, embeds=embeds, file_attachments=files,
                content=content, silent=silent)
        except Exception as exc:
            self._log(f"[Macro] Webhook send failed: {exc}")
            return
        if send_result["ok"]:
            self._log("[Macro] Webhook sent.")
        else:
            self._log(f"[Macro] Webhook send failed: {send_result['reason']}")

    def _run_prestart(self, hwnd, stop_event: threading.Event, task: dict, default_walk_paths: dict,
                        first_repeat: bool = True) -> bool:
        # Camera setup runs ONCE per fresh entry into a stage (same
        # first_repeat gate as Team Loadout and the Walk Path block below)
        # -- it used to re-run on every repeat as a "per-match reset", but
        # the camera actually holds its angle across a repeat's
        # re-teleport, so re-running just re-dragged an already-correct
        # camera off its spot every loop.
        #
        # Expedition gets its own sequence: the standard drag-down + O
        # zoom-hold doesn't frame Expedition maps right, so it uses the
        # drag-down + Left-arrow rotate instead (the same sequence Settings
        # > Debug > Camera Setup 3 tests) -- 730ms rotate, then a short O
        # tap for a small zoom step (duration user-tunable: Settings >
        # Debug > "Expedition Camera Zoom", 100ms default).
        if first_repeat:
            self._log("[Macro] Pre Start: setting up the camera...")
            self._set_status(action="Setting up camera...")
            try:
                if task.get("mode") == "expedition":
                    camera.run_camera_drag_hold(self._mouse, self._keyboard, hwnd, hold_ms=730,
                                                 o_tap_ms=self._expedition_camera_o_ms)
                else:
                    camera.run_camera_setup(self._mouse, self._keyboard, hwnd)
                self._log("[Macro] Camera setup done.")
            except Exception as exc:
                self._log(f"[Macro] Camera setup failed: {exc}")
        else:
            self._log("[Macro] Repeat of the same stage -- skipping camera setup (already set on entry).")
        if self._checkpoint(stop_event):
            return False

        # Team Loadout only makes sense the FIRST time a task enters a stage --
        # it sets up units/equipment before the match even starts, so
        # re-applying it on every repeat (this used to run unconditionally,
        # same bug the walk below already had a fix for) was pointlessly
        # re-pressing H and re-picking a loadout mid-repeat-cycle, which
        # could land on the wrong screen entirely if the panel wasn't in the
        # exact state it expects and produce exactly the kind of "it bugs
        # out" behavior this was reported as.
        if first_repeat:
            if not self._apply_team_loadout(hwnd, stop_event, task):
                if stop_event.is_set():
                    return False
                self._log("[Macro] Team Loadout didn't actually apply -- failing this match setup so it "
                           "retries from the lobby instead of starting a round with no team equipped.")
                return False
        else:
            self._log("[Macro] Repeat of the same stage -- skipping Team Loadout (already applied on entry).")
        if self._checkpoint(stop_event):
            return False

        # Walk Path used to be a fixed step here, always running before any
        # of the template's own blocks and never reorderable -- now it's a
        # real block within the list itself (see _run_prestart_blocks'
        # "walk_path" handling / _run_walk_path_block), so it runs wherever
        # it's actually positioned relative to Setting/Place Unit blocks
        # instead of always jumping the whole list.
        self._run_prestart_blocks(hwnd, stop_event, task, first_repeat, default_walk_paths)
        if self._checkpoint(stop_event):
            return False
        return True

    def _apply_team_loadout(self, hwnd, stop_event: threading.Event, task: dict) -> bool:
        """Presses H to open the team-select panel, waits for it to
        actually open, clicks the task's Macro Operation template's
        configured Team Loadout slot (1-8, scrolling for 4-8), clicks
        Confirm, picks Include/Exclude for equipment, then presses H again
        to close the panel.

        No team configured, an unrecognized/out-of-range slot number (a
        template config problem retrying can't fix either) still just skip
        with a log line and report success -- there was never a team to
        apply. But once a team num IS valid, actually equipping it is not
        optional: skipping a genuine failure (panel never opened, Confirm
        never showed up) used to just log and move on, which silently
        started the round with no team equipped -- a guaranteed loss
        (confirmed from a real report). Those cases now report failure so
        the caller (_run_prestart) fails this match setup instead of
        starting it, sending the run back through the normal recover-to-
        lobby-and-retry path rather than straight into a loss.
        """
        macro_name = task.get("macro")
        if not macro_name:
            return True
        from . import templates as tpl
        data = tpl.load_template(macro_name)
        blocks = data.get("blocks") or {}
        if isinstance(blocks, list):
            return True  # old-format template -- same as _run_prestart_blocks
        team = blocks.get("team") or ""
        if not team:
            return True
        equipment = blocks.get("equipment") if blocks.get("equipment") in ("include", "exclude") else "include"

        try:
            team_num = int(team)
        except (TypeError, ValueError):
            self._log(f'[Macro] Team Loadout "{team}" isn\'t a recognized slot number -- skipping.')
            return True
        if not (1 <= team_num <= TEAM_LOADOUT_MAX_SUPPORTED):
            self._log(f'[Macro] Team Loadout {team_num} needs scrolling to reach (only 1-'
                       f'{TEAM_LOADOUT_MAX_SUPPORTED} are positioned so far) -- skipping.')
            return True

        self._log(f"[Macro] Applying Team Loadout {team_num} (equipment: {equipment})...")
        self._set_status(action=f"Applying Team Loadout {team_num}...")
        self._keyboard.tap(ord("H"))

        try:
            team_match = vision.wait_for_image(hwnd, "team", timeout=TEAM_PANEL_TIMEOUT, stop_event=stop_event)
        except vision.TemplateNotFound as exc:
            self._log(f"[Macro] Can't confirm the team panel opened: {exc}")
            return False
        if team_match is None:
            if not stop_event.is_set():
                self._log(f'[Macro] "team" not found within {TEAM_PANEL_TIMEOUT:.0f}s after pressing H -- '
                           f'the team panel never opened. Team Loadout {team_num} was NOT applied.')
            return False
        try:
            return self._apply_team_loadout_panel(hwnd, stop_event, team_match, team_num, equipment)
        finally:
            # Every early-return inside the panel flow (scroll landing wrong,
            # Confirm/equipment images never showing up, a checkpoint stop)
            # used to skip this and leave the H panel sitting open -- which
            # then blocked the NEXT Pre Start's camera setup: right-click
            # doesn't lock/hide the cursor while a UI panel like this has
            # focus, so the camera drag's relative nudges moved the real,
            # unlocked cursor instead of rotating the camera, reported as
            # "holding right click on a UI, mouse just moves instead of
            # locking". Closing here unconditionally, however this call
            # exits, means a broken loadout never leaves that trap for the
            # match that follows it.
            self._keyboard.tap(ord("H"))
            self._log("[Macro] Closed the Team Loadout panel.")
            time.sleep(1.5)

    def _apply_team_loadout_panel(self, hwnd, stop_event: threading.Event, team_match, team_num: int,
                                    equipment: str) -> bool:
        # The "team" image proves only that the Unit Manager is open. Clicking
        # it starts a second transition into the actual Load Team list. The
        # old path waited a blind 0.3s and immediately scrolled/clicked; if
        # this click was early or dropped, every later action landed against
        # the wrong screen. Verify the destination's own title and retry THIS
        # click before touching any loadout row.
        loadout_open = None
        for attempt in range(1, TEAM_LOADOUT_OPEN_RETRY_ATTEMPTS + 1):
            if self._checkpoint(stop_event):
                return False
            if attempt > 1:
                self._log(f'[Macro] Load Team list did not open -- retrying the Teams button '
                           f'(attempt {attempt}/{TEAM_LOADOUT_OPEN_RETRY_ATTEMPTS}).')
            vision.click_match(self._mouse, hwnd, team_match)
            self._log("[Macro] Clicked Teams -- waiting for the Load Team list.")
            try:
                loadout_open = vision.wait_for_image(
                    hwnd, "team_loadout_open", threshold=TEAM_LOADOUT_OPEN_THRESHOLD,
                    timeout=TEAM_PANEL_TIMEOUT, stop_event=stop_event)
            except vision.TemplateNotFound as exc:
                self._log(f"[Macro] Can't verify the Load Team list: {exc}")
                return False
            if loadout_open is not None:
                break
            if stop_event.is_set():
                return False
        if loadout_open is None:
            self._log(f'[Macro] Load Team list never opened after '
                      f'{TEAM_LOADOUT_OPEN_RETRY_ATTEMPTS} Teams clicks.')
            self._save_debug_screenshot_unconditional(hwnd, "team_loadout_open_failed")
            return False
        self._log(f'[Macro] Load Team list open (score {loadout_open["score"]:.2f}).')
        # The title arrives before the row animation has completely settled.
        time.sleep(TEAM_LOADOUT_OPEN_SETTLE)
        if self._checkpoint(stop_event):
            return False

        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        row1_x, row1_y = self._cxy("team_loadout")  # Loadout 1's row (Settings > Debug > Macro Coordinates)
        row_x = row1_x + TEAM_LOADOUT_BUTTON_CENTER_X_OFFSET
        if team_num > 3:
            # Dragging from the old (895, 285) point was unreliable for two
            # separate reasons confirmed against the live Windows UI: that
            # point is on the Load Team button, not the scrollbar, and the
            # 300px drag for slots 7/8 leaves the panel before mouse-up. Move
            # over the real scrollbar and send ordinary wheel notches
            # instead. Each notch shifts the rows by a stable 100px.
            scroll_steps = min(team_num, TEAM_LOADOUT_WHEEL_MAX_STEPS)
            hover_x = left + TEAM_LOADOUT_SCROLLBAR_HOVER[0]
            hover_y = top + TEAM_LOADOUT_SCROLLBAR_HOVER[1]
            self._mouse.move_to(hover_x, hover_y)
            time.sleep(TEAM_LOADOUT_SCROLL_HOVER_SETTLE)
            self._mouse.nudge()
            for _ in range(scroll_steps):
                self._mouse.scroll(TEAM_LOADOUT_WHEEL_DELTA)
                time.sleep(TEAM_LOADOUT_WHEEL_INTERVAL)
            time.sleep(TEAM_LOADOUT_SCROLL_SETTLE)
            if self._checkpoint(stop_event):
                return False
            # Current rows are 137px apart. Account for the 100px movement
            # from each wheel notch to click the requested row where it
            # actually landed. Slot 8 bottoms out with only the top of its
            # green button visible, so cap the click at the verified visible
            # part of that button.
            row_y = (
                row1_y
                + (team_num - 1) * TEAM_LOADOUT_CURRENT_ROW_HEIGHT
                - scroll_steps * TEAM_LOADOUT_WHEEL_ROW_SHIFT
            )
            row_y = min(row_y, TEAM_LOADOUT_VISIBLE_BOTTOM_Y)
            self._log(f"[Macro] Scrolled {scroll_steps} notch(es); Loadout {team_num} "
                      f"is at ({row_x}, {row_y}).")
        else:
            row_y = row1_y + (team_num - 1) * int(self._coords["team_loadout_row_height"])

        # Clicking the Loadout row is what actually equips the team --
        # Confirm not showing up afterward used to just skip the rest of
        # this sequence, which silently entered the match with the
        # PREVIOUS match's team (or none) still equipped, a guaranteed
        # loss. Retried here instead (re-clicking the row each attempt, not
        # just re-waiting -- a dropped click is the likely cause, same as
        # every other retried click site in this codebase), and only after
        # every attempt fails does this actually give up.
        confirm_match = None
        for attempt in range(1, TEAM_LOADOUT_CONFIRM_RETRY_ATTEMPTS + 1):
            if self._checkpoint(stop_event):
                return False
            if attempt > 1:
                self._log(f'[Macro] "confirm" didn\'t show up -- retrying Loadout {team_num} '
                           f'(attempt {attempt}/{TEAM_LOADOUT_CONFIRM_RETRY_ATTEMPTS}).')
            self._mouse.click(left + row_x, top + row_y)
            self._log(f"[Macro] Clicked Loadout {team_num}.")
            # Let the Confirm button finish sliding up before locating it --
            # otherwise it's found mid-animation and the click lands where it
            # was, not where it settles (see TEAM_LOADOUT_CONFIRM_SETTLE).
            time.sleep(TEAM_LOADOUT_CONFIRM_SETTLE)
            if self._checkpoint(stop_event):
                return False
            try:
                confirm_match = vision.wait_for_image(
                    hwnd, "confirm", timeout=TEAM_PANEL_TIMEOUT, stop_event=stop_event)
            except vision.TemplateNotFound as exc:
                self._log(f"[Macro] Can't confirm the Loadout Confirm button appeared: {exc}")
                return False
            if confirm_match is not None:
                break
            if stop_event.is_set():
                return False
        if confirm_match is None:
            self._log(f'[Macro] "confirm" never showed up after {TEAM_LOADOUT_CONFIRM_RETRY_ATTEMPTS} attempts -- '
                       f'Team Loadout {team_num} was NOT applied.')
            self._save_debug_screenshot_unconditional(hwnd, "team_loadout_confirm_failed")
            return False
        vision.click_match(self._mouse, hwnd, confirm_match)
        self._log("[Macro] Clicked Confirm.")
        if self._checkpoint(stop_event):
            return False

        # Confirm opens a second, required modal for the configured equipment
        # choice.  Do not silently continue when it cannot be read: that
        # starts the match with a different loadout than the template asked
        # for.  Keep a screenshot of the unread screen so future game-art
        # changes can be diagnosed from the actual failed frame.
        try:
            equip_match = vision.wait_for_image(hwnd, equipment, timeout=TEAM_PANEL_TIMEOUT, stop_event=stop_event)
        except vision.TemplateNotFound as exc:
            equip_match = None
            self._log(f"[Macro] Can't detect the {equipment} equipment option: {exc}")
        if equip_match is not None:
            vision.click_match(self._mouse, hwnd, equip_match)
            self._log(f'[Macro] Equipment: {equipment} (score {equip_match["score"]:.2f}).')
            # Without a settle here, the caller's finally-block H tap (see
            # _apply_team_loadout) fires on the very next line -- pressing H
            # to close the panel before this click has actually registered
            # in-game, so the equipment choice lands inconsistently or not
            # at all and can leave the panel stuck in a half-closed state.
            time.sleep(0.5)
        elif not stop_event.is_set():
            self._log(f'[Macro] "{equipment}" option never showed up -- Team Loadout '
                      f'{team_num} was not fully applied.')
            self._save_debug_screenshot_unconditional(hwnd, f"team_loadout_{equipment}_failed")
            return False
        return equip_match is not None











    def _wait_teleport_in(self, hwnd, stop_event: threading.Event, webhook: dict = None,
                            task: dict = None, timeout: float = None) -> bool:
        # nav_unitmanager only renders once you're actually in the match (not
        # during the loading/teleport transition), so waiting for it is the
        # confirmation the teleport actually finished.
        timeout = TELEPORT_IN_TIMEOUT if timeout is None else timeout
        self._log(f'[Macro] Waiting to teleport in-game (watching for "nav_unitmanager", up to '
                   f'{timeout:.0f}s)...')
        self._set_status(action='Waiting to teleport in-game ("nav_unitmanager")...')
        result = self._wait_for_teleport_or_stuck(hwnd, stop_event, timeout)
        if result == "ok":
            self._log("[Macro] Teleported in-game.")
            return True
        if result in ("stuck", "disconnected"):
            self._handle_disconnect(hwnd, stop_event, webhook, task, result)
            return False
        if result == "timeout" and not stop_event.is_set():
            self._log(f'[Macro] "nav_unitmanager" not found within {timeout:.0f}s -- never teleported '
                       f'in-game (or the Unit Manager button isn\'t matching your setup -- if you\'re '
                       f'visibly in the match, add your own crop of it via Settings > General > '
                       f'Image Manager). Stopping.')
        return False

    def _wait_for_teleport_or_stuck(self, hwnd, stop_event: threading.Event, timeout: float) -> str:
        """Polls for nav_unitmanager (teleport-in confirmed), Roblox's own
        Reconnect/Retry prompt (a definite disconnect, no continuous-
        visibility wait needed), and teleportstuck (a hung loading screen,
        which CAN be a momentary false alarm so it only counts once it's
        been continuously visible for TELEPORT_STUCK_TIMEOUT) side by side --
        a stuck/disconnected teleport never resolves into either success or
        a clean "gone" the way other timeouts do, it just sits there
        forever, so this is the only way to tell "still loading, be
        patient" apart from "actually broken, needs a rejoin". Returns
        "ok", "disconnected", "stuck", "stopped", or "timeout". Both
        reconnect/retry and teleportstuck are optional -- a missing crop
        just disables that half of the check, same as any other best-effort
        image search in this file."""
        deadline = time.time() + timeout
        stuck_since = None
        stuck_template_missing = False
        while time.time() < deadline:
            if stop_event.is_set():
                return "stopped"
            try:
                match = vision.find_image(hwnd, "nav_unitmanager")
            except vision.TemplateNotFound as exc:
                self._log(f"[Macro] Can't confirm teleport-in: {exc}")
                return "timeout"
            if match is not None:
                return "ok"

            for name in RECONNECT_IMAGE_NAMES:
                try:
                    reconnect_match = vision.find_image(hwnd, name)
                except vision.TemplateNotFound:
                    continue  # that particular crop hasn't been added -- try the next one
                if reconnect_match is not None:
                    return "disconnected"

            if not stuck_template_missing:
                try:
                    stuck_match = vision.find_image(hwnd, "teleportstuck")
                except vision.TemplateNotFound:
                    stuck_match = None
                    stuck_template_missing = True  # don't keep re-searching for a crop that was never added
                if stuck_match is not None:
                    if stuck_since is None:
                        stuck_since = time.time()
                    elif time.time() - stuck_since >= TELEPORT_STUCK_TIMEOUT:
                        return "stuck"
                else:
                    stuck_since = None  # only counts while CONTINUOUSLY visible

            time.sleep(TELEPORT_POLL_INTERVAL)
        return "timeout"

    def _handle_disconnect(self, hwnd, stop_event: threading.Event, webhook: dict, task: dict,
                             reason: str) -> None:
        """A stuck/disconnected teleport is unrecoverable by waiting longer
        or retrying a click -- only an actual rejoin fixes it. Logs the
        disconnect to Discord (if configured) and attempts one, updating
        self._current_hwnd on success so the next task-setup retry (see
        _run_task's recovery loop, which re-reads self._current_hwnd) picks
        up wherever the game ended up re-docked. Always returns None --
        callers treat this attempt as failed either way and let the normal
        task-recovery loop decide whether to retry."""
        why = "Roblox's own Reconnect/Retry prompt appeared" if reason == "disconnected" \
            else f"the teleport was stuck for over {TELEPORT_STUCK_TIMEOUT:.0f}s"
        self._log(f"[Macro] Disconnected from Roblox ({why}) -- attempting to rejoin.")
        screenshot_path = self._save_debug_screenshot_unconditional(hwnd, "teleport_disconnected")
        self._send_event_webhook(webhook, task, "Disconnected -- Rejoining",
                                   f"{why.capitalize()}. Attempting to rejoin via deep link.",
                                   0xE8935A, screenshot_path)
        self._attempt_rejoin(hwnd, stop_event)

    def _send_event_webhook(self, webhook: dict, task: dict, title: str, description: str, color: int,
                              screenshot_path: str = None, extra_fields: list = None) -> None:
        """Shared by every "worth a Discord ping" runner event that ISN'T a
        Victory/Defeat result (see _send_result_webhook for that one) --
        disconnects, a Restart Game, a Start Game click that never actually
        registered, a task finally giving up after every recovery attempt.
        Reuses the same task-result webhook config (url/enabled/mention/
        silent) rather than a second separate webhook, and attaches a
        screenshot via webhook.send_file when one's given so these read as
        "here's what was actually on screen", not just a line of text."""
        url = (webhook or {}).get("url")
        if not url or not webhook.get("enabled"):
            return
        from . import webhook as webhook_module
        fields = [{"name": "Map", "value": (task or {}).get("map") or "-", "inline": True}]
        if extra_fields:
            fields.extend(extra_fields)
        embed = {
            "title": title,
            "description": description,
            "color": color,
            "fields": fields,
            "footer": {"text": "Cream's Macro | Anime Expeditions"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        mention_id = (webhook or {}).get("mention_id")
        content = f"<@{mention_id}>" if mention_id else ""
        silent = bool(webhook.get("silent"))
        try:
            if screenshot_path:
                result = webhook_module.send_file(url, embed, screenshot_path, content=content, silent=silent)
            else:
                result = webhook_module.send(url, embed, content=content, silent=silent)
            if not result.get("ok"):
                self._log(f'[Macro] "{title}" webhook send failed: {result.get("reason")}')
        except Exception as exc:
            self._log(f'[Macro] "{title}" webhook send failed: {exc}')

    def _attempt_rejoin(self, hwnd, stop_event: threading.Event) -> bool:
        """Launches the Roblox deep link and waits for the lobby (nav_play)
        to come back, polling self._hwnd_getter() rather than trusting the
        original hwnd -- if Roblox had fully closed, the deep link spawns a
        brand new process/window, and main.py's dock watchdog re-docks it
        under a NEW hwnd on its own; this is how a rejoin picks that up
        instead of continuing to poll a dead window handle. Updates
        self._current_hwnd on success. Returns whether the lobby was
        actually reached again."""
        # A deep-link launch invokes Roblox's OWN single-instance handling,
        # which force-closes every OTHER open Roblox window down to just
        # the newly launched one -- fine (even desired) on a single-
        # instance setup, but it was silently taking out someone's other
        # accounts/windows on a multi-instance one, which is a much worse
        # outcome than just failing this one rejoin attempt. Only attempt
        # it when there's nothing else around it could take down (the
        # window that actually disconnected doesn't count here -- it's
        # still docked/hidden at this point, not a standalone window
        # list_roblox_windows would even see).
        try:
            other_windows = wm.list_roblox_windows()
        except Exception:
            other_windows = []
        # In cutout mode (e.g. use_wgc_capture) our OWN Roblox stays a
        # top-level window, so it shows up in list_roblox_windows and this
        # guard tripped on our own game -- a rejoin would then never fire.
        # Exclude the window we're farming (list_roblox_windows returns dicts)
        # so only GENUINELY other Roblox windows (alt accounts) block a rejoin.
        # Harmless in the normal child-reparent dock, where our own window
        # isn't top-level and wouldn't be listed here anyway.
        try:
            _cur = self._hwnd_getter() if self._hwnd_getter else hwnd
        except Exception:
            _cur = hwnd
        other_windows = [w for w in other_windows
                         if (w.get("hwnd") if isinstance(w, dict) else w) not in (hwnd, _cur)]
        if other_windows:
            self._log("[Macro] Not attempting a deep-link rejoin -- other Roblox windows are open and it "
                       "would close them. Stopping instead.")
            return False

        self._set_status(action="Disconnected -- rejoining...")
        try:
            os.startfile(REJOIN_DEEPLINK)
        except OSError as exc:
            self._log(f"[Macro] Couldn't launch the rejoin link: {exc}")
            return False
        self._log("[Macro] Rejoin link launched -- waiting for the game to load back in...")

        deadline = time.time() + REJOIN_TIMEOUT
        while time.time() < deadline:
            if stop_event.is_set():
                return False
            time.sleep(REJOIN_POLL_INTERVAL)
            current_hwnd = self._hwnd_getter() if self._hwnd_getter else hwnd
            if not current_hwnd or not wm.is_window(current_hwnd):
                continue
            try:
                match, _ = vision.find_image_any(current_hwnd, NAV_PLAY_IMAGE_NAMES)
            except vision.TemplateNotFound:
                match = None
            if match is not None:
                self._log("[Macro] Rejoined -- back on the lobby.")
                self._current_hwnd = current_hwnd
                return True
        self._log(f"[Macro] Rejoin didn't reach the lobby within {REJOIN_TIMEOUT:.0f}s -- giving up.")
        return False

    def _click_start_and_wait_teleport(self, hwnd, stop_event: threading.Event, webhook: dict = None,
                                          task: dict = None) -> bool:
        """Solo mode's Start click used to fire once, and if it didn't
        actually register in-game (a frame too early, an overlay in the way)
        the run just sat there until the teleport wait ran out with nothing
        to show for it. Retrying the CLICK itself -- not just waiting longer
        -- is what actually recovers from that.

        The bug this fixes: after a successful click, nav_start legitimately
        disappears from screen (you're on your way to the loading/teleport
        screen) -- treating "can't find nav_start anymore" as a failed
        attempt and giving up after SOLO_START_RETRY_ATTEMPTS was punishing
        a teleport that was just SLOW, not one that never started. Only
        re-click when nav_start is still actually visible (the click really
        didn't register); once it's gone, that's success -- keep waiting for
        nav_unitmanager instead of trying to click a button that isn't there.
        """
        clicked = False
        for _ in range(1, SOLO_START_RETRY_ATTEMPTS + 1):
            if self._checkpoint(stop_event):
                return False

            # wait_for_image, not a bare one-shot find_image: right after
            # nav_select_stage is clicked, this screen is still mid-
            # transition for a moment, and a single check timed unluckily
            # can miss a button that's genuinely there a beat later. Only on
            # a LATER attempt (clicked already) is a quick poll actually
            # useful signal about whether it's really gone.
            try:
                start_match, start_name = vision.wait_for_image_any(
                    hwnd, NAV_START_IMAGE_NAMES, timeout=SOLO_START_TIMEOUT, stop_event=stop_event)
            except vision.TemplateNotFound as exc:
                self._log(f"[Macro] {exc}")
                return False
            if self._checkpoint(stop_event):
                return False
            if start_match is not None:
                debug_path = self._debug_save(hwnd, start_name, start_match)
                suffix = f" Debug: {debug_path}" if debug_path else ""
                self._log(f'[Macro] Found "{start_name}" (score {start_match["score"]:.2f}) -- clicking it.{suffix}')
                self._set_status(action="Clicking Start...")
                vision.click_match(self._mouse, hwnd, start_match)
                clicked = True
            elif not clicked:
                # Never managed to click it even once, and it's already
                # gone -- this is the wrong screen entirely, not a slow
                # teleport, so there's nothing to keep waiting on.
                self._log(f'[Macro] "nav_start" not found within {SOLO_START_TIMEOUT:.0f}s -- the Start '
                           f'button never showed up (if it\'s visibly on screen, add your own crop of it '
                           f'via Settings > General > Image Manager). Stopping.')
                return False
            else:
                self._log("[Macro] Start already clicked -- still teleporting, waiting longer.")
            if self._checkpoint(stop_event):
                return False

            self._log(f'[Macro] Waiting to teleport in-game (watching for "nav_unitmanager", up to '
                       f'{SOLO_TELEPORT_PER_ATTEMPT_TIMEOUT:.0f}s)...')
            self._set_status(action='Waiting to teleport in-game ("nav_unitmanager")...')
            result = self._wait_for_teleport_or_stuck(hwnd, stop_event, SOLO_TELEPORT_PER_ATTEMPT_TIMEOUT)
            if result == "ok":
                self._log("[Macro] Teleported in-game.")
                return True
            if result in ("stuck", "disconnected"):
                # Broken, not slow -- re-clicking Start or waiting through
                # more attempts won't fix a hung/disconnected server, so this
                # bails immediately instead of burning the rest of the retry
                # budget on something a rejoin (not a click) actually fixes.
                self._handle_disconnect(hwnd, stop_event, webhook, task, result)
                return False
            if stop_event.is_set():
                return False
            self._log("[Macro] Didn't teleport yet -- checking again.")

        self._log(f'[Macro] "nav_unitmanager" never matched across {SOLO_START_RETRY_ATTEMPTS} Start '
                   f'attempts -- never teleported in-game, stopping.')
        return False

    def _click_found_image(self, hwnd, name: str, timeout: float, stop_event: threading.Event = None) -> dict:
        """Shared wait-for-it-then-click for a plain nav button (nav_settings,
        nav_search, ...) -- no per-button quirks like Story/Play have, so one
        helper covers all of them instead of a bespoke method each.

        Returns the match dict (truthy) on success or None (falsy) on
        failure -- existing `if not self._click_found_image(...)` call sites
        work unchanged either way, but callers that need the click position
        afterward (see _start_game_or_reset_via_settings saving nav_search's
        spot to reuse) can read it straight off the returned match instead of
        re-searching for the same button a second time.
        """
        try:
            match = vision.wait_for_image(hwnd, name, timeout=timeout, stop_event=stop_event)
        except vision.TemplateNotFound as exc:
            self._log(f"[Macro] {exc}")
            return None
        if match is None:
            if stop_event is None or not stop_event.is_set():
                self._log(f'[Macro] "{name}" not found within {timeout:.0f}s -- stopping.')
            return None
        debug_path = self._debug_save(hwnd, name, match)
        suffix = f" Debug: {debug_path}" if debug_path else ""
        self._log(f'[Macro] Found "{name}" (score {match["score"]:.2f}) -- clicking it.{suffix}')
        vision.click_match(self._mouse, hwnd, match)
        return match

    def _click_and_verify_gone(self, hwnd, stop_event: threading.Event, name: str, timeout: float,
                                 retry_attempts: int = 3, verify_settle: float = 1.0,
                                 success_name: str = None) -> bool:
        """Like _click_found_image, but re-checks the button actually
        disappeared afterward and re-clicks (with a focus reassert) if it's
        still there, up to retry_attempts times -- same "click found it but
        Roblox never got it" flakiness the Play/Start Game clicks needed
        this same treatment for. Used for Leave Stage/Repeat Stage: a
        dropped click here leaves the whole run just sitting on the result
        screen indefinitely, since nothing else would ever notice or retry
        on its own. ``success_name`` handles transitions where the original
        image remains visible behind the next modal: Leave Stage is still
        visible when Return to Lobby appears, so seeing ``return`` is also
        proof that the click registered.

        Returns whether the button was found at all -- not whether it
        definitely disappeared, since after retry_attempts a stuck button
        falls through to the caller's own recovery path rather than being
        treated as "never found in the first place"."""
        match = None
        for attempt in range(1, retry_attempts + 1):
            try:
                match = vision.wait_for_image(hwnd, name, timeout=timeout, stop_event=stop_event)
            except vision.TemplateNotFound as exc:
                self._log(f"[Macro] {exc}")
                return False
            if match is None:
                if stop_event is None or not stop_event.is_set():
                    self._log(f'[Macro] "{name}" not found within {timeout:.0f}s -- stopping.')
                return False

            debug_path = self._debug_save(hwnd, name, match)
            suffix = f" Debug: {debug_path}" if debug_path else ""
            self._log(f'[Macro] Found "{name}" (score {match["score"]:.2f}) -- clicking it '
                       f'(attempt {attempt}/{retry_attempts}).{suffix}')
            if not wm.activate_window(hwnd):
                self._log(f"[Macro] Couldn't confirm focus before clicking \"{name}\" -- click may not register.")
            vision.click_match(self._mouse, hwnd, match)
            time.sleep(verify_settle)
            if self._checkpoint(stop_event):
                return True

            if success_name is not None:
                try:
                    transitioned = vision.find_image(hwnd, success_name)
                except vision.TemplateNotFound:
                    transitioned = None
                if transitioned is not None:
                    return True

            try:
                still_there = vision.find_image(hwnd, name)
            except vision.TemplateNotFound:
                still_there = None
            if still_there is None:
                return True
            if attempt == retry_attempts:
                self._log(f'[Macro] "{name}" still showing after {retry_attempts} clicks -- continuing anyway.')
        return True

    def _wait_for_image_gone(self, hwnd, names, timeout: float, stop_event: threading.Event = None) -> bool:
        """Polls until NONE of `names` are found anymore, or timeout runs
        out. No clicking -- purely a "has this actually disappeared yet"
        wait, for confirming a modal/banner is really gone rather than just
        assuming it is because some OTHER click nearby reported success
        (see _handle_match_result's Repeat Stage path, where the button
        image itself disappearing turned out not to mean the whole result
        panel had). Best-effort: a missing template is silently treated as
        already-gone (same as any optional check in this file), and running
        out the timeout just returns False rather than raising -- callers
        treat this as a settle wait, not a hard requirement."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if stop_event is not None and stop_event.is_set():
                return False
            still_showing = False
            for name in names:
                try:
                    match = vision.find_image(hwnd, name)
                except vision.TemplateNotFound:
                    continue
                if match is not None:
                    still_showing = True
                    break
            if not still_showing:
                return True
            time.sleep(0.3)
        self._log(f'[Macro] {"/".join(names)} still showing after {timeout:.0f}s -- continuing anyway.')
        return False

    def _click_start_game_2_if_found(self, hwnd) -> bool:
        # nav_start_game_confirm (was "nav_start_game_2" -- renamed because
        # the _2 suffix made it look like a mere visual variant of
        # nav_start_game, which it is NOT): a second Start Game/confirm
        # button that can show up alongside the warning itself (e.g. a
        # "Start Anyway" prompt) -- clicking it skips the wait in
        # _wait_out_start_game_warning entirely instead of sitting through
        # the full timeout for a warning that's actually already
        # dismissable right now.
        try:
            skip_match = vision.find_image(hwnd, "nav_start_game_confirm")
        except vision.TemplateNotFound:
            return False
        if skip_match is None:
            return False
        self._log(f"[Macro] Found nav_start_game_confirm (score {skip_match['score']:.2f}) -- "
                   f"clicking it to skip the warning wait.")
        vision.click_match(self._mouse, hwnd, skip_match)
        return True

    def _find_start_game_button(self, hwnd, stop_event: threading.Event = None, timeout: float = 0):
        """Tries nav_start_game (whose folder holds every visual variant of
        the ready-up button seen in practice -- the old separately-named
        _3/_4 crops live in there now, all tried automatically per search),
        then nav_start_game_confirm (a DIFFERENT button -- the "Start
        Anyway"-style second confirm, see _click_start_game_2_if_found) --
        so the actual "start the round" click (see _play_one_match) isn't
        dependent on just one image matching. Returns (name, match) for
        whichever was found first, or (None, None) if none of them were --
        missing/not-yet-added templates are skipped silently, same as any
        other optional template.

        timeout=0 (the default) is a single instant pass -- used right
        after a click to check it's gone, where waiting around would just
        slow the retry loop down. Pass a real timeout for the FIRST check
        (right as Pre Start hands off), since the button can still be
        animating in at that exact moment and a one-shot check there was
        landing before it existed at all, especially on Expedition where
        Pre Start's place_unit clicks run right up until this point."""
        deadline = time.time() + max(0.0, timeout)
        while True:
            for name in ("nav_start_game", "nav_start_game_confirm"):
                try:
                    match = vision.find_image(hwnd, name)
                except vision.TemplateNotFound:
                    continue
                if match is not None:
                    return name, match
            if time.time() >= deadline or (stop_event is not None and stop_event.is_set()):
                return None, None
            time.sleep(0.3)

    def _wait_out_start_game_warning(self, hwnd, stop_event: threading.Event) -> None:
        """Best-effort, like nav_disband: a warning popup (e.g. an
        incomplete-setup warning) can sit in front of Start Game right
        after Pre Start finishes -- if one's up, wait for it to clear (and
        Start Game to actually show up) instead of immediately treating a
        missing nav_start_game as "already started" and moving on. A
        missing warning.png just skips this check entirely, same as any
        other optional template."""
        try:
            warning_match = vision.find_image(hwnd, "warning")
        except vision.TemplateNotFound:
            return
        if warning_match is None:
            return

        self._log(f"[Macro] Found a warning (score {warning_match['score']:.2f}) -- checking for a way past it.")
        if self._click_start_game_2_if_found(hwnd):
            return

        self._log(f"[Macro] Waiting up to {WARNING_WAIT_TIMEOUT:.0f}s for it to clear.")
        self._set_status(action="Waiting for warning to clear...")
        deadline = time.time() + WARNING_WAIT_TIMEOUT
        while time.time() < deadline:
            if self._checkpoint(stop_event):
                return
            if self._click_start_game_2_if_found(hwnd):
                return
            try:
                warning_gone = vision.find_image(hwnd, "warning") is None
            except vision.TemplateNotFound:
                warning_gone = True
            try:
                start_visible = vision.find_image(hwnd, "nav_start_game") is not None
            except vision.TemplateNotFound:
                start_visible = False
            if warning_gone and start_visible:
                self._log("[Macro] Warning cleared -- Start Game is up.")
                return
            time.sleep(WARNING_POLL_INTERVAL)
        self._log(f'[Macro] "warning" still showing (or "nav_start_game" still not found) after '
                   f'{WARNING_WAIT_TIMEOUT:.0f}s -- continuing anyway.')

    def _open_settings_search(self, hwnd, stop_event: threading.Event):
        """Opens Settings and clicks its search box. Returns the search
        box's screen position (reusable for as many searches as needed --
        the box itself never moves once found, so later callers don't need
        a second image search against a panel that's since reflowed around
        whatever the first search changed), or None if Settings/the search
        box couldn't be found at all."""
        if not self._click_found_image(hwnd, "nav_settings", NAV_CLICK_TIMEOUT, stop_event):
            return None
        if self._checkpoint(stop_event):
            return None
        # The Settings panel opens with a scale/slide-in animation -- without
        # this, nav_search can get matched (even at a perfect 1.00 score)
        # against a mid-animation frame, whose search box isn't at its final
        # settled position yet. The click then lands wherever that transient
        # frame put it instead of where the box actually ends up, missing it
        # entirely. SETTLE_DELAY is comfortably longer than the animation.
        time.sleep(SETTLE_DELAY)
        search_match = self._click_found_image(hwnd, "nav_search", NAV_CLICK_TIMEOUT, stop_event)
        if not search_match:
            return None
        if self._checkpoint(stop_event):
            return None
        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        return (left + search_match["cx"], top + search_match["cy"])

    def _search_and_set_toggle(self, hwnd, stop_event: threading.Event, search_box_pos, setting_name: str,
                                 desired_on: bool) -> bool:
        """Types setting_name into an already-open Settings search box
        (see _open_settings_search) and clicks its toggle if it isn't
        already in the desired on/off state. Shared by the Auto Vote Start
        handling below and any Setting block of "toggle" kind (see
        _run_setting_block) -- same search-and-toggle mechanic either way,
        just a different setting name/desired state. Returns whether the
        setting's toggle was found at all (not whether a click happened --
        already being in the right state is also success)."""
        self._set_status(action=f'Searching settings for "{setting_name}"...')
        self._log(f'[Macro] Typing "{setting_name}"...')
        self._mouse.click(*search_box_pos)
        time.sleep(0.2)  # let the search field actually take focus before typing
        self._keyboard.combo(keys.VK_CONTROL, ord("A"))  # select any existing search text...
        self._keyboard.tap(keys.VK_DELETE)                # ...and clear it before typing this search
        self._keyboard.type_text(setting_name)
        time.sleep(SETTLE_DELAY)  # let the filtered settings list render before reading its toggle
        if self._checkpoint(stop_event):
            return False

        try:
            on_match = vision.find_image(hwnd, "toggle_true")
        except vision.TemplateNotFound as exc:
            self._log(f"[Macro] {exc}")
            return False
        is_on = on_match is not None

        if is_on == desired_on:
            self._log(f'[Macro] "{setting_name}" already {"on" if desired_on else "off"} -- nothing to click.')
            return True

        # Not in the desired state -- toggle_true/toggle_false share the
        # same click target either way (clicking the switch flips it), so
        # whichever of the two actually matched is what gets clicked.
        toggle_match = on_match
        if toggle_match is None:
            try:
                toggle_match = vision.find_image(hwnd, "toggle_false")
            except vision.TemplateNotFound as exc:
                self._log(f"[Macro] {exc}")
                return False
        if toggle_match is None:
            self._log(f'[Macro] "{setting_name}" not found in Settings -- skipping.')
            return False
        debug_path = self._debug_save(hwnd, "toggle_true" if is_on else "toggle_false", toggle_match)
        suffix = f" Debug: {debug_path}" if debug_path else ""
        self._log(f'[Macro] "{setting_name}" is {"on" if is_on else "off"} -- '
                   f'turning it {"off" if is_on else "on"}.{suffix}')
        vision.click_match(self._mouse, hwnd, toggle_match)
        return True

    def _close_settings_if_open(self, hwnd, stop_event: threading.Event) -> None:
        """One-shot cleanup shared by anything that opened Settings via
        _open_settings_search and is done with it -- a look, not a wait,
        since not finding it open is success too (some flows, like a
        Restart Game confirm, can already close Settings on their own)."""
        time.sleep(SETTLE_DELAY)
        if self._checkpoint(stop_event):
            return
        try:
            settings_match = vision.find_image(hwnd, "nav_settings_on")
        except vision.TemplateNotFound as exc:
            self._log(f"[Macro] {exc}")
            return
        if settings_match is None:
            return
        debug_path = self._debug_save(hwnd, "nav_settings_on", settings_match)
        suffix = f" Debug: {debug_path}" if debug_path else ""
        self._log(f"[Macro] Closing Settings.{suffix}")
        vision.click_match(self._mouse, hwnd, settings_match)

    def _start_game_or_reset_via_settings(self, hwnd, stop_event: threading.Event, play_mode: str = "solo") -> bool:
        # Party leadership and Auto Vote Start are both matchmaking-only
        # concepts -- Solo mode has no party at all, so there's no leader
        # to check for and nothing to reset via Settings. This used to run
        # unconditionally for solo too, which is exactly what produced the
        # confusing "No Start Game button (not the party leader)" log on
        # every solo run: there was never a party to be leader of, so the
        # check always "failed" and fell through to the Auto Vote Start
        # search, a setting that's entirely irrelevant to a solo run.
        if play_mode != "matchmaking":
            return True

        # Start Game only exists for the party leader -- a quick presence
        # check, not a long wait (see START_GAME_CHECK_TIMEOUT), decides
        # which of the two very different paths to take. Either way, this is
        # a LEADERSHIP check, not the actual "begin the round" click: Start
        # Game must not be pressed until Pre Start (camera, walk, unit
        # placement) has actually run -- pressing it here, this early, was
        # starting the round before any of that had a chance to happen.
        # The real click lives at the end of _run, once, after Pre Start
        # finishes, for leader and non-leader alike.
        self._set_status(action="Checking party leadership...")
        try:
            match = vision.wait_for_image(hwnd, "nav_start_game", timeout=START_GAME_CHECK_TIMEOUT, stop_event=stop_event)
        except vision.TemplateNotFound as exc:
            self._log(f"[Macro] {exc}")
            return False
        if stop_event.is_set():
            return False
        if match is not None:
            self._log(f"[Macro] Found Start Game (score {match['score']:.2f}) -- you're the party leader. "
                       f"Running Pre Start before pressing it.")
            return True

        # No Start Game button just means Auto Vote Start is on -- most
        # people run with it on deliberately (it's a legitimate feature,
        # not a misconfiguration), so this no longer fights it by disabling
        # it and restarting the game. The vote-started round teleports in
        # on its own; _wait_teleport_in downstream is what actually confirms
        # that happened, this just needs to not treat its absence as a
        # failure.
        self._log('[Macro] No Start Game button found -- Auto Vote Start is likely on, letting it '
                   "auto-start the round instead of disabling it.")
        return True

    def _cxy(self, prefix: str) -> tuple:
        """(x, y) for a Macro Coordinates point -- self._coords holds the
        user's saved overrides (or DEFAULT_COORDS before a run starts), so
        every click site that reads through here is re-tunable from
        Settings > Debug without a code change."""
        return int(self._coords[f"{prefix}_x"]), int(self._coords[f"{prefix}_y"])

    def _select_difficulty(self, hwnd, difficulty: str, coords: dict) -> None:
        # Fixed spot on the stage-detail panel, same as the stage rows --
        # no image search needed, just like Story's click was.
        key_prefix = "difficulty_hard" if difficulty == "Hard" else "difficulty_normal"
        x, y = coords[f"{key_prefix}_x"], coords[f"{key_prefix}_y"]
        self._log(f'[Macro] Clicking difficulty "{difficulty}" at ({x}, {y}).')
        self._set_status(action=f'Clicking difficulty "{difficulty}"...')
        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        self._mouse.click(left + x, top + y)


    def _click_enter_matchmaking(self, hwnd, stop_event: threading.Event, coords: dict, mode: str = None) -> bool:
        # Expedition's and Challenge's matchmaking buttons are each their
        # own image (exp_enter_matchmaking / chal_enter) at an uncalibrated
        # position -- no matchmaking_region_* exists for either, so they're
        # searched full-window instead of the Story/Raid region restriction.
        image_name = {"expedition": "exp_enter_matchmaking", "challenge": "chal_enter"}.get(mode, "enter_matchmaking")
        # Event reuses Story/Raid's shared "enter_matchmaking" image, but its
        # button isn't at the calibrated Story/Raid position (matchmaking_
        # region_*) -- so it's searched full-window, same as Expedition/
        # Challenge, rather than boxed to a region it may not land in.
        region = None if mode in ("expedition", "challenge", "event") else (
            coords["matchmaking_region_x"], coords["matchmaking_region_y"],
            coords["matchmaking_region_w"], coords["matchmaking_region_h"],
        )
        self._log(f'[Macro] Waiting for Enter Matchmaking (searching "{image_name}", up to '
                   f'{MATCHMAKING_WAIT_TIMEOUT:.0f}s)...')
        self._set_status(action=f'Waiting for Enter Matchmaking ("{image_name}")...')
        try:
            match = vision.wait_for_image(
                hwnd, image_name, region=region,
                timeout=MATCHMAKING_WAIT_TIMEOUT, stop_event=stop_event)
        except vision.TemplateNotFound as exc:
            self._log(f"[Macro] Can't find Enter Matchmaking: {exc}")
            return False
        if match is None:
            if not stop_event.is_set():
                self._log(f'[Macro] "{image_name}" not found within {MATCHMAKING_WAIT_TIMEOUT:.0f}s -- the '
                           f'Enter Matchmaking button never showed up (if it\'s visibly on screen, add your '
                           f'own crop of it via Settings > General > Image Manager). Stopping.')
            return False
        debug_path = self._debug_save(hwnd, image_name, match)
        suffix = f" Debug: {debug_path}" if debug_path else ""
        self._log(f"[Macro] Found Enter Matchmaking (score {match['score']:.2f}) -- clicking it.{suffix}")
        vision.click_match(self._mouse, hwnd, match)
        return True

    def _select_stage(self, hwnd, stop_event: threading.Event, stage: str, mode: str) -> bool:
        # Raid's screen is the same nav_select_stage screen as Story's, just
        # with 3 Act rows spaced differently instead of the 7 stage rows
        # (see ACT_ORDER/ACT_CLICK_BASE/ACT_ROW_HEIGHT).
        order, base, row_height, label = (
            (ACT_ORDER, self._cxy("act_row"), int(self._coords["act_row_height"]), "Act") if mode == "raid"
            else (STAGE_ORDER, self._cxy("stage_row"), int(self._coords["stage_row_height"]), "stage"))
        if stage not in order:
            self._log(f'[Macro] Unknown {label} "{stage}" -- expected one of {order}.')
            return False

        self._log("[Macro] Waiting for the stage select screen...")
        self._set_status(action="Waiting for stage screen...")
        try:
            match = vision.wait_for_image(
                hwnd, "nav_select_stage", timeout=STAGE_SCREEN_TIMEOUT, stop_event=stop_event)
        except vision.TemplateNotFound as exc:
            self._log(f"[Macro] Can't confirm the stage screen opened: {exc}")
            return False
        if match is None:
            if not stop_event.is_set():
                self._log(f'[Macro] "nav_select_stage" not found within {STAGE_SCREEN_TIMEOUT:.0f}s -- the '
                           f'stage select screen never opened (if it\'s visibly open, add your own crop of '
                           f'the Select Stage button via Settings > General > Image Manager). Stopping.')
            return False

        # nav_select_stage confirms the CONFIRM BUTTON is on screen, not
        # that the stage/Act row list above it has finished rendering that
        # map's own rows yet -- clicking a computed row position immediately
        # used to be able to catch the list still mid-transition (e.g. a
        # moment of the PREVIOUS map's rows still visible/animating out),
        # landing on the wrong stage entirely (reported: Mastery selected,
        # but a regular numbered stage started instead).
        time.sleep(SETTLE_DELAY)

        idx = order.index(stage)
        x = base[0]
        y = base[1] + idx * row_height
        self._log(f'[Macro] Stage screen open -- double-clicking {label} "{stage}" at ({x}, {y}).')
        self._set_status(action=f'Clicking {label} "{stage}"...')
        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        # A single click was sometimes not registering as the row's own
        # "selected" state (confirmed from real reports on both Story and
        # Raid) -- a double-click reliably does. The stage-detail panel
        # (Story's difficulty toggle, or straight to the Select Stage
        # confirm button for Raid/Infinite/Mastery, which have no
        # difficulty toggle at all) then animates in, same as the map click
        # before it -- settled here UNCONDITIONALLY, not just on the
        # difficulty-click path, since Raid/locked stages used to go
        # straight from this click into Select Stage with no wait at all.
        self._mouse.double_click(left + x, top + y)
        time.sleep(DIFFICULTY_CLICK_DELAY)
        return True

    def _ensure_lobby(self, hwnd, stop_event: threading.Event) -> bool:
        # "On the lobby" is inferred from the Play button actually being
        # visible in its known Nav spot -- it only renders there outside of
        # a match, so finding it there IS the lobby check, not a separate
        # step before it.
        self._log("[Macro] Checking you're on the lobby...")
        self._set_status(action="Checking lobby...")
        try:
            match, _ = vision.wait_for_image_any(
                hwnd, NAV_PLAY_IMAGE_NAMES,
                timeout=LOBBY_CHECK_TIMEOUT, stop_event=stop_event)
        except vision.TemplateNotFound as exc:
            self._log(f"[Macro] Can't check the lobby: {exc}")
            return False
        if match is not None:
            return True
        if stop_event.is_set():
            return False
        # No Play button after a full LOBBY_CHECK_TIMEOUT wait looks exactly
        # like a silent disconnect that never even triggered Roblox's own
        # Reconnect/Retry prompt (see _handle_disconnect) -- rather than
        # just give up here, try the same deep-link rejoin a detected
        # disconnect already uses instead of stopping the whole run over it.
        # (_attempt_rejoin itself skips this on a multi-instance setup --
        # see its own comment.)
        self._log(f'[Macro] "nav_play" not found within {LOBBY_CHECK_TIMEOUT:.0f}s -- not on the lobby '
                   f'(likely a silent disconnect), attempting a rejoin via deep link.')
        return self._attempt_rejoin(hwnd, stop_event)

    def _click_play(self, hwnd, stop_event: threading.Event) -> bool:
        self._set_status(action="Clicking Play...")
        try:
            match, name = vision.find_image_any(hwnd, NAV_PLAY_IMAGE_NAMES)
        except vision.TemplateNotFound as exc:
            self._log(f"[Macro] {exc}")
            return False
        if match is None:
            self._log('[Macro] "nav_play" vanished before it could be clicked -- stopping.')
            return False
        debug_path = self._debug_save(hwnd, name, match)
        suffix = f" Debug: {debug_path}" if debug_path else ""
        self._log(f"[Macro] Found Play (score {match['score']:.2f}) -- clicking it.{suffix}")
        # Reasserted right here, not just once at the top of the run --
        # SendInput clicks go to whatever window has REAL OS focus, and
        # this is the very first live click after a screen transition
        # (lobby just loaded), the exact moment focus is most likely to
        # not have actually settled yet. Reported by multiple users as
        # "it finds Play correctly, the click just doesn't register."
        if not wm.activate_window(hwnd):
            self._log("[Macro] Couldn't confirm focus before clicking Play -- click may not register.")
        time.sleep(0.1)
        vision.click_match(self._mouse, hwnd, match)
        return True

    def _reach_map_selected(self, hwnd, stop_event: threading.Event, map_name: str, mode: str,
                              scroll_power: int, scroll_nudges: int) -> bool:
        """Lobby -> Play -> Story/Raid -> map search, as one restartable unit --
        called in a loop by _run (see MAP_SELECT_RETRY_ATTEMPTS). Each call
        re-checks from scratch (including the "already on the gamemode
        menu" shortcut) rather than assuming any state left over from a
        previous failed attempt, since _spam_back_until_gone may have just
        backed all the way out to the lobby.
        """
        # If the gamemode menu (Story/Raid/...) is ALREADY open -- e.g. a
        # previous attempt got this far and backed out only partway, or you
        # opened it by hand -- nav_back is already on screen, so checking
        # for the lobby and clicking Play would be pointless (Play doesn't
        # even exist there) and would just burn LOBBY_CHECK_TIMEOUT waiting
        # for something that was never going to appear. One quick, non-
        # waiting check skips both steps straight to clicking Story.
        try:
            already_open = vision.find_image(hwnd, "nav_back") is not None
        except vision.TemplateNotFound as exc:
            self._log(f"[Macro] {exc}")
            return False
        if already_open:
            self._log("[Macro] Already on the gamemode menu -- skipping the lobby and Play.")
        else:
            if not self._ensure_lobby(hwnd, stop_event):
                return False
            if self._checkpoint(stop_event):
                return False
            if not self._click_play(hwnd, stop_event):
                return False
            if self._checkpoint(stop_event):
                return False

        if not self._click_gamemode(hwnd, stop_event, mode, wait_for_menu=not already_open):
            return False
        if self._checkpoint(stop_event):
            return False

        self._set_status(action=f'Selecting map "{map_name}"...')

        # Expedition doesn't use Story's scrolling map carousel -- it's a
        # small fixed set of map cards, each found by its own reference
        # image (or, for School Grounds, not found/clicked at all -- see
        # EXPEDITION_MAP_IMAGES).
        if mode == "expedition":
            if self._select_expedition_map(hwnd, stop_event, map_name):
                return True
            self._spam_back_until_gone(hwnd, stop_event)
            return False

        log_and_status = lambda msg: (self._log(msg), self._set_status(action=msg.split("] ", 1)[-1]))
        kwargs = {"debug_screenshots": self._debug_screenshots}
        if scroll_power is not None:
            kwargs["scroll_power"] = scroll_power
        if scroll_nudges is not None:
            kwargs["scroll_nudges"] = scroll_nudges
        if stage_select.find_and_click_map(self._mouse, hwnd, map_name, log_and_status, stop_event, **kwargs):
            return True

        self._spam_back_until_gone(hwnd, stop_event)
        return False

    def _reach_event_act_selected(self, hwnd, stop_event: threading.Event, act: str,
                                    scroll_power: int = None, scroll_nudges: int = None) -> bool:
        """Lobby -> Event -> Event gamemode -> Act (villain card), as one
        restartable unit -- Event's equivalent of _reach_map_selected. Event
        has its OWN lobby entry (the nav_event button), not the Play ->
        gamemode -> map flow the other modes share, so there's no gamemode
        menu or map carousel here: click nav_event, click the event_gamemode
        card, then the chosen Act's villain card. On any failure it backs out
        to the lobby (_spam_back_until_gone) so the next attempt starts clean,
        same as the map path does.

        The first couple of Act cards are on screen already; later ones
        (EVENT_ACT_SCROLL_FROM_INDEX on) sit below the fold and only come into
        view by scrolling, so those get the same wheel-scroll search the Story
        map carousel uses (see _scroll_find_and_click) instead of a plain
        wait-then-click.
        """
        act = str(act)
        act_images = EVENT_ACT_IMAGES.get(act)
        # Both structures are checked, not just the images: EVENT_ACT_ORDER is
        # indexed further down to decide whether the card needs scrolling to,
        # so an act present in one but not the other would raise ValueError
        # mid-navigation rather than failing cleanly here. They're hand-synced
        # and Act 4 is queued to be added, so it's worth not depending on that.
        if act_images is None or act not in EVENT_ACT_ORDER:
            self._log(f'[Macro] Unknown Event Act "{act}" -- expected one of {EVENT_ACT_ORDER}.')
            return False
        if isinstance(act_images, str):
            act_images = (act_images,)

        if not self._ensure_lobby(hwnd, stop_event):
            return False
        if self._checkpoint(stop_event):
            return False

        # nav_event: the lobby's Event button (its own nav entry, not under
        # Play). Each of the three clicks below is a wait-then-click with a
        # focus-safe verify via _click_found_image, and each screen animates
        # in, so a short settle follows before searching the next one.
        self._set_status(action="Clicking Event...")
        if self._click_found_image(hwnd, "nav_event", EVENT_SCREEN_TIMEOUT, stop_event) is None:
            self._spam_back_until_gone(hwnd, stop_event)
            return False
        if self._checkpoint(stop_event):
            return False
        time.sleep(SETTLE_DELAY)

        self._set_status(action="Clicking Event gamemode...")
        if self._click_found_image(hwnd, "event_gamemode", EVENT_SCREEN_TIMEOUT, stop_event) is None:
            self._spam_back_until_gone(hwnd, stop_event)
            return False
        if self._checkpoint(stop_event):
            return False
        time.sleep(SETTLE_DELAY)

        self._set_status(action=f"Clicking Act {act}...")
        needs_scroll = EVENT_ACT_ORDER.index(act) >= EVENT_ACT_SCROLL_FROM_INDEX
        if needs_scroll:
            # Act 3+ is below the fold -- scroll the villain list into view
            # (Story-carousel style) before clicking it.
            if not self._scroll_find_and_click(hwnd, act_images, stop_event, scroll_power, scroll_nudges,
                                                 label=f"Act {act}"):
                self._spam_back_until_gone(hwnd, stop_event)
                return False
        elif self._click_found_image(hwnd, act_images[0], EVENT_SCREEN_TIMEOUT, stop_event) is None:
            self._spam_back_until_gone(hwnd, stop_event)
            return False
        # Let the stage/Enter-Matchmaking screen finish animating in before
        # the shared tail searches for its confirm button (same reason
        # _select_stage settles after its own click).
        time.sleep(SETTLE_DELAY)
        return not self._checkpoint(stop_event)

    def _scroll_find_and_click(self, hwnd, names, stop_event: threading.Event,
                                 scroll_power: int = None, scroll_nudges: int = None,
                                 label: str = "card") -> bool:
        """Wheel-scroll search for a card that may sit below the fold, modeled
        on (and reusing the tuning of) the Story map carousel search -- see
        stage_select.find_and_click_map. `names` is a tuple of candidate crops;
        any match wins, so a card with several visual states (e.g. Event Act 4,
        which has a peeking-in crop and a fully-scrolled-in one) is matched in
        whichever state it's currently showing.

        Checks what's on screen first (no scroll), so a card that's already
        visible is clicked without scrolling at all; otherwise nudges the list
        forward up to scroll_nudges times, then resets to the top and tries the
        whole pass again, up to MAX_PASSES times -- same recover-by-resetting
        approach the map carousel uses so a nudge that lands mid-animation
        doesn't strand the search. Returns whether it found and clicked one.
        """
        power = scroll_power if scroll_power is not None else stage_select.DEFAULT_SCROLL_POWER
        nudges = scroll_nudges if scroll_nudges is not None else stage_select.SCROLL_NUDGES_PER_PASS
        scroll_step = -120 * max(1, int(power))
        nudges = max(0, int(nudges))
        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        cx, cy = left + stage_select.SCROLL_CENTER[0], top + stage_select.SCROLL_CENTER[1]

        for pass_no in range(1, stage_select.MAX_PASSES + 1):
            if self._checkpoint(stop_event):
                return False
            self._log(f'[Macro] Looking for {label} (pass {pass_no}/{stage_select.MAX_PASSES}, '
                       f'up to {nudges} scrolls)...')
            for nudge in range(nudges + 1):
                if stop_event is not None and stop_event.is_set():
                    return False
                try:
                    match, found = vision.find_image_any(hwnd, tuple(names))
                except vision.TemplateNotFound as exc:
                    self._log(f"[Macro] {exc}")
                    return False
                if match is not None:
                    debug_path = self._debug_save(hwnd, found, match)
                    suffix = f" Debug: {debug_path}" if debug_path else ""
                    self._log(f'[Macro] Found "{found}" (score {match["score"]:.2f}) -- clicking it.{suffix}')
                    vision.click_match(self._mouse, hwnd, match)
                    return True
                if nudge < nudges:
                    self._mouse.move_to(cx, cy)
                    stage_select._hover_wiggle(self._mouse)
                    self._mouse.scroll(scroll_step)
                    time.sleep(stage_select.SETTLE_DELAY)
            # Not on this pass -- scroll all the way back to a known start
            # before trying again (see the map carousel's identical reset).
            self._log(f"[Macro] {label} not found in this pass -- scrolling back to the start.")
            self._mouse.move_to(cx, cy)
            stage_select._hover_wiggle(self._mouse)
            for _ in range(stage_select.SCROLL_RESET_NOTCHES):
                self._mouse.scroll(-scroll_step)
            time.sleep(stage_select.SETTLE_DELAY)

        self._log(f'[Macro] {label} never came into view after {stage_select.MAX_PASSES} scroll passes -- '
                   f'stopping. If its card was visibly scrolling by, its crop isn\'t matching your setup '
                   f'-- add your own via Settings > General > Image Manager.')
        return False

    def _relic_dropped(self, hwnd) -> bool:
        """Whether a Crow Relic is on the Victory reward row (DROP_RELIC_IMAGE)
        -- the trigger for an event farm task's Act 4 auto-divert. Best-effort:
        a missing crop just reads as 'no relic', same as any other optional
        image search in this file."""
        try:
            return vision.find_image(hwnd, DROP_RELIC_IMAGE) is not None
        except vision.TemplateNotFound:
            return False

    def _act4_locked(self, hwnd) -> bool:
        """Whether Act 4's locked card (VILLIAN4_CLOSE_IMAGE, the 'requires 1
        Crow Relic / 0/1x Owned' popup) is on screen -- i.e. there's no relic
        to spend, so an entry attempt can't succeed. Best-effort, same as
        above."""
        try:
            return vision.find_image(hwnd, VILLIAN4_CLOSE_IMAGE) is not None
        except vision.TemplateNotFound:
            return False

    def _run_act4_diversion(self, hwnd, stop_event: threading.Event, farm_task: dict, coords: dict,
                              scroll_power: int, scroll_nudges: int, default_walk_paths: dict,
                              webhook: dict) -> None:
        """Clear Villian Invasion Act 4 ("Crow - Dawn") after a farm task's
        Victory dropped a Crow Relic. Uses the farm task's OWN Act 4 Macro
        Operation (act4_macro) -- Act 4 plays nothing like Acts 1-3, so it
        can't reuse the farm macro. Runs Act 4 once, or (act4_mode ==
        "until_locked") repeatedly until the card shows locked/out of relics
        (VILLIAN4_CLOSE_IMAGE). The farm stage was already Left in
        _handle_match_result, so this starts from the lobby; _run_task
        re-enters the farm task afterward. Best-effort throughout -- any
        navigation failure just logs and returns to let the farm resume."""
        until_locked = (farm_task.get("act4_mode") == "until_locked")
        act4_macro = farm_task.get("act4_macro") or ""
        # Act 4 can run in its own play mode (Solo/Matchmaking); falls back to
        # the farm task's play mode when it was never set (older tasks, or the
        # user leaving it on the inherited default). See the Act 4 Play Mode
        # control in ui/app.js renderTaskBuilder.
        play_mode = farm_task.get("act4_play_mode") or farm_task.get("play_mode") or "solo"
        runs = 0
        while True:
            if self._checkpoint(stop_event):
                return
            if self._current_hwnd and wm.is_window(self._current_hwnd):
                hwnd = self._current_hwnd

            # Synthetic one-repeat Act 4 task, driven through the exact same
            # setup/play/result pipeline a real event task uses (same trick as
            # Challenge's _run_one_challenge_stage). act4_on_drop is NOT set on
            # it, so its own _handle_match_result never re-triggers a divert.
            act4_task = {
                "mode": "event", "map": "Event", "stage": EVENT_ACT4_STAGE, "difficulty": "-",
                "macro": act4_macro, "play_mode": play_mode, "repeat": 1, "team": "",
                "equipment": farm_task.get("equipment") or "include", "is_act4_divert": True,
            }
            self._set_status(current_task="Act 4 (Crow - Dawn)", current_repeat="1 / 1", map="Event",
                              stage="Act 4", action="Clearing Act 4...", mode="event", difficulty="-",
                              play_mode=play_mode, macro=act4_macro or "-")
            self._log(f"[Macro] {'Clearing Act 4 again' if runs else 'Clearing Act 4 (Crow - Dawn)'} "
                       f"with Macro Operation \"{act4_macro or '-'}\".")

            if not self._run_task_setup(hwnd, stop_event, act4_task, "event", "Event", coords,
                                          scroll_power, scroll_nudges, webhook):
                if stop_event.is_set():
                    return
                if self._current_hwnd and wm.is_window(self._current_hwnd):
                    hwnd = self._current_hwnd
                # Couldn't enter. The expected reason after the relics run out
                # is the locked card -- distinguish it so "until locked" reads
                # as a clean finish, not an error.
                if self._act4_locked(hwnd):
                    self._log("[Macro] Act 4 is locked (no Crow Relic to spend) -- "
                               f"{'done clearing it' if runs else 'nothing to clear'}.")
                else:
                    self._log("[Macro] Couldn't enter Act 4 -- giving up on the divert.")
                self._spam_back_until_gone(hwnd, stop_event)
                return

            if self._current_hwnd and wm.is_window(self._current_hwnd):
                hwnd = self._current_hwnd
            battle_started = time.time()
            result = self._play_one_match(hwnd, stop_event, act4_task, default_walk_paths,
                                            first_repeat=True, webhook=webhook)
            if result is None:
                if not stop_event.is_set():
                    self._recover_to_lobby(hwnd, stop_event)
                return
            duration = self._format_duration(time.time() - battle_started)
            # Always Leave Stage (repeat=False) -- one clear per entry; the
            # loop decides whether to go again.
            if not self._handle_match_result(hwnd, stop_event, act4_task, result, duration, webhook,
                                               repeat=False):
                return
            if self._checkpoint(stop_event):
                return
            runs += 1
            self._log(f"[Macro] Act 4 cleared ({result}, {duration}).")
            if not until_locked:
                return
            # until_locked: loop and try again; the next setup lands on the
            # locked card once relics run out and stops us there.

    def _spam_back_until_gone(self, hwnd, stop_event: threading.Event) -> None:
        # A failed map search can leave the run sitting on any of several
        # nested screens (mid-carousel-scroll, a map's detail panel, ...) --
        # repeatedly clicking Back until it's no longer found backs out
        # through however many of those there actually are, instead of
        # leaving the game wherever the failed search happened to stop for
        # the next run to trip over. Best-effort: not fatal either way, this
        # only ever runs after the run has already decided to give up.
        self._log("[Macro] Backing out after failed map search...")
        self._set_status(action="Backing out...")
        for attempt in range(1, BACK_SPAM_MAX_CLICKS + 1):
            if stop_event.is_set():
                return
            # wait_for_image, not a one-shot find_image: right after a
            # click, the next nested screen (if any) is still mid-
            # transition for a moment, and a single check timed unluckily
            # can miss a Back button that's genuinely there a beat later --
            # exactly what left this only clicking once when a second click
            # was still needed.
            try:
                match = vision.wait_for_image(
                    hwnd, "nav_back", timeout=BACK_SPAM_CHECK_TIMEOUT, stop_event=stop_event)
            except vision.TemplateNotFound as exc:
                self._log(f"[Macro] {exc}")
                return
            if match is None:
                self._log(f"[Macro] Back button gone after {attempt - 1} click(s) -- done.")
                return
            vision.click_match(self._mouse, hwnd, match)
            time.sleep(BACK_SPAM_DELAY)
        self._log(f"[Macro] Stopped backing out after {BACK_SPAM_MAX_CLICKS} clicks (Back button still found).")

    def _click_gamemode(self, hwnd, stop_event: threading.Event, mode: str, wait_for_menu: bool = True) -> bool:
        # Story's card position doesn't move once the menu is open, so it's
        # just a fixed coordinate (see STORY_CLICK's comment). Raid's isn't
        # known as a fixed spot, so it's found by image search instead --
        # nav_back's appearance is only used to confirm the menu has
        # actually finished opening before either click fires, not to
        # locate Story itself. wait_for_menu=False skips that check
        # when the caller already confirmed nav_back is on screen (see the
        # "already open" shortcut in _run) -- no point polling for something
        # already known to be there.
        if wait_for_menu:
            match = None
            for attempt in range(1, PLAY_CLICK_RETRY_ATTEMPTS + 1):
                self._log(f'[Macro] Waiting for the gamemode menu to open (searching "nav_back", up to '
                           f'{STORY_SCREEN_TIMEOUT:.0f}s)...' if attempt == 1 else
                           f'[Macro] "nav_back" not found within {STORY_SCREEN_TIMEOUT:.0f}s -- still on '
                           f'the lobby, re-clicking Play (attempt {attempt}/{PLAY_CLICK_RETRY_ATTEMPTS})...')
                self._set_status(action='Waiting for gamemode menu ("nav_back")...')
                try:
                    match = vision.wait_for_image(
                        hwnd, "nav_back", timeout=STORY_SCREEN_TIMEOUT, stop_event=stop_event)
                except vision.TemplateNotFound as exc:
                    self._log(f"[Macro] Can't confirm the menu opened: {exc}")
                    return False
                if match is not None or stop_event.is_set():
                    break
                if attempt < PLAY_CLICK_RETRY_ATTEMPTS:
                    # Still on the lobby -- the earlier Play click plausibly
                    # never actually registered (see PLAY_CLICK_RETRY_ATTEMPTS'
                    # comment). Re-clicking is retriable in a way waiting
                    # even longer for a click that already failed isn't.
                    if not self._click_play(hwnd, stop_event):
                        return False
            if match is None:
                if not stop_event.is_set():
                    self._log(f'[Macro] "nav_back" not found within {STORY_SCREEN_TIMEOUT:.0f}s x '
                               f'{PLAY_CLICK_RETRY_ATTEMPTS} attempt(s) -- the gamemode menu never opened '
                               f'(if it\'s visibly open, add your own crop of its Back button via '
                               f'Settings > General > Image Manager). Stopping.')
                    self._save_debug_screenshot_unconditional(hwnd, "gamemode_menu_timeout")
                return False

        # A "Disband Party" prompt can sit in front of the menu at this
        # point -- if it's up, Story can't be clicked (or clicks through to
        # the wrong thing) until it's dismissed. Optional/one-shot: no long
        # wait, since most runs never see it, and if nav_disband.png hasn't
        # been added yet this is just silently skipped rather than failing
        # the whole run over a nice-to-have check.
        try:
            disband_match, disband_name = vision.find_image_any(hwnd, NAV_DISBAND_IMAGE_NAMES)
        except vision.TemplateNotFound:
            disband_match, disband_name = None, None
        if disband_match is not None:
            debug_path = self._debug_save(hwnd, disband_name, disband_match)
            suffix = f" Debug: {debug_path}" if debug_path else ""
            self._log(f"[Macro] Found Disband Party prompt (score {disband_match['score']:.2f}) -- "
                       f"clicking it before Story.{suffix}")
            vision.click_match(self._mouse, hwnd, disband_match)
            if stop_event.is_set():
                return False
            time.sleep(0.3)  # let the prompt actually close before clicking the gamemode card

        if mode == "expedition":
            self._log("[Macro] Menu open -- searching for Expedition...")
            self._set_status(action="Clicking Expedition...")
            try:
                match, name = vision.wait_for_image_any(
                    hwnd, EXPEDITION_IMAGE_NAMES, region=GAMEMODE_CARD_REGION, timeout=GAMEMODE_CLICK_TIMEOUT, stop_event=stop_event)
            except vision.TemplateNotFound as exc:
                self._log(f"[Macro] Can't find Expedition: {exc}")
                return False
            if match is None:
                if not stop_event.is_set():
                    self._log(f'[Macro] "expedition" not found within {GAMEMODE_CLICK_TIMEOUT:.0f}s -- the '
                               f'Expedition card never showed up, stopping.')
                return False
            debug_path = self._debug_save(hwnd, name, match)
            suffix = f" Debug: {debug_path}" if debug_path else ""
            self._log(f"[Macro] Found Expedition (score {match['score']:.2f}) -- clicking it.{suffix}")
            vision.click_match(self._mouse, hwnd, match)
            return True

        if mode == "challenge":
            self._log("[Macro] Menu open -- searching for Challenge...")
            self._set_status(action="Clicking Challenge...")
            try:
                match, name = vision.wait_for_image_any(
                    hwnd, CHALLENGE_IMAGE_NAMES, region=GAMEMODE_CARD_REGION, timeout=GAMEMODE_CLICK_TIMEOUT, stop_event=stop_event)
            except vision.TemplateNotFound as exc:
                self._log(f"[Macro] Can't find Challenge: {exc}")
                return False
            if match is None:
                if not stop_event.is_set():
                    self._log(f'[Macro] "challenge" not found within {GAMEMODE_CLICK_TIMEOUT:.0f}s -- the '
                               f'Challenge card never showed up, stopping.')
                return False
            debug_path = self._debug_save(hwnd, name, match)
            suffix = f" Debug: {debug_path}" if debug_path else ""
            self._log(f"[Macro] Found Challenge (score {match['score']:.2f}) -- clicking it.{suffix}")
            vision.click_match(self._mouse, hwnd, match)
            return True

        if mode == "raid":
            self._log("[Macro] Menu open -- searching for Raid...")
            self._set_status(action="Clicking Raid...")
            try:
                match, name = vision.wait_for_image_any(
                    hwnd, RAID_IMAGE_NAMES, region=GAMEMODE_CARD_REGION, timeout=GAMEMODE_CLICK_TIMEOUT, stop_event=stop_event)
            except vision.TemplateNotFound as exc:
                self._log(f"[Macro] Can't find Raid: {exc}")
                return False
            if match is None:
                if not stop_event.is_set():
                    self._log(f'[Macro] "raid" not found within {GAMEMODE_CLICK_TIMEOUT:.0f}s -- the '
                               f'Raid card never showed up, stopping.')
                return False
            debug_path = self._debug_save(hwnd, name, match)
            suffix = f" Debug: {debug_path}" if debug_path else ""
            self._log(f"[Macro] Found Raid (score {match['score']:.2f}) -- clicking it.{suffix}")
            vision.click_match(self._mouse, hwnd, match)
            return True

        # story.png alone used to not be distinct enough to match reliably
        # (see STORY_CLICK's comment) -- Assets/ui/story/ holds a second
        # reference crop of the same card (every image in the folder is
        # tried per search, see vision.template_variant_paths), so image
        # search is worth trying again here before falling back to the
        # fixed coordinate.
        self._log("[Macro] Menu open -- searching for Story...")
        self._set_status(action="Clicking Story...")
        try:
            match, name = vision.wait_for_image_any(
                hwnd, STORY_IMAGE_NAMES, region=GAMEMODE_CARD_REGION, timeout=GAMEMODE_CLICK_TIMEOUT, stop_event=stop_event)
        except vision.TemplateNotFound:
            match, name = None, None
        if match is not None:
            debug_path = self._debug_save(hwnd, name, match)
            suffix = f" Debug: {debug_path}" if debug_path else ""
            self._log(f"[Macro] Found Story (score {match['score']:.2f}) -- clicking it.{suffix}")
            vision.click_match(self._mouse, hwnd, match)
        else:
            if stop_event.is_set():
                return False
            story_x, story_y = self._cxy("story_click")
            self._log(f"[Macro] Story card not found by image search -- falling back to fixed coordinate ({story_x}, {story_y}).")
            left, top, _, _ = wm.get_window_rect_screen(hwnd)
            self._mouse.click(left + story_x, top + story_y)
        # Unlike Raid/Expedition/Challenge (which wait_for_image their own
        # gamemode card before clicking, naturally giving the screen a
        # moment), the fixed-coordinate fallback above is blind -- nav_back
        # confirms the MENU is open, not that the map carousel it leads to
        # has finished rendering. The very first carousel scan used to fire
        # immediately after this click, which could catch it mid-transition
        # and misread/misclick the wrong card (reported: landing on the
        # wrong map, e.g. a different Story map instead of the one asked
        # for).
        time.sleep(SETTLE_DELAY)
        return True
