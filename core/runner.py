"""The macro's actual run loop -- Dashboard > Start. Currently covers the
launch sequence (confirm the task queue isn't empty, confirm we're on the
lobby screen, open Play > Story), picking the first task's map and stage,
Pre Start (camera setup, a per-map default walk, the task's Macro Operation
template's Pre Start blocks), pressing Start Game, and then waiting out the
battle for a Victory/Defeat screen -- which gets its game stats (and, on a
win, its rewards) read via OCR, recorded to run history/win-loss counts, and
reported to Discord if a webhook is configured. Team/equipment application
and the full Battle-phase block runner (mid-match upgrades/sells/waits) plug
in once those exist -- this only watches for the match to END, it doesn't
act during it yet.
"""
import threading
import time
from datetime import datetime, timezone

from . import camera
from . import keys
from . import paths as walk_paths
from . import stage_select
from . import vision
from . import window as wm

# Nav > Play button, in the docked game window's own client coordinates
# (top-left of the docked Roblox window == (0, 0), same convention as every
# other fixed region in this codebase, e.g. main.REWARD_SCROLLBAR_PROBE).
NAV_PLAY_REGION = (74, 434, 58, 58)

# Recovery after a failed map search (see _spam_back_until_gone): repeatedly
# click Back until it's no longer found, rather than leaving the run stuck
# on whatever screen the failed search happened to end on.
BACK_SPAM_MAX_CLICKS = 8
BACK_SPAM_DELAY = 0.4
# Nested screens (map detail -> gamemode menu -> lobby) each have their own
# Back button -- a short poll after each click, not a one-shot check, so
# the NEXT screen's Back button gets a beat to render before concluding
# there isn't one and stopping a click early.
BACK_SPAM_CHECK_TIMEOUT = 1.5
# How many full lobby->Play->Story->map-search restarts to try (see
# _reach_map_selected) before giving up on this task entirely.
MAP_SELECT_RETRY_ATTEMPTS = 3
# How many times _run_task recovers to the lobby and retries a task from
# scratch after a mid-task failure (a stuck battle, a missed click, ...)
# before giving up on just that task and moving on to the next one -- so one
# bad match doesn't end an unattended overnight run.
TASK_RECOVERY_ATTEMPTS = 3

# The Story card's position on the gamemode-select screen (after Play) is
# fixed -- unlike Play itself, nothing here needs to be found by image
# search, just clicked. Raid's card sits somewhere else on the same panel;
# rather than guess a second fixed coordinate, it's found by image search
# (raid.png) instead -- its crop is a colored word on a transparent-ish
# background, distinct enough for template matching, unlike the earlier
# story.png attempt (see Assets/ui/README.txt).
STORY_CLICK = (666, 147)

LOBBY_CHECK_TIMEOUT = 15.0   # how long to wait for the Play button to appear before giving up
STORY_SCREEN_TIMEOUT = 10.0  # Play's menu (Story/Raid) animates in, not instant
BACK_CONFIRM_TIMEOUT = 8.0   # how long to wait for nav_back after clicking Story, to confirm it landed
GAMEMODE_CLICK_TIMEOUT = 8.0  # how long to search for the Raid card once the menu's open

# Stage-select screen (after picking a map): a fixed vertical list of rows,
# same x for every row, y stepping by one row height per stage -- Level 1
# through 5, then Infinite, then Mastery, in that fixed order (matches
# TASK_DATA.story.stages in ui/app.js). No image search needed for the rows
# themselves, just nav_select_stage to confirm the screen has loaded before
# clicking a computed position on it.
STAGE_SCREEN_TIMEOUT = 10.0
STAGE_ORDER = ["1", "2", "3", "4", "5", "Infinite", "Mastery"]
STAGE_CLICK_BASE = (246, 230)  # Level 1's click point
STAGE_ROW_HEIGHT = 56

# Raid's stage-select screen only ever shows 3 Acts, spaced much further
# apart than Story's rows -- same screen (nav_select_stage), same confirm
# click, just a different row layout (matches TASK_DATA.raid.stages).
ACT_ORDER = ["1", "2", "3"]
ACT_CLICK_BASE = (250, 267)  # Act 1's click point
ACT_ROW_HEIGHT = 129

# Infinite/Mastery are locked to Hard in-game with no picker shown for them
# (see ui/app.js's TASK_DATA.story comment) -- no difficulty click happens
# for those stages at all, so there's nothing to look up for them here.
SPECIAL_STAGES_NO_DIFFICULTY = ("Infinite", "Mastery")

# Defaults for the stage-detail panel's Normal/Hard toggle and the region
# Enter Matchmaking is searched for in -- overridable per-run via the
# `coords` dict (see _run), sourced from Settings > Debug > Macro
# Coordinates so a game update shifting any of these doesn't need a code
# change, just a setting.
DEFAULT_COORDS = {
    "difficulty_normal_x": 311, "difficulty_normal_y": 315,
    "difficulty_hard_x": 364, "difficulty_hard_y": 315,
    "matchmaking_region_x": 277, "matchmaking_region_y": 543,
    "matchmaking_region_w": 437, "matchmaking_region_h": 45,
}
MATCHMAKING_WAIT_TIMEOUT = 10.0
SOLO_START_TIMEOUT = 10.0  # Solo mode's direct Start button, in place of Enter Matchmaking

# Teleporting into the actual match can take a while (loading screen) --
# nav_unitmanager only renders once you're actually in-game, so waiting for
# it is the "did we teleport in" confirmation. For matchmaking this is one
# long wait (TELEPORT_IN_TIMEOUT); for Solo it's retried in shorter chunks
# alongside re-clicking Start -- see _click_start_and_wait_teleport.
TELEPORT_IN_TIMEOUT = 30.0
SOLO_START_RETRY_ATTEMPTS = 3
SOLO_TELEPORT_PER_ATTEMPT_TIMEOUT = 20.0  # generous per chunk -- a slow teleport shouldn't burn through attempts

# Whether Start Game is even present depends on being the party leader, so
# this is a quick presence check, not a long wait. Short on purpose: Start
# Game (when it exists at all) reliably renders in the same beat as
# nav_unitmanager -- by the time _wait_teleport_in already confirmed
# nav_unitmanager is up, Start Game is either already there too or it was
# never going to show up, so there's nothing to gain from waiting several
# more seconds to find that out.
START_GAME_CHECK_TIMEOUT = 1.5
NAV_CLICK_TIMEOUT = 8.0  # nav_settings / nav_search in the Auto Vote Start fallback
SETTLE_DELAY = 0.6  # lets a panel-open animation (e.g. Settings) finish before searching it

# Place Unit block execution: click, check for a rejection message, nudge
# and retry if blocked, then verify. cannot_place/max_placement_reached are
# matched over gameplay art (see vision.find_bottommost_image's reasoning),
# so their thresholds are set a bit stricter than the general default.
PLACE_UNIT_CLICK_SETTLE = 0.25   # lets a rejection message actually render before checking for it
PLACE_UNIT_MAX_NUDGES = 10
# Small offsets tried in order when a spot is rejected -- a simple expanding
# cross/diagonal pattern, not a specific direction, since "slightly move
# until it works" doesn't imply which way is more likely to be clear.
PLACE_UNIT_NUDGE_OFFSETS = [
    (0, 0), (12, 0), (-12, 0), (0, 12), (0, -12),
    (16, 16), (-16, 16), (16, -16), (-16, -16), (24, 0),
]
PLACE_UNIT_VERIFY_TIMEOUT = 2.0
PLACE_UNIT_VERIFY_ATTEMPTS = 3  # search-then-click retried up to this many times before giving up on verifying
CANNOT_PLACE_THRESHOLD = 0.85
MAX_PLACEMENT_THRESHOLD = 0.85
UNIT_INFO_RESET_CLICK = (3, 3)  # near-empty corner of the Roblox screen -- closes the unit info panel after verifying

# Fallbacks if main.py doesn't pass real calibrated regions through (mirrors
# main.py's REWARD_REGION_DEFAULTS/STATS_REGION_DEFAULTS).
DEFAULT_REWARD_REGION = {"x": 212, "y": 429, "width": 504, "height": 106}
DEFAULT_STATS_REGION = {"x": 210, "y": 337, "width": 509, "height": 57}

# Victory/Defeat: no fixed timeout makes sense for "how long can a battle
# run", so this is a generous safety net (30 min), not an expected duration --
# polled slowly since there's no rush to notice a screen that, once it
# appears, just sits there until acted on.
MATCH_RESULT_TIMEOUT = 1800.0
MATCH_RESULT_POLL_INTERVAL = 1.0


class MacroRunner:
    """One run's worth of state -- module-level singleton via main.Api, same
    pattern as core.paths._recorder, since only one run can realistically be
    active at a time (one physical game window, one macro)."""

    def __init__(self, mouse, keyboard, log, set_status=None, record_result=None):
        self._mouse = mouse
        self._keyboard = keyboard
        self._log = log
        self._set_status = set_status or (lambda **kw: None)
        # (result: "win"|"loss", map_name, duration_str, stats_dict, items_list) ->
        # persists to run history / win-loss counters (see main.Api._record_match_result).
        self._record_result = record_result or (lambda *a, **kw: None)
        self._thread = None
        self._stop_event = None
        self._pause_event = threading.Event()
        self._paused_logged = False
        self._debug_screenshots = False
        self._current_hwnd = None       # set at the top of _run -- lets _checkpoint reach Leave Stage on stop
        self._left_stage_this_run = False

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def is_paused(self) -> bool:
        return self._pause_event.is_set()

    def start(self, hwnd_getter, get_tasks, scroll_power: int = None, coords: dict = None,
              scroll_nudges: int = None, debug_screenshots: bool = False, default_walk_paths: dict = None,
              reward_region: dict = None, stats_region: dict = None, webhook: dict = None) -> dict:
        if self.is_running():
            return {"ok": False, "reason": "already_running"}
        self._stop_event = threading.Event()
        self._pause_event.clear()
        self._paused_logged = False
        self._debug_screenshots = bool(debug_screenshots)
        self._current_hwnd = None
        self._left_stage_this_run = False
        self._thread = threading.Thread(
            target=self._run,
            args=(hwnd_getter, get_tasks, self._stop_event, scroll_power, coords, scroll_nudges, default_walk_paths,
                  reward_region, stats_region, webhook),
            daemon=True)
        self._thread.start()
        return {"ok": True}

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
            self._log("[Macro] Stopped.")
            self._set_status(action="Idle")
            return True
        return False

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

    def _debug_save(self, hwnd, name: str, match: dict) -> str:
        """Gated behind Settings > Debug > "Debug Match Screenshots" (off by
        default): a full-window screenshot on every single match found while
        running -- Play, Story, every stage row, ... -- adds up fast, so this
        only actually writes when the toggle is on. Returns None otherwise,
        which callers fold into their log line's " Debug: ..." suffix."""
        return vision.save_match_debug(hwnd, name, match) if self._debug_screenshots else None

    def _run(self, hwnd_getter, get_tasks, stop_event: threading.Event, scroll_power: int = None,
              coords: dict = None, scroll_nudges: int = None, default_walk_paths: dict = None,
              reward_region: dict = None, stats_region: dict = None, webhook: dict = None) -> None:
        coords = {**DEFAULT_COORDS, **(coords or {})}
        default_walk_paths = default_walk_paths or {}
        reward_region = reward_region or DEFAULT_REWARD_REGION
        stats_region = stats_region or DEFAULT_STATS_REGION
        webhook = webhook or {}
        tasks = get_tasks()
        if not tasks:
            self._log("[Macro] Task queue is empty -- add a task on the Task screen first.")
            self._set_status(action="Idle")
            return

        hwnd = hwnd_getter()
        if not hwnd or not wm.is_window(hwnd):
            self._log("[Macro] Roblox isn't docked yet -- can't start.")
            self._set_status(action="Idle")
            return
        self._current_hwnd = hwnd

        self._log(f"[Macro] Starting run -- {len(tasks)} task(s) queued.")

        # A click has to actually reach the game, not this panel -- same
        # focus-fix every other live-input action in this app uses.
        wm.show_window(hwnd)
        wm.activate_window(hwnd)

        for task_index, task in enumerate(tasks, start=1):
            if self._checkpoint(stop_event):
                return

            map_name = task.get("map")
            if not map_name:
                self._log(f"[Macro] Task {task_index}/{len(tasks)} has no map set -- skipping it.")
                continue

            # A mid-task failure (a stuck battle, a missed click, ...)
            # doesn't kill the whole overnight run -- _run_task recovers to
            # the lobby and retries internally, only returning False when
            # stop_event actually fired.
            if not self._run_task(hwnd, stop_event, task, task_index, len(tasks), coords, scroll_power,
                                    scroll_nudges, default_walk_paths, reward_region, stats_region, webhook):
                self._set_status(action="Idle")
                return

        self._log("[Macro] Task queue finished -- all tasks complete.")
        self._set_status(current_task="-", current_repeat="-", map="-", action="Idle")

    def _run_task(self, hwnd, stop_event: threading.Event, task: dict, task_index: int, task_count: int,
                   coords: dict, scroll_power: int, scroll_nudges: int, default_walk_paths: dict,
                   reward_region: dict, stats_region: dict, webhook: dict) -> bool:
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
            if recovery_attempt > 1:
                self._log(f'[Macro] Retrying task {task_index}/{task_count} from the lobby '
                           f'(attempt {recovery_attempt}/{TASK_RECOVERY_ATTEMPTS})...')
            self._log(f'[Macro] Task {task_index}/{task_count}: "{map_name}" x{repeat_total}.')
            self._set_status(current_task=f"{task_index} / {task_count}", current_repeat=f"1 / {repeat_total}",
                              map=map_name, action="Starting...")

            # Everything from the lobby through the first teleport-in runs
            # ONCE per task -- every repeat after that re-enters the same
            # stage directly via Repeat Stage (see _handle_match_result),
            # skipping the lobby/gamemode/map/stage picks entirely.
            if not self._run_task_setup(hwnd, stop_event, task, mode, map_name, coords, scroll_power, scroll_nudges):
                if stop_event.is_set():
                    return False
                if not self._recover_to_lobby(hwnd, stop_event):
                    return not stop_event.is_set()
                continue

            task_failed = False
            for repeat_index in range(1, repeat_total + 1):
                self._set_status(current_repeat=f"{repeat_index} / {repeat_total}")
                battle_started = time.time()
                result = self._play_one_match(hwnd, stop_event, task, default_walk_paths,
                                                first_repeat=(repeat_index == 1))
                if result is None:
                    if stop_event.is_set():
                        return False
                    task_failed = True
                    break
                duration = self._format_duration(time.time() - battle_started)

                is_last_repeat = repeat_index == repeat_total
                if not self._handle_match_result(hwnd, stop_event, task, result, duration,
                                                  reward_region, stats_region, webhook, repeat=not is_last_repeat):
                    if stop_event.is_set():
                        return False
                    task_failed = True
                    break
                if self._checkpoint(stop_event):
                    return False

                if not is_last_repeat:
                    if not self._wait_teleport_in(hwnd, stop_event):
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
        if self._checkpoint(stop_event):
            return False
        self._spam_back_until_gone(hwnd, stop_event)
        if self._checkpoint(stop_event):
            return False
        return self._ensure_lobby(hwnd, stop_event)

    def _run_task_setup(self, hwnd, stop_event: threading.Event, task: dict, mode: str, map_name: str,
                          coords: dict, scroll_power: int, scroll_nudges: int) -> bool:
        """Lobby -> Play -> Story/Raid -> map -> stage/act -> difficulty ->
        confirm -> matchmaking/solo -> teleport-in. Runs once per TASK, not
        once per repeat -- see the repeat loop in _run."""
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
            self._select_difficulty(hwnd, task.get("difficulty") or "Normal", coords)
        if self._checkpoint(stop_event):
            return False

        # nav_select_stage has to be CLICKED here, not just waited on --
        # after picking a stage row and difficulty, it's a confirm button
        # that finalizes the choice; Start/Enter Matchmaking doesn't
        # actually appear/work until it's pressed.
        self._set_status(action="Clicking Select Stage...")
        if not self._click_found_image(hwnd, "nav_select_stage", STAGE_SCREEN_TIMEOUT, stop_event):
            return False
        if self._checkpoint(stop_event):
            return False

        # Solo has its own direct Start button -- Enter Matchmaking is
        # multiplayer-only and was never going to appear in Solo mode, which
        # is exactly why it kept sitting there waiting on it and looking
        # like it was "going to matchmaking" regardless of this setting.
        if task.get("play_mode") == "matchmaking":
            if not self._click_enter_matchmaking(hwnd, stop_event, coords):
                return False
            if self._checkpoint(stop_event):
                return False
            if not self._wait_teleport_in(hwnd, stop_event):
                return False
        else:
            self._log("[Macro] Solo mode -- clicking Start (retrying up to "
                       f"{SOLO_START_RETRY_ATTEMPTS} times if it doesn't teleport).")
            if not self._click_start_and_wait_teleport(hwnd, stop_event):
                return False
        return not self._checkpoint(stop_event)

    def _play_one_match(self, hwnd, stop_event: threading.Event, task: dict, default_walk_paths: dict,
                          first_repeat: bool = True):
        """Assumes teleport-in already happened -- the initial one from
        _run_task_setup, or a repeat's re-teleport after Repeat Stage (see
        _handle_match_result). Start Game settings check, Pre Start, the
        actual Start Game click, then watches for Victory/Defeat. Runs once
        per repeat. first_repeat gates the default walk and any "Once"
        Pre Start block so they only fire on the task's first entry into
        this stage, not on every repeat (see _run_prestart). Returns
        "win"/"loss", or None on failure/stop."""
        if not self._start_game_or_reset_via_settings(hwnd, stop_event):
            return None
        if self._checkpoint(stop_event):
            return None

        if not self._run_prestart(hwnd, stop_event, task, default_walk_paths, first_repeat):
            return None
        if self._checkpoint(stop_event):
            return None

        self._log("[Macro] Pre Start finished -- starting the round.")
        self._set_status(action="Starting the round...")
        try:
            start_match = vision.find_image(hwnd, "nav_start_game")
        except vision.TemplateNotFound as exc:
            self._log(f"[Macro] {exc}")
            start_match = None
        if start_match is not None:
            debug_path = self._debug_save(hwnd, "nav_start_game", start_match)
            suffix = f" Debug: {debug_path}" if debug_path else ""
            self._log(f"[Macro] Found Start Game (score {start_match['score']:.2f}) -- clicking it.{suffix}")
            vision.click_match(self._mouse, hwnd, start_match)
        else:
            # Not fatal: Start Game may already have been pressed by the
            # leader (or Auto Vote Start already handles it) earlier in
            # _start_game_or_reset_via_settings -- its absence here just
            # means the round is already starting on its own.
            self._log("[Macro] Start Game not found -- already started, continuing.")
        if self._checkpoint(stop_event):
            return None

        self._set_status(action="Battle. (Team/equipment + Battle-phase blocks aren't wired up yet.)")
        self._log("[Macro] Moving into Battle. (Team/equipment + Battle-phase blocks aren't wired up yet.)")

        return self._wait_for_match_result(hwnd, stop_event)

    def _wait_for_match_result(self, hwnd, stop_event: threading.Event):
        self._log("[Macro] Battle in progress -- watching for Victory/Defeat...")
        self._set_status(action="Battle in progress...")
        deadline = time.time() + MATCH_RESULT_TIMEOUT
        while time.time() < deadline:
            if self._checkpoint(stop_event):
                return None
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
        self._log(f"[Macro] Timed out after {MATCH_RESULT_TIMEOUT / 60:.0f} min waiting for Victory/Defeat.")
        # Unlike every other debug screenshot (gated behind Settings > Debug
        # > "Debug Match Screenshots"), this one always saves -- a timeout
        # this long is rare enough, and useful enough to diagnose after the
        # fact, that it's worth it unconditionally rather than only when the
        # toggle happened to already be on.
        try:
            left, top, right, bottom = wm.get_window_rect_screen(hwnd)
            path = vision.save_region_debug(hwnd, "match_result_timeout", (0, 0, right - left, bottom - top))
            self._log(f"[Macro] Saved a screenshot of the timeout for troubleshooting: {path}")
        except Exception as exc:
            self._log(f"[Macro] Couldn't save a timeout screenshot: {exc}")
        return None

    @staticmethod
    def _format_duration(seconds: float) -> str:
        seconds = int(seconds)
        m, s = divmod(seconds, 60)
        return f"{m}m {s}s" if m else f"{s}s"

    def _handle_match_result(self, hwnd, stop_event: threading.Event, task: dict, result: str, duration: str,
                              reward_region: dict, stats_region: dict, webhook: dict, repeat: bool) -> bool:
        label = "Victory" if result == "win" else "Defeat"

        # Only the CAPTURE (a couple of screenshots, maybe one scroll) has to
        # happen while the result screen is actually still up -- the OCR
        # itself (several seconds of Tesseract subprocesses) doesn't need
        # anything more from the screen once the pixels are already in hand,
        # so it runs on its own thread instead of making the run sit and
        # wait on it before it can move on to Repeat/Leave Stage.
        self._set_status(action=f"Capturing {label} screen...")
        stats_image = self._capture_stats_image(hwnd, stats_region)
        reward_images = self._capture_reward_images(hwnd, reward_region) if result == "win" else None

        map_name = task.get("map") or "-"
        threading.Thread(
            target=self._finish_match_result_background,
            args=(stats_image, reward_images, result, map_name, duration, task, webhook),
            daemon=True,
        ).start()
        self._log(f"[Macro] {label} ({duration}) -- stats/rewards processing in the background.")

        # The cursor is moved to the same near-empty corner
        # _reset_unit_info_panel uses first, so a leftover hover
        # state/tooltip from whatever was under the cursor can't throw off
        # whichever button gets clicked next.
        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        self._mouse.move_to(left + UNIT_INFO_RESET_CLICK[0], top + UNIT_INFO_RESET_CLICK[1])
        time.sleep(0.1)

        if repeat:
            # More repeats left on this task -- Repeat Stage re-queues the
            # same stage directly, skipping the lobby/gamemode/map/stage
            # picks entirely (see _run_task_setup, which only runs once per
            # task, not once per repeat).
            self._set_status(action=f"{label} -- clicking Repeat Stage...")
            if not self._click_found_image(hwnd, "repeat_stage", NAV_CLICK_TIMEOUT, stop_event):
                self._log('[Macro] "Repeat Stage" not found -- can\'t continue this task\'s repeats, stopping.')
                return False
            return True

        # Last repeat of this task (or the whole queue) -- back out to the
        # lobby so the next task's setup (or a clean stop) starts from a
        # known state instead of sitting on the result screen.
        self._set_status(action=f"{label} -- clicking Leave Stage...")
        if not self._click_found_image(hwnd, "leave_stage", NAV_CLICK_TIMEOUT, stop_event):
            self._log('[Macro] "Leave Stage" not found -- stopping.')
            return False
        return True

    def _finish_match_result_background(self, stats_image, reward_images, result: str, map_name: str,
                                          duration: str, task: dict, webhook: dict) -> None:
        stats = self._ocr_game_stats(stats_image)
        items = []
        if reward_images is not None:
            allowed_names = self._log_expected_rewards(task)
            items = self._ocr_reward_items(*reward_images, allowed_names=allowed_names)
        self._record_result(result, map_name, duration, stats, items)
        self._send_result_webhook(webhook, result, task, duration, stats, items)

    def _log_expected_rewards(self, task: dict) -> list:
        # Reference logged right before the actual OCR'd items so they're
        # easy to eyeball against each other -- scraped from the wiki's own
        # data (see tools/fetch_stage_data.py). The returned name list is
        # also fed into icon identification (core.rewards.identify_item_name's
        # allowed_names) to narrow its candidate pool down to what this
        # specific stage can actually reward, instead of every known item --
        # not just a passive log line, an actual accuracy improvement.
        try:
            from . import stage_data
            map_name, stage, difficulty = task.get("map"), task.get("stage") or "1", task.get("difficulty") or "Normal"
            expected = stage_data.expected_rewards(map_name, stage, difficulty)
            names = stage_data.expected_item_names(map_name, stage, difficulty)
        except Exception:
            return None
        if expected:
            self._log(f"[Macro] Possible reward for this stage: {', '.join(expected)}")
        return names or None

    def _capture_stats_image(self, hwnd, stats_region: dict):
        try:
            from core.ocr import capture_region
            left, top, _, _ = wm.get_window_rect_screen(hwnd)
            return capture_region(
                left + stats_region["x"], top + stats_region["y"], stats_region["width"], stats_region["height"])
        except Exception as exc:
            self._log(f"[Macro] Couldn't capture the game stats region: {exc}")
            return None

    def _ocr_game_stats(self, image) -> dict:
        if image is None:
            return {}
        try:
            from core import game_stats
            stats = game_stats.read_game_stats(image)
        except Exception as exc:
            self._log(f"[Macro] Couldn't read game stats: {exc}")
            return {}
        self._log(f"[Macro] Stats -- Clear Time {stats.get('clear_time') or '?'} | "
                   f"Yen {stats.get('total_yen') or '?'} | Kills {stats.get('total_kills') or '?'} | "
                   f"Damage {stats.get('total_damage') or '?'}")
        return stats

    def _capture_reward_images(self, hwnd, reward_region: dict):
        # Same capture-then-maybe-scroll-then-capture-again dance as
        # main.Api.read_rewards. The scroll only happens when the scrollbar
        # track is actually, confidently detected (rewards.SCROLLBAR_TOLERANCE,
        # stricter than sample_color_matches' own default) -- scrolling on a
        # false positive doesn't just waste time, it can scroll the page into
        # a completely different panel/section and capture THAT as
        # "image_bottom", which merge_reward_pages then blends in as if it
        # were more real rewards (garbled quantities, items that were never
        # actually dropped). If the scrollbar isn't confidently found, this
        # never touches the mouse at all -- no move into the reward box, no
        # scroll.
        try:
            from core import rewards
            from core.ocr import capture_region, sample_color_matches
            left, top, _, _ = wm.get_window_rect_screen(hwnd)
            image_top = capture_region(
                left + reward_region["x"], top + reward_region["y"],
                reward_region["width"], reward_region["height"])

            probe_x, probe_y, probe_w, probe_h = rewards.SCROLLBAR_PROBE
            has_more = sample_color_matches(
                left + probe_x, top + probe_y, probe_w, probe_h, rewards.SCROLLBAR_COLOR,
                tolerance=rewards.SCROLLBAR_TOLERANCE)
            if not has_more:
                self._log("[Macro] Reward list fits in view -- no scroll needed.")
                return (image_top, None)

            self._log("[Macro] Reward list overflows -- scrolling for the rest.")
            wm.activate_window(hwnd)
            time.sleep(0.1)
            box_cx = left + reward_region["x"] + reward_region["width"] // 2
            box_cy = top + reward_region["y"] + reward_region["height"] // 2
            self._mouse.move_to(box_cx, box_cy)
            time.sleep(0.05)
            self._mouse.nudge()
            time.sleep(0.2)
            for _ in range(20):
                self._mouse.scroll(-120)
                time.sleep(0.02)
            time.sleep(0.2)
            image_bottom = capture_region(
                left + reward_region["x"], top + reward_region["y"],
                reward_region["width"], reward_region["height"])
            # Move off the reward box once scrolling is done -- leaving the
            # cursor parked there can keep it "active"/hovered for whatever
            # comes next, same reasoning as the unit-info-panel reset click.
            self._mouse.move_to(left + UNIT_INFO_RESET_CLICK[0], top + UNIT_INFO_RESET_CLICK[1])
            return (image_top, image_bottom)
        except Exception as exc:
            self._log(f"[Macro] Couldn't capture the rewards region: {exc}")
            return None

    def _ocr_reward_items(self, image_top, image_bottom, allowed_names: list = None) -> list:
        try:
            from core import rewards
            pages = [rewards.read_reward_grid(image_top, allowed_names=allowed_names)]
            if image_bottom is not None:
                pages.append(rewards.read_reward_grid(image_bottom, allowed_names=allowed_names))
            items = rewards.merge_reward_pages(*pages)
        except Exception as exc:
            self._log(f"[Macro] Couldn't read rewards: {exc}")
            return []

        if not items:
            self._log("[Macro] No reward icons read -- check the region in Settings > Debug.")
        for item in items:
            self._log(f"[Macro] Reward: {item.get('quantity') or '?'} {item.get('name') or '(unreadable)'}")
        return items

    def _send_result_webhook(self, webhook: dict, result: str, task: dict, duration: str,
                              stats: dict, items: list) -> None:
        url = (webhook or {}).get("url")
        if not url or not webhook.get("enabled"):
            return
        from . import webhook as webhook_module

        is_win = result == "win"
        map_name = task.get("map") or "-"
        stage = task.get("stage") or "-"
        difficulty = task.get("difficulty") or "-"

        fields = [
            {"name": "Map", "value": map_name, "inline": True},
            {"name": "Stage", "value": stage, "inline": True},
            {"name": "Difficulty", "value": difficulty, "inline": True},
            {"name": "Clear Time", "value": stats.get("clear_time") or "?", "inline": True},
            {"name": "Total Yen", "value": stats.get("total_yen") or "?", "inline": True},
            {"name": "Total Kills", "value": stats.get("total_kills") or "?", "inline": True},
            {"name": "Total Damage", "value": stats.get("total_damage") or "?", "inline": True},
            {"name": "Run Time", "value": duration, "inline": True},
        ]
        if is_win and items:
            lines = [f"{item.get('quantity') or '?'}x {item.get('name') or '(unreadable)'}" for item in items]
            # Discord field values cap at 1024 chars -- trim the list rather
            # than let a long drop silently fail the whole webhook send.
            text = "\n".join(lines)
            if len(text) > 1024:
                text = text[:1000] + "\n..."
            fields.append({"name": f"Rewards ({len(items)})", "value": text, "inline": False})

        embed = {
            "title": "Victory!" if is_win else "Defeat",
            "color": 0x3FBF6F if is_win else 0xE05A6D,
            "fields": fields,
            "footer": {"text": "Cream's Macro | Anime Expeditions"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        mention_id = (webhook or {}).get("mention_id")
        content = f"<@{mention_id}>" if mention_id else ""
        try:
            webhook_module.send(url, embed, content=content, silent=bool(webhook.get("silent")))
        except Exception as exc:
            self._log(f"[Macro] Webhook send failed: {exc}")

    def _run_prestart(self, hwnd, stop_event: threading.Event, task: dict, default_walk_paths: dict,
                        first_repeat: bool = True) -> bool:
        # Camera setup ALWAYS runs in Pre Start -- it's a per-match reset
        # (the camera doesn't stay put across a repeat's re-teleport), so it
        # isn't gated by first_repeat the way the walk below is.
        self._log("[Macro] Pre Start: setting up the camera...")
        self._set_status(action="Setting up camera...")
        try:
            camera.run_camera_setup(self._mouse, self._keyboard, hwnd)
            self._log("[Macro] Camera setup done.")
        except Exception as exc:
            self._log(f"[Macro] Camera setup failed: {exc}")
        if self._checkpoint(stop_event):
            return False

        # The default walk only makes sense the FIRST time a task enters a
        # stage -- once you're standing where the walk leaves you, repeating
        # the same walk on every repeat would just walk you away from that
        # spot again for no reason. first_repeat=False (a Repeat Stage
        # re-entry, see _run's repeat loop) skips it entirely.
        if not first_repeat:
            self._log("[Macro] Repeat of the same stage -- skipping the default walk (already walked on entry).")
        else:
            map_name = task.get("map")
            # A Raid map's Acts can need different walks (e.g. Spirit City
            # Act 3 -- see ACT_ORDER) -- looked up as "<map> Act<n>" first,
            # falling back to the plain map-name entry other Acts/Story
            # share, so only the Acts that actually need a different walk
            # need their own default_walk_paths entry.
            path_name = None
            if map_name:
                if task.get("mode") == "raid":
                    path_name = default_walk_paths.get(f"{map_name} Act{task.get('stage')}")
                path_name = path_name or default_walk_paths.get(map_name)
            if not path_name:
                self._log(f'[Macro] No default walk path set for "{map_name}" -- skipping walk.'
                           if map_name else "[Macro] No map set -- skipping walk.")
            else:
                self._log(f'[Macro] Walking path "{path_name}"...')
                self._set_status(action=f'Walking "{path_name}"...')
                data = walk_paths.load_path(path_name)
                events = data.get("events", [])
                if not events:
                    self._log(f'[Macro] Walk path "{path_name}" has no recorded movement -- skipping.')
                else:
                    walk_paths.replay_events(events, self._keyboard, stop_event)
                    self._log("[Macro] Walk finished.")
        if self._checkpoint(stop_event):
            return False

        self._run_prestart_blocks(hwnd, stop_event, task, first_repeat)
        if self._checkpoint(stop_event):
            return False
        return True

    def _run_prestart_blocks(self, hwnd, stop_event: threading.Event, task: dict, first_repeat: bool = True) -> None:
        # The task's Macro Operation (Creation > template) is what actually
        # places starter units and flips settings -- this is the piece that
        # was never wired up: the field existed on every Task card, but
        # nothing ever read it. Runs after camera+walk and before Start Game
        # is pressed, same as Pre Start blocks are laid out in Creation.
        macro_name = task.get("macro")
        if not macro_name:
            self._log("[Macro] No Macro Operation set on this task -- nothing to place.")
            return

        from . import templates as tpl
        data = tpl.load_template(macro_name)
        blocks = data.get("blocks") or {}
        if isinstance(blocks, list):
            self._log(f'[Macro] Template "{macro_name}" is saved in an old format -- '
                       f'open it in Creation and Save again to run its Pre Start blocks.')
            return
        prestart_blocks = blocks.get("prestart") if "prestart" in blocks else blocks.get("before")
        prestart_blocks = prestart_blocks or []
        if not prestart_blocks:
            self._log(f'[Macro] Template "{macro_name}" has no Pre Start blocks.')
            return

        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        self._log(f'[Macro] Running {len(prestart_blocks)} Pre Start block(s) from "{macro_name}"...')
        self._set_status(action=f'Running "{macro_name}" Pre Start blocks...')
        for i, block in enumerate(prestart_blocks, start=1):
            if self._checkpoint(stop_event):
                return
            if block.get("once") and not first_repeat:
                # "Once" (see the block's Once chip in Creation) means only
                # the task's FIRST entry into this stage runs it -- e.g. a
                # starter placement that shouldn't be re-placed (and would
                # just get rejected as a duplicate/waste a click) on every
                # repeat of the same stage.
                self._log(f'[Macro] Skipping block #{i} -- marked "Once" and this isn\'t the first repeat.')
                continue
            btype = block.get("type")
            if btype == "place_unit":
                self._run_place_unit_block(hwnd, stop_event, left, top, block, i, macro_name)
            elif btype == "setting_change":
                self._run_setting_block(block, i)
            else:
                self._log(f'[Macro] Skipping block #{i} ("{btype}") -- not runnable in Pre Start yet.')
            time.sleep(0.2)  # brief gap between blocks so the game UI can settle

    def _run_place_unit_block(self, hwnd, stop_event: threading.Event, left: int, top: int, block: dict,
                                index: int, macro_name: str) -> None:
        params = block.get("params") or {}
        name = params.get("name") or f"#{index}"
        hotkey = block.get("hotkey")
        orig_x, orig_y = params.get("x"), params.get("y")
        self._set_status(action=f'Placing unit "{name}"...')

        if not (orig_x or orig_y):
            self._log(f'[Macro] Place Unit "{name}" has no position set -- skipping.')
            return

        # Z first, always -- clears whatever the cursor/UI was last doing so
        # the hotkey press right after it reliably starts a fresh placement
        # instead of potentially colliding with leftover state.
        self._keyboard.tap(ord("Z"))
        time.sleep(0.1)

        if hotkey:
            vk = keys.key_name_to_vk(hotkey)
            if vk is not None:
                self._log(f'[Macro] Place Unit "{name}": pressing hotkey "{hotkey}".')
                self._keyboard.tap(vk)
                time.sleep(0.15)  # lets the placement cursor/ghost actually appear before the click
            else:
                self._log(f'[Macro] Place Unit "{name}": hotkey "{hotkey}" isn\'t recognized -- skipping key press.')
        else:
            self._log(f'[Macro] Place Unit "{name}" has no hotkey set -- skipping key press.')

        # Click, then check for a rejection message. cannot_place/
        # max_placement_reached are optional templates (like nav_disband) --
        # a missing image just means that check is silently skipped rather
        # than failing the block, since not everyone will have added them.
        cur_x, cur_y = int(orig_x), int(orig_y)
        placed = False
        for attempt in range(1, PLACE_UNIT_MAX_NUDGES + 1):
            if self._checkpoint(stop_event):
                return
            self._mouse.click(left + cur_x, top + cur_y)
            time.sleep(PLACE_UNIT_CLICK_SETTLE)

            try:
                limit_match = vision.find_image(hwnd, "max_placement_reached", threshold=MAX_PLACEMENT_THRESHOLD)
            except vision.TemplateNotFound:
                limit_match = None
            if limit_match is not None:
                self._log(f'[Macro] Place Unit "{name}": max placement limit reached -- skipping this block.')
                return

            try:
                blocked_match = vision.find_bottommost_image(hwnd, "cannot_place", threshold=CANNOT_PLACE_THRESHOLD)
            except vision.TemplateNotFound:
                blocked_match = None
            if blocked_match is None:
                placed = True
                break

            dx, dy = PLACE_UNIT_NUDGE_OFFSETS[attempt % len(PLACE_UNIT_NUDGE_OFFSETS)]
            cur_x, cur_y = int(orig_x) + dx, int(orig_y) + dy
            self._log(f'[Macro] Place Unit "{name}": can\'t place there (attempt {attempt}/{PLACE_UNIT_MAX_NUDGES}, '
                       f'score {blocked_match["score"]:.2f}) -- nudging to ({cur_x}, {cur_y}).')

        if not placed:
            self._log(f'[Macro] Place Unit "{name}": still blocked after {PLACE_UNIT_MAX_NUDGES} nudges -- giving up.')
            return

        # Verify: look for unit_exist FIRST, before clicking anything -- it
        # may already be visible with no extra input needed at all. Only if
        # it isn't there does this click once (not double-click, which risked
        # triggering something else entirely, like a sell/context menu) and
        # check again, up to PLACE_UNIT_VERIFY_ATTEMPTS times total.
        exists_match = None
        clicked_to_verify = False
        for verify_attempt in range(1, PLACE_UNIT_VERIFY_ATTEMPTS + 1):
            if self._checkpoint(stop_event):
                return
            if verify_attempt > 1:
                self._mouse.click(left + cur_x, top + cur_y)
                clicked_to_verify = True
                time.sleep(0.3)  # let the info panel actually render before checking for it
            try:
                exists_match = vision.wait_for_image(hwnd, "unit_exist", timeout=PLACE_UNIT_VERIFY_TIMEOUT)
            except vision.TemplateNotFound:
                exists_match = None
                break  # no unit_exist.png added -- retrying won't change that, stop wasting clicks
            if exists_match is not None:
                break
            self._log(f'[Macro] Place Unit "{name}": verify check {verify_attempt}/{PLACE_UNIT_VERIFY_ATTEMPTS} '
                       f'-- unit_exist not seen yet.')

        # Only reset the info panel if a verify click actually happened --
        # the plain search-first check above never opens anything, so there's
        # nothing to close if that's all it took.
        if clicked_to_verify:
            self._reset_unit_info_panel(hwnd)

        if exists_match is None:
            self._log(f'[Macro] Place Unit "{name}": placed at ({cur_x}, {cur_y}) but couldn\'t verify '
                       f'(no unit_exist match) -- add Assets/ui/unit_exist.png to enable this check.')
            return

        self._log(f'[Macro] Place Unit "{name}": verified placed at ({cur_x}, {cur_y}) '
                   f'(score {exists_match["score"]:.2f}).')
        if (cur_x, cur_y) != (int(orig_x), int(orig_y)):
            self._save_corrected_position(macro_name, index, cur_x, cur_y, name)

    def _reset_unit_info_panel(self, hwnd) -> None:
        # Closes whatever info panel double-clicking a placed unit opened
        # (see the verify step above) -- Z first (same deselect pressed
        # before every placement), then a click on a near-empty corner of
        # the Roblox screen, (3, 3), well clear of any real UI so it can't
        # be mistaken for a live game action.
        self._keyboard.tap(ord("Z"))
        time.sleep(0.1)
        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        self._mouse.click(left + UNIT_INFO_RESET_CLICK[0], top + UNIT_INFO_RESET_CLICK[1])

    def _save_corrected_position(self, macro_name: str, block_index: int, x: int, y: int, unit_name: str) -> None:
        # Writes a nudged-but-verified position back into the saved template
        # file, keyed by its position in the Pre Start list -- so a future
        # run (or another task using the same Macro Operation) starts from a
        # spot already known to work instead of repeating the same nudge
        # search from scratch every single time. block_index is 1-based, the
        # same numbering _run_prestart_blocks logs with.
        from . import templates as tpl
        data = tpl.load_template(macro_name)
        blocks = data.get("blocks") or {}
        if isinstance(blocks, list):
            return  # legacy shape -- not worth patching, the template needs re-saving anyway
        prestart_blocks = blocks.get("prestart") if "prestart" in blocks else blocks.get("before")
        if not prestart_blocks or block_index > len(prestart_blocks):
            return
        target = prestart_blocks[block_index - 1]
        if target.get("type") != "place_unit":
            return  # the template changed shape since this run started reading it -- don't guess
        target.setdefault("params", {})
        target["params"]["x"] = x
        target["params"]["y"] = y
        tpl.save_template(macro_name, blocks)
        self._log(f'[Macro] Saved corrected position for "{unit_name}" back into template "{macro_name}".')

    def _run_setting_block(self, block: dict, index: int) -> None:
        name = (block.get("params") or {}).get("name") or f"#{index}"
        kind = block.get("kind")
        if kind != "hotkey":
            # Toggle/slider Setting blocks record a value to plan around
            # (On/Off, 0-2) but never actually captured a key for it -- there's
            # nothing to press for those yet, only Hotkey-kind blocks are.
            self._log(f'[Macro] Setting "{name}" ({kind or "?"}) has no hotkey to press yet -- skipping.')
            return
        value = block.get("value")
        vk = keys.key_name_to_vk(value)
        if vk is None:
            self._log(f'[Macro] Setting "{name}": no hotkey captured yet -- skipping.')
            return
        self._set_status(action=f'Setting "{name}"...')
        self._log(f'[Macro] Setting "{name}": pressing hotkey "{value}".')
        self._keyboard.tap(vk)

    def _wait_teleport_in(self, hwnd, stop_event: threading.Event) -> bool:
        # nav_unitmanager only renders once you're actually in the match (not
        # during the loading/teleport transition), so waiting for it is the
        # confirmation the teleport actually finished.
        self._log("[Macro] Waiting to teleport in-game...")
        self._set_status(action="Waiting to teleport in-game...")
        try:
            match = vision.wait_for_image(
                hwnd, "nav_unitmanager", timeout=TELEPORT_IN_TIMEOUT, stop_event=stop_event)
        except vision.TemplateNotFound as exc:
            self._log(f"[Macro] Can't confirm teleport-in: {exc}")
            return False
        if match is None:
            if not stop_event.is_set():
                self._log("[Macro] Never teleported in-game (Unit Manager not found) -- stopping.")
            return False
        self._log("[Macro] Teleported in-game.")
        return True

    def _click_start_and_wait_teleport(self, hwnd, stop_event: threading.Event) -> bool:
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
        for attempt in range(1, SOLO_START_RETRY_ATTEMPTS + 1):
            if self._checkpoint(stop_event):
                return False

            # wait_for_image, not a bare one-shot find_image: right after
            # nav_select_stage is clicked, this screen is still mid-
            # transition for a moment, and a single check timed unluckily
            # can miss a button that's genuinely there a beat later. Only on
            # a LATER attempt (clicked already) is a quick poll actually
            # useful signal about whether it's really gone.
            try:
                start_match = vision.wait_for_image(
                    hwnd, "nav_start", timeout=SOLO_START_TIMEOUT, stop_event=stop_event)
            except vision.TemplateNotFound as exc:
                self._log(f"[Macro] {exc}")
                return False
            if self._checkpoint(stop_event):
                return False
            if start_match is not None:
                debug_path = self._debug_save(hwnd, "nav_start", start_match)
                suffix = f" Debug: {debug_path}" if debug_path else ""
                self._log(f'[Macro] Found "nav_start" (score {start_match["score"]:.2f}) -- clicking it.{suffix}')
                self._set_status(action="Clicking Start...")
                vision.click_match(self._mouse, hwnd, start_match)
                clicked = True
            elif not clicked:
                # Never managed to click it even once, and it's already
                # gone -- this is the wrong screen entirely, not a slow
                # teleport, so there's nothing to keep waiting on.
                self._log('[Macro] Couldn\'t find "nav_start" -- stopping.')
                return False
            else:
                self._log("[Macro] Start already clicked -- still teleporting, waiting longer.")
            if self._checkpoint(stop_event):
                return False

            self._log("[Macro] Waiting to teleport in-game...")
            self._set_status(action="Waiting to teleport in-game...")
            try:
                match = vision.wait_for_image(
                    hwnd, "nav_unitmanager", timeout=SOLO_TELEPORT_PER_ATTEMPT_TIMEOUT, stop_event=stop_event)
            except vision.TemplateNotFound as exc:
                self._log(f"[Macro] Can't confirm teleport-in: {exc}")
                return False
            if match is not None:
                self._log("[Macro] Teleported in-game.")
                return True
            if stop_event.is_set():
                return False
            self._log("[Macro] Didn't teleport yet -- checking again.")

        self._log(f"[Macro] Never teleported in-game after {SOLO_START_RETRY_ATTEMPTS} tries -- stopping.")
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
                self._log(f'[Macro] Couldn\'t find "{name}" -- stopping.')
            return None
        debug_path = self._debug_save(hwnd, name, match)
        suffix = f" Debug: {debug_path}" if debug_path else ""
        self._log(f'[Macro] Found "{name}" (score {match["score"]:.2f}) -- clicking it.{suffix}')
        vision.click_match(self._mouse, hwnd, match)
        return match

    def _start_game_or_reset_via_settings(self, hwnd, stop_event: threading.Event) -> bool:
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

        self._log("[Macro] No Start Game button (not the party leader) -- checking Auto Vote Start instead.")
        self._set_status(action="Opening Settings for Auto Vote Start...")
        if not self._click_found_image(hwnd, "nav_settings", NAV_CLICK_TIMEOUT, stop_event):
            return False
        if self._checkpoint(stop_event):
            return False
        # The Settings panel opens with a scale/slide-in animation -- without
        # this, nav_search can get matched (even at a perfect 1.00 score)
        # against a mid-animation frame, whose search box isn't at its final
        # settled position yet. The click then lands wherever that transient
        # frame put it instead of where the box actually ends up, missing it
        # entirely. SETTLE_DELAY is comfortably longer than the animation.
        time.sleep(SETTLE_DELAY)
        search_match = self._click_found_image(hwnd, "nav_search", NAV_CLICK_TIMEOUT, stop_event)
        if not search_match:
            return False
        if self._checkpoint(stop_event):
            return False
        # Saved so the search box can be clicked back into later (see the
        # "restart game" search below) WITHOUT a second image search -- once
        # the list is filtered and a toggle's been clicked, the panel has
        # reflowed around that, and re-matching nav_search's image against
        # that changed layout is exactly the kind of transitional-frame risk
        # SETTLE_DELAY exists to dodge in the first place. The box itself
        # doesn't move, so its position from the very first click is
        # already good for every future search.
        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        search_box_pos = (left + search_match["cx"], top + search_match["cy"])

        self._set_status(action='Searching settings for "Auto Vote Start"...')
        self._log('[Macro] Typing "Auto Vote Start"...')
        time.sleep(0.2)  # let the search field actually take focus before typing
        self._keyboard.type_text("Auto Vote Start")
        time.sleep(SETTLE_DELAY)  # let the filtered settings list render before reading its toggle
        if self._checkpoint(stop_event):
            return False

        try:
            toggle_match = vision.find_image(hwnd, "toggle_true")
        except vision.TemplateNotFound as exc:
            self._log(f"[Macro] {exc}")
            toggle_match = None
        if toggle_match is not None:
            debug_path = self._debug_save(hwnd, "toggle_true", toggle_match)
            suffix = f" Debug: {debug_path}" if debug_path else ""
            self._log(f"[Macro] Auto Vote Start is on (score {toggle_match['score']:.2f}) -- turning it off.{suffix}")
            vision.click_match(self._mouse, hwnd, toggle_match)
        else:
            self._log("[Macro] Auto Vote Start already off -- nothing to click.")
        if self._checkpoint(stop_event):
            return False

        self._log("[Macro] Clicking back into the search box...")
        self._mouse.click(*search_box_pos)
        time.sleep(0.2)
        self._keyboard.combo(keys.VK_CONTROL, ord("A"))  # select the existing search text...
        self._keyboard.tap(keys.VK_DELETE)                # ...and clear it before typing the next search
        if self._checkpoint(stop_event):
            return False

        self._set_status(action='Searching settings for "Restart Game"...')
        self._log('[Macro] Typing "restart game"...')
        self._keyboard.type_text("restart game")
        time.sleep(SETTLE_DELAY)
        if self._checkpoint(stop_event):
            return False

        if not self._click_found_image(hwnd, "restart_btn", NAV_CLICK_TIMEOUT, stop_event):
            return False
        if self._checkpoint(stop_event):
            return False

        # restart_btn2 -- a confirmation prompt Restart Game brings up (e.g.
        # "Are you sure?"), clicked right after the first press to actually
        # commit to it.
        if not self._click_found_image(hwnd, "restart_btn2", NAV_CLICK_TIMEOUT, stop_event):
            return False
        if self._checkpoint(stop_event):
            return False

        # The restart itself may have already closed Settings on its own --
        # this is just a cleanup check, not a required step, so it's a
        # one-shot look (after a settle delay for the restart to actually
        # take effect) rather than a long wait, and finding nothing here is
        # success too, not a failure of the whole flow.
        time.sleep(SETTLE_DELAY)
        try:
            settings_match = vision.find_image(hwnd, "nav_settings_on")
        except vision.TemplateNotFound as exc:
            self._log(f"[Macro] {exc}")
            return True
        if settings_match is None:
            self._log("[Macro] Settings already closed after restart.")
            return True
        debug_path = self._debug_save(hwnd, "nav_settings_on", settings_match)
        suffix = f" Debug: {debug_path}" if debug_path else ""
        self._log(f"[Macro] Settings still open (score {settings_match['score']:.2f}) -- closing it.{suffix}")
        vision.click_match(self._mouse, hwnd, settings_match)
        return True

    def _select_difficulty(self, hwnd, difficulty: str, coords: dict) -> None:
        # Fixed spot on the stage-detail panel, same as the stage rows --
        # no image search needed, just like Story's click was.
        key_prefix = "difficulty_hard" if difficulty == "Hard" else "difficulty_normal"
        x, y = coords[f"{key_prefix}_x"], coords[f"{key_prefix}_y"]
        self._log(f'[Macro] Clicking difficulty "{difficulty}" at ({x}, {y}).')
        self._set_status(action=f'Clicking difficulty "{difficulty}"...')
        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        self._mouse.click(left + x, top + y)

    def _click_enter_matchmaking(self, hwnd, stop_event: threading.Event, coords: dict) -> bool:
        region = (
            coords["matchmaking_region_x"], coords["matchmaking_region_y"],
            coords["matchmaking_region_w"], coords["matchmaking_region_h"],
        )
        self._log("[Macro] Waiting for Enter Matchmaking...")
        self._set_status(action="Waiting for Enter Matchmaking...")
        try:
            match = vision.wait_for_image(
                hwnd, "enter_matchmaking", region=region,
                timeout=MATCHMAKING_WAIT_TIMEOUT, stop_event=stop_event)
        except vision.TemplateNotFound as exc:
            self._log(f"[Macro] Can't find Enter Matchmaking: {exc}")
            return False
        if match is None:
            if not stop_event.is_set():
                self._log("[Macro] Enter Matchmaking never showed up -- stopping.")
            return False
        debug_path = self._debug_save(hwnd, "enter_matchmaking", match)
        suffix = f" Debug: {debug_path}" if debug_path else ""
        self._log(f"[Macro] Found Enter Matchmaking (score {match['score']:.2f}) -- clicking it.{suffix}")
        vision.click_match(self._mouse, hwnd, match)
        return True

    def _select_stage(self, hwnd, stop_event: threading.Event, stage: str, mode: str) -> bool:
        # Raid's screen is the same nav_select_stage screen as Story's, just
        # with 3 Act rows spaced differently instead of the 7 stage rows
        # (see ACT_ORDER/ACT_CLICK_BASE/ACT_ROW_HEIGHT).
        order, base, row_height, label = (
            (ACT_ORDER, ACT_CLICK_BASE, ACT_ROW_HEIGHT, "Act") if mode == "raid"
            else (STAGE_ORDER, STAGE_CLICK_BASE, STAGE_ROW_HEIGHT, "stage"))
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
                self._log("[Macro] Stage select screen never opened -- stopping.")
            return False

        idx = order.index(stage)
        x = base[0]
        y = base[1] + idx * row_height
        self._log(f'[Macro] Stage screen open -- clicking {label} "{stage}" at ({x}, {y}).')
        self._set_status(action=f'Clicking {label} "{stage}"...')
        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        self._mouse.click(left + x, top + y)
        return True

    def _ensure_lobby(self, hwnd, stop_event: threading.Event) -> bool:
        # "On the lobby" is inferred from the Play button actually being
        # visible in its known Nav spot -- it only renders there outside of
        # a match, so finding it there IS the lobby check, not a separate
        # step before it.
        self._log("[Macro] Checking you're on the lobby...")
        self._set_status(action="Checking lobby...")
        try:
            match = vision.wait_for_image(
                hwnd, "nav_play", region=NAV_PLAY_REGION,
                timeout=LOBBY_CHECK_TIMEOUT, stop_event=stop_event)
        except vision.TemplateNotFound as exc:
            self._log(f"[Macro] Can't check the lobby: {exc}")
            return False
        if match is None:
            if not stop_event.is_set():
                self._log("[Macro] Doesn't look like you're on the lobby (Play button not found) -- stopping.")
            return False
        return True

    def _click_play(self, hwnd, stop_event: threading.Event) -> bool:
        self._set_status(action="Clicking Play...")
        try:
            match = vision.find_image(hwnd, "nav_play", region=NAV_PLAY_REGION)
        except vision.TemplateNotFound as exc:
            self._log(f"[Macro] {exc}")
            return False
        if match is None:
            self._log("[Macro] Play button vanished before it could be clicked -- stopping.")
            return False
        debug_path = self._debug_save(hwnd, "nav_play", match)
        suffix = f" Debug: {debug_path}" if debug_path else ""
        self._log(f"[Macro] Found Play (score {match['score']:.2f}) -- clicking it.{suffix}")
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
            self._log("[Macro] Waiting for the gamemode menu to open...")
            self._set_status(action="Waiting for gamemode menu...")
            try:
                match = vision.wait_for_image(
                    hwnd, "nav_back", timeout=STORY_SCREEN_TIMEOUT, stop_event=stop_event)
            except vision.TemplateNotFound as exc:
                self._log(f"[Macro] Can't confirm the menu opened: {exc}")
                return False
            if match is None:
                if not stop_event.is_set():
                    self._log("[Macro] Gamemode menu never opened (no Back button found) -- stopping.")
                return False

        # A "Disband Party" prompt can sit in front of the menu at this
        # point -- if it's up, Story can't be clicked (or clicks through to
        # the wrong thing) until it's dismissed. Optional/one-shot: no long
        # wait, since most runs never see it, and if nav_disband.png hasn't
        # been added yet this is just silently skipped rather than failing
        # the whole run over a nice-to-have check.
        try:
            disband_match = vision.find_image(hwnd, "nav_disband")
        except vision.TemplateNotFound:
            disband_match = None
        if disband_match is not None:
            debug_path = self._debug_save(hwnd, "nav_disband", disband_match)
            suffix = f" Debug: {debug_path}" if debug_path else ""
            self._log(f"[Macro] Found Disband Party prompt (score {disband_match['score']:.2f}) -- "
                       f"clicking it before Story.{suffix}")
            vision.click_match(self._mouse, hwnd, disband_match)
            if stop_event.is_set():
                return False
            time.sleep(0.3)  # let the prompt actually close before clicking the gamemode card

        if mode == "raid":
            self._log("[Macro] Menu open -- searching for Raid...")
            self._set_status(action="Clicking Raid...")
            try:
                match = vision.wait_for_image(
                    hwnd, "raid", timeout=GAMEMODE_CLICK_TIMEOUT, stop_event=stop_event)
            except vision.TemplateNotFound as exc:
                self._log(f"[Macro] Can't find Raid: {exc}")
                return False
            if match is None:
                if not stop_event.is_set():
                    self._log("[Macro] Raid card never showed up -- stopping.")
                return False
            debug_path = self._debug_save(hwnd, "raid", match)
            suffix = f" Debug: {debug_path}" if debug_path else ""
            self._log(f"[Macro] Found Raid (score {match['score']:.2f}) -- clicking it.{suffix}")
            vision.click_match(self._mouse, hwnd, match)
            return True

        self._log(f"[Macro] Menu open -- clicking Story at {STORY_CLICK}.")
        self._set_status(action="Clicking Story...")
        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        self._mouse.click(left + STORY_CLICK[0], top + STORY_CLICK[1])
        return True
