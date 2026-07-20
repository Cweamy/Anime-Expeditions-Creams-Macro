"""The macro's actual run loop -- Dashboard > Start. Currently covers the
launch sequence (confirm the task queue isn't empty, confirm we're on the
lobby screen, open Play > Story), picking the first task's map and stage,
Pre Start (camera setup, Team Loadout, a per-map default walk, the task's
Macro Operation template's Pre Start blocks), pressing Start Game, and then
watching the battle for Battle-phase blocks (Upgrade/Sell/Auto Upgrade Unit)
and a Victory/Defeat screen -- which gets its game stats (and, on a win, its
rewards) read via OCR, recorded to run history/win-loss counts, and reported
to Discord if a webhook is configured. Equipment include/exclude, and the
rest of the Battle-phase block types (Walk/Wait/Setting), plug in once those
exist.
"""
import os
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
# Padded well past the button's own ~58x58 footprint (was searched at
# exactly that size, with zero margin for the button being even a few
# pixels off from where this assumed it'd be -- matchTemplate needs the
# whole template to fit inside the search region, so a template this size
# with no padding had nowhere to "look around" at all) -- template matching
# already scans every position within whatever region it's given, so a
# bigger region is a wider/fuzzier scan for free, not a slower or less
# precise one; the score threshold (see vision.DEFAULT_THRESHOLD) is what
# actually decides what counts as a match, not the region size.
NAV_PLAY_REGION = (44, 404, 118, 118)

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
# A perfectly-matched Play click that never opens the gamemode menu is
# exactly the "click didn't register" focus flakiness _click_play's own
# activate_window() reassertion was already added for (see its comment) --
# still reported by some users even with that fix in place, so this retries
# the whole click instead of trusting one attempt, same idea as
# SOLO_START_RETRY_ATTEMPTS below.
PLAY_CLICK_RETRY_ATTEMPTS = 3
# Same "click didn't register" flakiness, but for the actual "start the
# round" click -- previously fired once with no verification at all, so a
# dropped click here left the run sitting on the Start Game confirmation
# forever while _wait_for_match_result was already off watching for a
# Victory/Defeat that could never come.
START_GAME_CLICK_RETRY_ATTEMPTS = 3
START_GAME_CLICK_VERIFY_SETTLE = 1.0  # after clicking, how long to wait before checking it's actually gone

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
# it is the "did we teleport in" confirmation. Used for the repeat-cycle
# re-teleport (already-matched session, should be near-instant) and as
# Solo's per-attempt chunk -- see SOLO_TELEPORT_PER_ATTEMPT_TIMEOUT/
# _click_start_and_wait_teleport. NOT used for matchmaking's initial entry
# (see MATCHMAKING_TELEPORT_TIMEOUT below) -- that one's a genuinely
# different wait, not just a longer version of this one.
TELEPORT_IN_TIMEOUT = 30.0
# Clicking Enter Matchmaking doesn't teleport you in on its own -- it only
# happens once the lobby actually FILLS with real players, which can take
# anywhere from seconds to several minutes depending on server population,
# nothing like Solo's near-instant teleport. Reusing TELEPORT_IN_TIMEOUT
# (30s) here was timing this out mid-legitimate-wait almost every time,
# which looked exactly like "clicked Enter Matchmaking, then just never
# did anything else."
MATCHMAKING_TELEPORT_TIMEOUT = 300.0
SOLO_START_RETRY_ATTEMPTS = 3
SOLO_TELEPORT_PER_ATTEMPT_TIMEOUT = 20.0  # generous per chunk -- a slow teleport shouldn't burn through attempts
# How long teleportstuck.png (optional -- see Assets/ui/README.txt) must be
# CONTINUOUSLY visible during a teleport-in wait before the game is treated
# as broken and needing a rejoin, rather than just a slow loading screen.
# reconnect.png/reconnect_2.png/retry.png (Roblox's own disconnect prompt)
# are a DEFINITE signal on their own -- no continuous-visibility wait needed,
# unlike teleportstuck's spinner which can be a false alarm for a moment.
TELEPORT_STUCK_TIMEOUT = 10.0
TELEPORT_POLL_INTERVAL = 0.3
RECONNECT_IMAGE_NAMES = ("reconnect", "reconnect_2", "retry")

# Roblox deep link used to rejoin after a detected disconnect -- reopens
# (or, if the client fully closed, relaunches) straight into this specific
# experience instead of leaving the run stuck on a Reconnect prompt forever.
PLACE_ID = "84515722934860"
REJOIN_DEEPLINK = f"roblox://experiences/start?placeId={PLACE_ID}"
REJOIN_TIMEOUT = 90.0  # relaunching Roblox from scratch can take a while
REJOIN_POLL_INTERVAL = 2.0

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

# A warning popup can block Start Game right after Pre Start (see
# _wait_out_start_game_warning) -- waited out instead of immediately
# treating a missing nav_start_game as "already started".
WARNING_WAIT_TIMEOUT = 10.0
WARNING_POLL_INTERVAL = 1.0

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

# Battle-phase Upgrade/Sell Unit blocks (see _run_battle_blocks_tick):
# selecting a unit needs a beat to actually open its info panel before the
# upgradeable/not_upgradeable search means anything.
BATTLE_BLOCK_CLICK_SETTLE = 0.3
# How long an Upgrade Unit block waits before retrying after finding
# not_upgradeable (not enough gold yet, on cooldown, ...) -- not a failure,
# just not ready, so it keeps its remaining `times` budget and tries again
# later rather than giving up or burning through a poll every second.
UPGRADE_RETRY_WAIT = 5.0

# Auto Upgrade Unit's priority menu (see _run_auto_upgrade_unit_tick):
# right-clicking a selected unit opens a fixed-position context menu with
# Priority 1-6 stacked rows, then a Disable row one more row-height below
# Priority 6.
AUTO_UPGRADE_MENU_CLICK = (345, 453)  # right-click point that opens the menu
AUTO_UPGRADE_PRIORITY_1 = (427, 487)  # Priority 1's row
AUTO_UPGRADE_PRIORITY_ROW_HEIGHT = 34
# Auto Upgrade Unit chains TWO nested UI transitions (select the unit ->
# its info panel opens, right-click -> the priority menu opens on top of
# that) before the priority-row click means anything -- BATTLE_BLOCK_CLICK_
# SETTLE (0.3s, tuned for Upgrade/Sell Unit's single info-panel open) was
# firing the next click before the second transition had actually
# rendered, reported as the whole block just "too fast" to work reliably.
AUTO_UPGRADE_CLICK_SETTLE = 0.6

# Team Loadout application (see _apply_team_loadout) -- H opens the panel,
# then Loadout 1-3 are stacked rows at a fixed position. 4+ exist in
# Creation's picker but aren't reachable yet without scrolling.
TEAM_PANEL_TIMEOUT = 5.0
TEAM_LOADOUT_CLICK_1 = (800, 324)  # Loadout 1's row
TEAM_LOADOUT_ROW_HEIGHT = 126
TEAM_LOADOUT_MAX_SUPPORTED = 8
# Loadouts 4-8 need the list scrolled first -- a click-drag on the list
# itself (not a wheel scroll), same anchor point for both scroll sizes.
# The smaller drag (~1 row height) reveals 4-6 at the SAME row positions
# the 1-3 formula above already computes (the list just shifts to put
# them there); the larger drag re-anchors the list differently, landing 7
# and 8 at their own fixed positions instead of following that formula.
TEAM_LOADOUT_SCROLL_ANCHOR = (895, 285)
TEAM_LOADOUT_SCROLL_SMALL = 124  # reaches Loadout 4-6
TEAM_LOADOUT_SCROLL_LARGE = 300  # reaches Loadout 7-8
TEAM_LOADOUT_SLOT_7_Y = 410
TEAM_LOADOUT_SLOT_8_Y = 536
TEAM_LOADOUT_SCROLL_SETTLE = 0.3  # lets the drag's scroll animation finish before clicking a row

# Wait for Wave (see _run_wait_wave_tick) -- the "<current> / <max> wave"
# HUD badge, in the docked game window's own client coordinates.
WAVE_REGION = (467, 21, 104, 61)
# OCR here is several real Tesseract subprocess spawns (see core.wave/
# core.ocr's multi-mask sweep) -- checked on this cadence, not every single
# Battle-tick poll, so a long wait for a distant wave doesn't spend most of
# its time re-running OCR against a number that hasn't changed yet.
WAIT_WAVE_POLL_INTERVAL = 2.0

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

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def is_paused(self) -> bool:
        return self._pause_event.is_set()

    def start(self, hwnd_getter, get_tasks, scroll_power: int = None, coords: dict = None,
              scroll_nudges: int = None, debug_screenshots: bool = False, default_walk_paths: dict = None,
              reward_region: dict = None, stats_region: dict = None, webhook: dict = None,
              loop_queue: bool = False) -> dict:
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
                  reward_region, stats_region, webhook, bool(loop_queue)),
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
              reward_region: dict = None, stats_region: dict = None, webhook: dict = None,
              loop_queue: bool = False) -> None:
        coords = {**DEFAULT_COORDS, **(coords or {})}
        default_walk_paths = default_walk_paths or {}
        reward_region = reward_region or DEFAULT_REWARD_REGION
        stats_region = stats_region or DEFAULT_STATS_REGION
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

        loop_pass = 1
        while True:
            # Re-read the queue every pass instead of once up front -- with
            # Loop Queue on, a run can sit going for hours, and the user may
            # edit the Task screen (add/remove/reorder) between passes
            # expecting the NEXT pass to pick up their changes rather than
            # keep replaying a stale snapshot from when the run started.
            tasks = get_tasks()
            if not tasks:
                self._log("[Macro] Task queue is empty -- add a task on the Task screen first.")
                self._set_status(action="Idle")
                return

            if loop_pass == 1:
                self._log(f"[Macro] Starting run -- {len(tasks)} task(s) queued.")
            else:
                self._log(f"[Macro] Loop Queue: restarting from task 1 (pass {loop_pass}).")

            for task_index, task in enumerate(tasks, start=1):
                if self._checkpoint(stop_event):
                    return

                map_name = task.get("map")
                if not map_name:
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
                                        scroll_nudges, default_walk_paths, reward_region, stats_region, webhook):
                    self._set_status(action="Idle")
                    return

            if not loop_queue:
                self._log("[Macro] Task queue finished -- all tasks complete.")
                self._set_status(current_task="-", current_repeat="-", map="-", action="Idle")
                return

            if self._checkpoint(stop_event):
                return
            loop_pass += 1

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
            self._set_status(current_task=f"{task_index} / {task_count}", current_repeat=f"1 / {repeat_total}",
                              map=map_name, action="Starting...")

            # Everything from the lobby through the first teleport-in runs
            # ONCE per task -- every repeat after that re-enters the same
            # stage directly via Repeat Stage (see _handle_match_result),
            # skipping the lobby/gamemode/map/stage picks entirely.
            if not self._run_task_setup(hwnd, stop_event, task, mode, map_name, coords, scroll_power,
                                          scroll_nudges, webhook):
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
                                                first_repeat=(repeat_index == 1), webhook=webhook)
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
                    if not self._wait_teleport_in(hwnd, stop_event, webhook, task):
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
        if not self._start_game_or_reset_via_settings(hwnd, stop_event, task.get("play_mode"), webhook, task):
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
        self._wait_out_start_game_warning(hwnd, stop_event)
        if self._checkpoint(stop_event):
            return None
        start_name, start_match = self._find_start_game_button(hwnd)
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
        return self._wait_for_match_result(hwnd, stop_event, battle_blocks, first_repeat, task.get("macro"))

    def _load_battle_blocks(self, task: dict) -> list:
        macro_name = task.get("macro")
        if not macro_name:
            return []
        from . import templates as tpl
        data = tpl.load_template(macro_name)
        blocks = data.get("blocks") or {}
        if isinstance(blocks, list):
            # Oldest flat-list format -- ui/app.js's loadSelectedTemplate()
            # migrates this into prestart/battle client-side the moment you
            # open it in Creation, but never re-saves it to disk on its
            # own -- until you open + Save it again, this stays stuck.
            # Logged here too (already logged by _run_prestart_blocks for
            # Pre Start) so missing Battle blocks is never a silent no-op.
            self._log(f'[Macro] Template "{macro_name}" is saved in an old format -- '
                       f'open it in Creation and Save again to run its Battle blocks.')
            return []
        if "battle" in blocks:
            return blocks.get("battle") or []
        # Three-phase legacy shape (before/during/after, from before Pre
        # Start/Battle existed) -- Battle-eligible content lived in
        # "during"+"after", the same combination ui/app.js's
        # migrateLegacyBlocks() uses when it migrates this shape
        # client-side. _run_prestart_blocks already has an equivalent
        # fallback to "before" for Pre Start; this was the missing half --
        # without it, an unmigrated template's Battle blocks just silently
        # never ran, which is exactly what got reported as "Battle blocks
        # aren't firing."
        legacy_battle = (blocks.get("during") or []) + (blocks.get("after") or [])
        if legacy_battle:
            self._log(f'[Macro] Template "{macro_name}" is saved in an old format -- running its Battle '
                       f'blocks from the legacy during/after lists. Open it in Creation and Save again '
                       f'to migrate it properly.')
        return legacy_battle

    def _wait_for_match_result(self, hwnd, stop_event: threading.Event, battle_blocks: list = None,
                                 first_repeat: bool = True, macro_name: str = None):
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
        self._save_debug_screenshot_unconditional(hwnd, "match_result_timeout")
        return None

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

    def _run_battle_blocks_tick(self, hwnd, stop_event: threading.Event, battle_blocks: list, first_repeat: bool,
                                  macro_name: str = None) -> None:
        """Advances the Battle-phase block list by one step, called once per
        poll of _wait_for_match_result's Victory/Defeat loop instead of
        running the whole list to completion up front -- Upgrade Unit can
        need several separate attempts spread out over the match (see
        _run_upgrade_unit_tick's not_upgradeable/retry handling), so this
        has to interleave with the result check rather than block on it.

        self._battle_block_index/self._battle_block_state (reset once per
        match in _play_one_match) track which block is current and whatever
        per-block progress it's made (e.g. an Upgrade block's remaining
        `times` budget and next-retry time) across calls.
        """
        while self._battle_block_index < len(battle_blocks):
            block = battle_blocks[self._battle_block_index]
            if block.get("once") and not first_repeat:
                self._log(f'[Macro] Skipping Battle block #{self._battle_block_index + 1} -- '
                           f'marked "Once" and this isn\'t the first repeat.')
                self._battle_block_index += 1
                self._battle_block_state = {}
                continue

            btype = block.get("type")
            if btype == "upgrade_unit":
                done = self._run_upgrade_unit_tick(hwnd, stop_event, block, self._battle_block_index + 1)
            elif btype == "sell_unit":
                done = self._run_sell_unit_tick(hwnd, stop_event, block, self._battle_block_index + 1)
                self._battle_block_state = {}
            elif btype == "auto_upgrade_unit":
                done = self._run_auto_upgrade_unit_tick(hwnd, stop_event, block, self._battle_block_index + 1)
                self._battle_block_state = {}
            elif btype == "place_unit":
                # Mid-battle placement (a reinforcement dropped in later,
                # not a Pre Start starter) -- same click/verify/nudge-retry
                # logic Pre Start uses, one-shot like Sell Unit. Continues
                # the SAME #ordinal count Pre Start's place_unit blocks left
                # off at, matching ui/app.js's listPlacedUnits() (which
                # numbers place_unit blocks across both phases as one list),
                # so Upgrade/Sell/Auto Upgrade Unit blocks targeting a
                # unit placed here by #index still resolve correctly.
                self._last_unit_ordinal += 1
                left, top, _, _ = wm.get_window_rect_screen(hwnd)
                self._run_place_unit_block(hwnd, stop_event, left, top, block, self._battle_block_index + 1,
                                             macro_name, self._last_unit_ordinal)
                done = True
                self._battle_block_state = {}
            elif btype == "wait_ms":
                self._run_wait_ms_tick(stop_event, block, self._battle_block_index + 1)
                done = True
                self._battle_block_state = {}
            elif btype == "walk":
                self._run_walk_block_tick(stop_event, block, self._battle_block_index + 1)
                done = True
                self._battle_block_state = {}
            elif btype == "wait_wave":
                done = self._run_wait_wave_tick(hwnd, block, self._battle_block_index + 1)
            elif btype == "setting_change":
                self._run_setting_block(hwnd, stop_event, block, self._battle_block_index + 1)
                done = True
                self._battle_block_state = {}
            else:
                self._log(f'[Macro] Skipping Battle block #{self._battle_block_index + 1} '
                           f'("{btype}") -- not runnable in Battle yet.')
                done = True
                self._battle_block_state = {}

            if done:
                self._battle_block_index += 1
                self._battle_block_state = {}
            # Not done (an Upgrade block still has budget left, or is
            # waiting out its retry cooldown) -- stay on this same block and
            # pick back up here on the next poll tick, rather than blocking
            # the whole loop (and the Victory/Defeat check) on it now.
            return

    def _placed_unit_click_point(self, block: dict, label: str):
        index = block.get("params", {}).get("index")
        try:
            index = int(index)
        except (TypeError, ValueError):
            self._log(f'[Macro] {label}: no unit selected -- skipping.')
            return None
        pos = self._placed_unit_positions.get(index)
        if pos is None:
            self._log(f'[Macro] {label}: unit #{index} was never placed this match (or Pre Start hasn\'t '
                       f'placed it yet) -- skipping.')
            return None
        return pos

    def _run_upgrade_unit_tick(self, hwnd, stop_event: threading.Event, block: dict, block_num: int) -> bool:
        """One attempt: click the unit, look for upgradeable/not_upgradeable.
        Returns True once this block is DONE (times budget used up, or the
        unit/position couldn't be resolved at all) -- False means try again
        later (see UPGRADE_RETRY_WAIT), still holding this block's spot in
        _run_battle_blocks_tick's loop."""
        label = f'Battle block #{block_num} (Upgrade Unit)'
        state = self._battle_block_state
        if "remaining" not in state:
            try:
                state["remaining"] = max(1, int(block.get("params", {}).get("times") or 1))
            except (TypeError, ValueError):
                state["remaining"] = 1
            state["next_attempt"] = 0.0

        if time.time() < state["next_attempt"]:
            return False  # still waiting out the retry cooldown from a previous not_upgradeable

        pos = self._placed_unit_click_point(block, label)
        if pos is None:
            return True

        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        self._mouse.click(left + UNIT_INFO_RESET_CLICK[0], top + UNIT_INFO_RESET_CLICK[1])
        time.sleep(0.1)

        self._set_status(action=f"Upgrading unit ({state['remaining']} left)...")
        self._mouse.click(left + pos[0], top + pos[1])
        time.sleep(BATTLE_BLOCK_CLICK_SETTLE)
        if self._checkpoint(stop_event):
            return True

        try:
            upgrade_match = vision.find_image(hwnd, "upgradeable")
        except vision.TemplateNotFound:
            upgrade_match = None
        if upgrade_match is not None:
            self._log(f'{label}: found Upgradeable (score {upgrade_match["score"]:.2f}) -- clicking it '
                       f'({state["remaining"]} left after this).')
            vision.click_match(self._mouse, hwnd, upgrade_match)
            time.sleep(BATTLE_BLOCK_CLICK_SETTLE)
            if self._checkpoint(stop_event):
                return True
            # Reset click, same corner as before selecting the unit -- closes
            # the info panel the upgrade click left open, so the next thing
            # that runs (another attempt on this same unit, or whatever
            # Battle block comes after it) doesn't have to fight a leftover
            # panel/tooltip still covering the screen.
            self._mouse.click(left + UNIT_INFO_RESET_CLICK[0], top + UNIT_INFO_RESET_CLICK[1])
            state["remaining"] -= 1
            state["next_attempt"] = 0.0
            return state["remaining"] <= 0

        try:
            not_upgrade_match = vision.find_image(hwnd, "not_upgradeable")
        except vision.TemplateNotFound:
            not_upgrade_match = None
        if not_upgrade_match is not None:
            self._log(f'{label}: not upgradeable yet (score {not_upgrade_match["score"]:.2f}) -- '
                       f'waiting {UPGRADE_RETRY_WAIT:.0f}s and retrying.')
        else:
            self._log(f'{label}: neither Upgradeable nor Not Upgradeable found -- '
                       f'waiting {UPGRADE_RETRY_WAIT:.0f}s and retrying.')
        state["next_attempt"] = time.time() + UPGRADE_RETRY_WAIT
        return False

    def _run_sell_unit_tick(self, hwnd, stop_event: threading.Event, block: dict, block_num: int) -> bool:
        """One-shot: click the unit, press X. Always "done" after one try --
        no retry/budget concept like Upgrade Unit has."""
        label = f'Battle block #{block_num} (Sell Unit)'
        pos = self._placed_unit_click_point(block, label)
        if pos is None:
            return True

        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        self._mouse.click(left + UNIT_INFO_RESET_CLICK[0], top + UNIT_INFO_RESET_CLICK[1])
        time.sleep(0.1)

        self._set_status(action="Selling unit...")
        self._mouse.click(left + pos[0], top + pos[1])
        time.sleep(BATTLE_BLOCK_CLICK_SETTLE)
        if self._checkpoint(stop_event):
            return True

        self._log(f'{label}: clicked unit at {pos} -- pressing X to sell.')
        self._keyboard.tap(ord("X"))
        return True

    def _run_wait_ms_tick(self, stop_event: threading.Event, block: dict, block_num: int) -> None:
        """Just waits -- no unit/click involved. Slept in small chunks
        (checking _checkpoint between each) rather than one bare
        time.sleep(), so Pause/Stop still cuts in promptly during a long
        configured wait instead of having to sit through the whole thing."""
        try:
            ms = int(block.get("params", {}).get("ms") or 0)
        except (TypeError, ValueError):
            ms = 0
        ms = max(0, ms)
        self._log(f'Battle block #{block_num} (Wait): waiting {ms}ms.')
        self._set_status(action=f"Waiting {ms}ms...")
        deadline = time.time() + ms / 1000.0
        while time.time() < deadline:
            if self._checkpoint(stop_event):
                return
            time.sleep(min(0.1, deadline - time.time()))

    def _run_walk_block_tick(self, stop_event: threading.Event, block: dict, block_num: int) -> None:
        """One-shot: replays a recorded walk path -- the same core.paths
        record/load/replay system the pinned Pre Start Walk Path row
        already uses (see _run_prestart), just picked by name here instead
        of by map. Picks up wherever the player currently is; no position
        tracking needed, same as every other Battle block that just fires
        an action rather than needing to know where a unit was placed."""
        path_name = block.get("params", {}).get("path") or ""
        label = f'Battle block #{block_num} (Walk)'
        if not path_name:
            self._log(f'{label}: no path selected -- skipping.')
            return
        self._log(f'{label}: walking path "{path_name}"...')
        self._set_status(action=f'Walking "{path_name}"...')
        data = walk_paths.load_path(path_name)
        events = data.get("events", [])
        if not events:
            self._log(f'{label}: path "{path_name}" has no recorded movement -- skipping.')
            return
        walk_paths.replay_events(events, self._keyboard, stop_event)
        self._log(f'{label}: walk finished.')

    def _run_wait_wave_tick(self, hwnd, block: dict, block_num: int) -> bool:
        """Waits until the current wave has reached OR already passed the
        configured target -- not exact equality, so a wave that ticks over
        between polls (or was already past target the first time this is
        checked) still counts as done instead of waiting forever for a
        number that will never be read again. Checked periodically (see
        WAIT_WAVE_POLL_INTERVAL), not every single Battle-tick poll --
        each OCR read is several real Tesseract subprocess spawns.
        Returns True once done (target reached/passed, or the block's own
        target can't be resolved at all); False to keep waiting.
        """
        label = f'Battle block #{block_num} (Wait for Wave)'
        state = self._battle_block_state
        try:
            target = int(block.get("params", {}).get("wave") or 1)
        except (TypeError, ValueError):
            self._log(f'{label}: no target wave set -- skipping.')
            return True

        if "next_check" not in state:
            state["next_check"] = 0.0
        if time.time() < state["next_check"]:
            return False

        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        try:
            from core.ocr import capture_region
            from core import wave as wave_module
            image = capture_region(left + WAVE_REGION[0], top + WAVE_REGION[1], WAVE_REGION[2], WAVE_REGION[3])
            current, maximum = wave_module.read_wave(image)
        except Exception as exc:
            self._log(f'{label}: OCR failed ({exc}) -- retrying in {WAIT_WAVE_POLL_INTERVAL:.0f}s.')
            state["next_check"] = time.time() + WAIT_WAVE_POLL_INTERVAL
            return False

        if current is None:
            self._log(f"{label}: couldn't read the wave counter -- retrying in {WAIT_WAVE_POLL_INTERVAL:.0f}s.")
            state["next_check"] = time.time() + WAIT_WAVE_POLL_INTERVAL
            return False

        if current >= target:
            self._log(f'{label}: wave {current}/{maximum} -- reached (or already past) target {target}.')
            return True

        self._log(f'{label}: wave {current}/{maximum}, waiting for {target}.')
        self._set_status(action=f"Waiting for wave {target} (currently {current})...")
        state["next_check"] = time.time() + WAIT_WAVE_POLL_INTERVAL
        return False

    def _run_auto_upgrade_unit_tick(self, hwnd, stop_event: threading.Event, block: dict, block_num: int) -> bool:
        """One-shot: click the unit, right-click to open its priority menu,
        click the configured priority row (or Disable for "None"), then a
        reset click. Always "done" after one try -- setting a priority
        isn't a repeated action the way Upgrade Unit's clicks are."""
        label = f'Battle block #{block_num} (Auto Upgrade Unit)'
        pos = self._placed_unit_click_point(block, label)
        if pos is None:
            return True

        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        self._set_status(action="Setting auto-upgrade priority...")
        self._mouse.click(left + pos[0], top + pos[1])
        time.sleep(AUTO_UPGRADE_CLICK_SETTLE)
        if self._checkpoint(stop_event):
            return True

        self._mouse.click(left + AUTO_UPGRADE_MENU_CLICK[0], top + AUTO_UPGRADE_MENU_CLICK[1], button="right")
        time.sleep(AUTO_UPGRADE_CLICK_SETTLE)
        if self._checkpoint(stop_event):
            return True

        priority = str(block.get("params", {}).get("priority") or "None")
        if priority == "None":
            # The Disable row sits one row-height below Priority 6 -- the
            # last of the 6 priority rows, not a 7th priority.
            row_index = 6
            self._log(f'{label}: disabling auto-upgrade for this unit.')
        else:
            try:
                row_index = int(priority) - 1
            except ValueError:
                row_index = 0
            self._log(f'{label}: setting priority {priority}.')
        row_y = AUTO_UPGRADE_PRIORITY_1[1] + row_index * AUTO_UPGRADE_PRIORITY_ROW_HEIGHT
        self._mouse.click(left + AUTO_UPGRADE_PRIORITY_1[0], top + row_y)
        time.sleep(AUTO_UPGRADE_CLICK_SETTLE)
        if self._checkpoint(stop_event):
            return True

        self._mouse.click(left + UNIT_INFO_RESET_CLICK[0], top + UNIT_INFO_RESET_CLICK[1])
        return True

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
            if not self._click_and_verify_gone(hwnd, stop_event, "repeat_stage", NAV_CLICK_TIMEOUT):
                self._log('[Macro] "Repeat Stage" not found -- can\'t continue this task\'s repeats, stopping.')
                return False
            return True

        # Last repeat of this task (or the whole queue) -- back out to the
        # lobby so the next task's setup (or a clean stop) starts from a
        # known state instead of sitting on the result screen. Verified/
        # retried, not a one-shot click -- a dropped click here used to
        # just leave the run sitting on the result screen forever with
        # nothing else ever noticing or retrying.
        self._set_status(action=f"{label} -- clicking Leave Stage...")
        if not self._click_and_verify_gone(hwnd, stop_event, "leave_stage", NAV_CLICK_TIMEOUT):
            self._log('[Macro] "Leave Stage" not found -- stopping.')
            return False
        return True

    def _finish_match_result_background(self, stats_image, reward_images, result: str, map_name: str,
                                          duration: str, task: dict, webhook: dict) -> None:
        stats = self._ocr_game_stats(stats_image)
        items = []
        if reward_images is not None:
            names, amounts = self._log_expected_rewards(task)
            items = self._ocr_reward_items(*reward_images, allowed_names=names, amounts=amounts)
        self._record_result(result, map_name, duration, stats, items)
        self._send_result_webhook(webhook, result, task, duration, stats, items)

    def _log_expected_rewards(self, task: dict) -> tuple:
        # Reference logged right before the actual reward read so they're
        # easy to eyeball against each other -- scraped from the wiki's own
        # data (see tools/fetch_stage_data.py). The returned name list AND
        # amounts dict both feed into the actual read (core.rewards.
        # read_reward_grid): names narrow icon identification down to what
        # this stage can actually reward, and amounts is the lookup table
        # that replaced OCRing each reward's quantity badge entirely -- not
        # just a passive log line, this stage data now IS how quantities
        # get reported. Returns (names, amounts), both possibly empty/None
        # if stage_data.json has nothing for this map/stage/difficulty.
        try:
            from . import stage_data
            map_name, stage, difficulty = task.get("map"), task.get("stage") or "1", task.get("difficulty") or "Normal"
            expected = stage_data.expected_rewards(map_name, stage, difficulty)
            names = stage_data.expected_item_names(map_name, stage, difficulty)
            amounts = stage_data.expected_item_amounts(map_name, stage, difficulty)
        except Exception:
            return None, None
        if expected:
            self._log(f"[Macro] Expected reward for this stage: {', '.join(expected)}")
        if names:
            try:
                from . import rewards
                rewards._ensure_wiki_icons_for(names)
            except Exception as exc:
                self._log(f"[Macro] Couldn't fetch wiki icons for this stage's rewards: {exc}")
        return names or None, amounts or None

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

    def _ocr_reward_items(self, image_top, image_bottom, allowed_names: list = None,
                            amounts: dict = None) -> list:
        try:
            from core import rewards
            pages = [rewards.read_reward_grid(image_top, allowed_names=allowed_names, amounts=amounts)]
            if image_bottom is not None:
                pages.append(rewards.read_reward_grid(image_bottom, allowed_names=allowed_names, amounts=amounts))
            items = rewards.merge_reward_pages(*pages)
        except Exception as exc:
            self._log(f"[Macro] Couldn't read rewards: {exc}")
            return []

        if not items:
            self._log("[Macro] No reward icons identified -- check the region in Settings > Debug.")
        for item in items:
            self._log(f"[Macro] Reward: {item.get('quantity') or '?'} {item.get('name') or '(unidentified)'}")
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
        mode = task.get("mode") or "story"
        # Raid and Infinite/Mastery stages are locked to Hard in-game (see
        # _run_task_setup's identical check) -- the task's own difficulty
        # setting never actually applies there, so reporting it verbatim
        # was showing e.g. "Normal" for a run that was really Hard.
        if mode == "raid" or stage in SPECIAL_STAGES_NO_DIFFICULTY:
            difficulty = "Hard"
        else:
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
            result = webhook_module.send(url, embed, content=content, silent=bool(webhook.get("silent")))
        except Exception as exc:
            self._log(f"[Macro] Webhook send failed: {exc}")
            return
        if result["ok"]:
            self._log("[Macro] Webhook sent.")
        else:
            self._log(f"[Macro] Webhook send failed: {result['reason']}")

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

        # Team Loadout only makes sense the FIRST time a task enters a stage --
        # it sets up units/equipment before the match even starts, so
        # re-applying it on every repeat (this used to run unconditionally,
        # same bug the walk below already had a fix for) was pointlessly
        # re-pressing H and re-picking a loadout mid-repeat-cycle, which
        # could land on the wrong screen entirely if the panel wasn't in the
        # exact state it expects and produce exactly the kind of "it bugs
        # out" behavior this was reported as.
        if first_repeat:
            self._apply_team_loadout(hwnd, stop_event, task)
        else:
            self._log("[Macro] Repeat of the same stage -- skipping Team Loadout (already applied on entry).")
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

    def _apply_team_loadout(self, hwnd, stop_event: threading.Event, task: dict) -> None:
        """Presses H to open the team-select panel, waits for it to
        actually open, clicks the task's Macro Operation template's
        configured Team Loadout slot (1-8 in Creation's picker, though only
        1-3 are positioned here -- 4+ need a scroll method not implemented
        yet), clicks Confirm, picks Include/Exclude for equipment, then
        presses H again to close the panel. Best-effort like every other
        Pre Start step: no team set, an out-of-range slot, or any of the
        expected images never showing up all just skip (the rest of the
        sequence included) with a log line instead of failing the run."""
        macro_name = task.get("macro")
        if not macro_name:
            return
        from . import templates as tpl
        data = tpl.load_template(macro_name)
        blocks = data.get("blocks") or {}
        if isinstance(blocks, list):
            return  # old-format template -- same as _run_prestart_blocks
        team = blocks.get("team") or ""
        if not team:
            return
        equipment = blocks.get("equipment") if blocks.get("equipment") in ("include", "exclude") else "include"

        try:
            team_num = int(team)
        except (TypeError, ValueError):
            self._log(f'[Macro] Team Loadout "{team}" isn\'t a recognized slot number -- skipping.')
            return
        if not (1 <= team_num <= TEAM_LOADOUT_MAX_SUPPORTED):
            self._log(f'[Macro] Team Loadout {team_num} needs scrolling to reach (only 1-'
                       f'{TEAM_LOADOUT_MAX_SUPPORTED} are positioned so far) -- skipping.')
            return

        self._log(f"[Macro] Applying Team Loadout {team_num} (equipment: {equipment})...")
        self._set_status(action=f"Applying Team Loadout {team_num}...")
        self._keyboard.tap(ord("H"))

        try:
            team_match = vision.wait_for_image(hwnd, "team", timeout=TEAM_PANEL_TIMEOUT, stop_event=stop_event)
        except vision.TemplateNotFound as exc:
            self._log(f"[Macro] Can't confirm the team panel opened: {exc}")
            return
        if team_match is None:
            if not stop_event.is_set():
                self._log('[Macro] Team panel never opened (no "team" match) -- skipping.')
            return
        vision.click_match(self._mouse, hwnd, team_match)
        # The Loadout list animates in right after this click -- without a
        # settle, the very next click (the Loadout row itself) can land
        # before it's actually finished sliding into place.
        time.sleep(SETTLE_DELAY)
        if self._checkpoint(stop_event):
            return

        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        if team_num > 3:
            # A click-drag on the list itself, not a wheel scroll -- 7/8
            # need a bigger drag that re-anchors the list differently
            # (their own fixed row positions below), while 4-6's smaller
            # drag just shifts the list enough to put them at the same row
            # positions the 1-3 formula already computes.
            scroll_amount = TEAM_LOADOUT_SCROLL_LARGE if team_num >= 7 else TEAM_LOADOUT_SCROLL_SMALL
            anchor_x = left + TEAM_LOADOUT_SCROLL_ANCHOR[0]
            anchor_y = top + TEAM_LOADOUT_SCROLL_ANCHOR[1]
            self._log(f"[Macro] Scrolling the Loadout list to reach {team_num}...")
            self._mouse.drag(anchor_x, anchor_y, anchor_x, anchor_y + scroll_amount)
            time.sleep(TEAM_LOADOUT_SCROLL_SETTLE)
            if self._checkpoint(stop_event):
                return

        if team_num == 7:
            row_y = TEAM_LOADOUT_SLOT_7_Y
        elif team_num == 8:
            row_y = TEAM_LOADOUT_SLOT_8_Y
        else:
            row_y = TEAM_LOADOUT_CLICK_1[1] + (team_num - 1) * TEAM_LOADOUT_ROW_HEIGHT
        self._mouse.click(left + TEAM_LOADOUT_CLICK_1[0], top + row_y)
        self._log(f"[Macro] Clicked Loadout {team_num}.")
        if self._checkpoint(stop_event):
            return

        try:
            confirm_match = vision.wait_for_image(hwnd, "confirm", timeout=TEAM_PANEL_TIMEOUT, stop_event=stop_event)
        except vision.TemplateNotFound as exc:
            self._log(f"[Macro] Can't confirm the Loadout Confirm button appeared: {exc}")
            return
        if confirm_match is None:
            if not stop_event.is_set():
                self._log('[Macro] Confirm button never showed up -- stopping Team Loadout here.')
            return
        vision.click_match(self._mouse, hwnd, confirm_match)
        self._log("[Macro] Clicked Confirm.")
        if self._checkpoint(stop_event):
            return

        # Whichever of include.png/exclude.png matches the configured
        # choice -- optional like nav_disband and friends: if that specific
        # image hasn't been added yet, this just logs and moves on to
        # closing the panel instead of failing the whole sequence over it.
        try:
            equip_match = vision.wait_for_image(hwnd, equipment, timeout=TEAM_PANEL_TIMEOUT, stop_event=stop_event)
        except vision.TemplateNotFound:
            equip_match = None
            self._log(f'[Macro] No Assets/ui/{equipment}.png yet -- skipping the equipment choice.')
        if equip_match is not None:
            vision.click_match(self._mouse, hwnd, equip_match)
            self._log(f"[Macro] Equipment: {equipment}.")
        elif not stop_event.is_set():
            self._log(f'[Macro] "{equipment}" option never showed up -- skipping the equipment choice.')
        if self._checkpoint(stop_event):
            return

        self._keyboard.tap(ord("H"))
        self._log("[Macro] Closed the Team Loadout panel.")

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
        # Separate from the generic #i below -- this only counts place_unit
        # blocks, matching ui/app.js's listPlacedUnits() numbering (the #1,
        # #2, ... the Upgrade/Sell Unit pickers show), so a template mixing
        # place_unit and setting_change blocks still numbers its units the
        # same way the UI does. self._last_unit_ordinal (not a local var)
        # since Battle-phase place_unit blocks (see _run_battle_blocks_tick)
        # continue this same count after Pre Start's blocks are done.
        self._last_unit_ordinal = 0
        for i, block in enumerate(prestart_blocks, start=1):
            if self._checkpoint(stop_event):
                return
            if block.get("type") == "place_unit":
                self._last_unit_ordinal += 1
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
                self._run_place_unit_block(hwnd, stop_event, left, top, block, i, macro_name, self._last_unit_ordinal)
            elif btype == "setting_change":
                self._run_setting_block(hwnd, stop_event, block, i)
            elif btype == "auto_upgrade_unit":
                self._run_auto_upgrade_unit_tick(hwnd, stop_event, block, i)
            else:
                self._log(f'[Macro] Skipping block #{i} ("{btype}") -- not runnable in Pre Start yet.')
            time.sleep(0.2)  # brief gap between blocks so the game UI can settle

    def _run_place_unit_block(self, hwnd, stop_event: threading.Event, left: int, top: int, block: dict,
                                index: int, macro_name: str, unit_ordinal: int = None) -> None:
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
        if unit_ordinal is not None:
            self._placed_unit_positions[unit_ordinal] = (cur_x, cur_y)
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

    # Windows/Meta-style keys are blocked from the Setting block's custom
    # hotkey box -- letting a macro send these could minimize the game,
    # open the Start menu, or otherwise yank focus/input away from Roblox
    # entirely, which no in-game "setting" should ever be able to do.
    _BLACKLISTED_KEY_NAMES = {"win", "meta", "windows", "lwin", "rwin", "super", "cmd", "command"}
    _CUSTOM_KEY_DEFAULT_HOLD_MS = 500

    def _parse_custom_key_spec(self, text: str):
        """Parses a Setting block's custom-key text box (see
        _run_setting_block's "hotkey" kind) into (vk, hold_seconds).
        Supported syntax: "w" (a plain tap), "hold w" (held for
        _CUSTOM_KEY_DEFAULT_HOLD_MS), "hold w 800ms" (held for an explicit
        duration). Returns None for empty/blacklisted/unrecognized input so
        a bad spec is a logged skip, never a crash mid-run."""
        text = (text or "").strip().lower()
        if not text:
            return None
        parts = text.split()

        hold_seconds = None
        if parts[0] == "hold" and len(parts) >= 2:
            key_name = parts[1]
            hold_seconds = self._CUSTOM_KEY_DEFAULT_HOLD_MS / 1000.0
            if len(parts) >= 3 and parts[2].endswith("ms"):
                try:
                    hold_seconds = int(parts[2][:-2]) / 1000.0
                except ValueError:
                    pass  # keep the default rather than fail the whole spec over a bad number
        else:
            key_name = parts[0]

        if key_name in self._BLACKLISTED_KEY_NAMES:
            return None
        vk = keys.key_name_to_vk(key_name)
        if vk is None:
            return None
        return (vk, hold_seconds)

    def _run_setting_block(self, hwnd, stop_event: threading.Event, block: dict, index: int) -> None:
        name = (block.get("params") or {}).get("name") or f"#{index}"
        kind = block.get("kind")
        value = block.get("value")

        if kind == "toggle":
            desired_on = str(value).lower() in ("on", "true", "1", "yes")
            self._set_status(action=f'Setting "{name}"...')
            search_box_pos = self._open_settings_search(hwnd, stop_event)
            if search_box_pos is None:
                self._log(f'[Macro] Setting "{name}": couldn\'t open Settings -- skipping.')
                return
            if self._checkpoint(stop_event):
                return
            self._search_and_set_toggle(hwnd, stop_event, search_box_pos, name, desired_on)
            if self._checkpoint(stop_event):
                return
            self._close_settings_if_open(hwnd, stop_event)
            return

        if kind == "hotkey":
            parsed = self._parse_custom_key_spec(value)
            if parsed is None:
                self._log(f'[Macro] Setting "{name}": custom key "{value}" is blacklisted or unrecognized -- '
                           f'skipping.')
                return
            vk, hold_seconds = parsed
            self._set_status(action=f'Setting "{name}"...')
            if hold_seconds is not None:
                self._log(f'[Macro] Setting "{name}": holding "{value}" for {hold_seconds * 1000:.0f}ms.')
                self._keyboard.tap(vk, hold=hold_seconds)
            else:
                self._log(f'[Macro] Setting "{name}": pressing "{value}".')
                self._keyboard.tap(vk)
            return

        self._log(f'[Macro] Setting "{name}" ({kind or "?"}) -- unsupported kind, skipping.')

    def _wait_teleport_in(self, hwnd, stop_event: threading.Event, webhook: dict = None,
                            task: dict = None, timeout: float = None) -> bool:
        # nav_unitmanager only renders once you're actually in the match (not
        # during the loading/teleport transition), so waiting for it is the
        # confirmation the teleport actually finished.
        timeout = TELEPORT_IN_TIMEOUT if timeout is None else timeout
        self._log("[Macro] Waiting to teleport in-game...")
        self._set_status(action="Waiting to teleport in-game...")
        result = self._wait_for_teleport_or_stuck(hwnd, stop_event, timeout)
        if result == "ok":
            self._log("[Macro] Teleported in-game.")
            return True
        if result in ("stuck", "disconnected"):
            self._handle_disconnect(hwnd, stop_event, webhook, task, result)
            return False
        if result == "timeout" and not stop_event.is_set():
            self._log("[Macro] Never teleported in-game (Unit Manager not found) -- stopping.")
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
                match = vision.find_image(current_hwnd, "nav_play", region=NAV_PLAY_REGION)
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

    def _click_and_verify_gone(self, hwnd, stop_event: threading.Event, name: str, timeout: float,
                                 retry_attempts: int = 3, verify_settle: float = 1.0) -> bool:
        """Like _click_found_image, but re-checks the button actually
        disappeared afterward and re-clicks (with a focus reassert) if it's
        still there, up to retry_attempts times -- same "click found it but
        Roblox never got it" flakiness the Play/Start Game clicks needed
        this same treatment for. Used for Leave Stage/Repeat Stage: a
        dropped click here leaves the whole run just sitting on the result
        screen indefinitely, since nothing else would ever notice or retry
        on its own. Returns whether the button was found at all -- not
        whether it definitely disappeared, since after retry_attempts a
        stuck button falls through to the caller's own recovery path rather
        than being treated as "never found in the first place"."""
        match = None
        for attempt in range(1, retry_attempts + 1):
            try:
                match = vision.wait_for_image(hwnd, name, timeout=timeout, stop_event=stop_event)
            except vision.TemplateNotFound as exc:
                self._log(f"[Macro] {exc}")
                return False
            if match is None:
                if stop_event is None or not stop_event.is_set():
                    self._log(f'[Macro] Couldn\'t find "{name}" -- stopping.')
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

            try:
                still_there = vision.find_image(hwnd, name)
            except vision.TemplateNotFound:
                still_there = None
            if still_there is None:
                return True
            if attempt == retry_attempts:
                self._log(f'[Macro] "{name}" still showing after {retry_attempts} clicks -- continuing anyway.')
        return True

    def _click_start_game_2_if_found(self, hwnd) -> bool:
        # nav_start_game_2 -- a second Start Game/confirm button that can
        # show up alongside the warning itself (e.g. a "Start Anyway"
        # prompt) -- clicking it skips the wait in
        # _wait_out_start_game_warning entirely instead of sitting through
        # the full timeout for a warning that's actually already
        # dismissable right now.
        try:
            skip_match = vision.find_image(hwnd, "nav_start_game_2")
        except vision.TemplateNotFound:
            return False
        if skip_match is None:
            return False
        self._log(f"[Macro] Found nav_start_game_2 (score {skip_match['score']:.2f}) -- "
                   f"clicking it to skip the warning wait.")
        vision.click_match(self._mouse, hwnd, skip_match)
        return True

    def _find_start_game_button(self, hwnd):
        """Tries nav_start_game, then nav_start_game_2, then
        nav_start_game_3 in order -- different visual variants of the same
        button seen in practice, so the actual "start the round" click
        (see _play_one_match) isn't dependent on just one of them matching.
        Returns (name, match) for whichever was found first, or (None,
        None) if none of them were -- missing/not-yet-added variants are
        skipped silently, same as any other optional template."""
        for name in ("nav_start_game", "nav_start_game_2", "nav_start_game_3"):
            try:
                match = vision.find_image(hwnd, name)
            except vision.TemplateNotFound:
                continue
            if match is not None:
                return name, match
        return None, None

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
        self._log("[Macro] Warning didn't clear (or Start Game didn't show up) in time -- continuing anyway.")

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

    def _start_game_or_reset_via_settings(self, hwnd, stop_event: threading.Event, play_mode: str = "solo",
                                            webhook: dict = None, task: dict = None) -> bool:
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

        self._log('[Macro] No Start Game button found -- likely because Auto Vote Start is enabled '
                   '(it replaces the manual Start button with an auto-starting vote) rather than not '
                   "being the party leader. Please disable Auto Vote Start in Settings if this keeps "
                   "happening -- checking/disabling it now so the round doesn't start before Pre Start runs.")
        self._set_status(action="Opening Settings for Auto Vote Start...")
        search_box_pos = self._open_settings_search(hwnd, stop_event)
        if search_box_pos is None:
            return False
        if self._checkpoint(stop_event):
            return False

        self._search_and_set_toggle(hwnd, stop_event, search_box_pos, "Auto Vote Start", desired_on=False)
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

        screenshot_path = self._save_debug_screenshot_unconditional(hwnd, "restart_game")
        self._send_event_webhook(
            webhook, task, "Restarting Game",
            "No Start Game button found (likely Auto Vote Start) -- restarting the game via Settings.",
            0xE8935A, screenshot_path)

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
            match = None
            for attempt in range(1, PLAY_CLICK_RETRY_ATTEMPTS + 1):
                self._log("[Macro] Waiting for the gamemode menu to open..." if attempt == 1 else
                           f"[Macro] Still on the lobby -- re-clicking Play (attempt {attempt}/"
                           f"{PLAY_CLICK_RETRY_ATTEMPTS})...")
                self._set_status(action="Waiting for gamemode menu...")
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
                    self._log(f"[Macro] Gamemode menu never opened after {PLAY_CLICK_RETRY_ATTEMPTS} attempt(s) "
                               f"(no Back button found) -- stopping.")
                    self._save_debug_screenshot_unconditional(hwnd, "gamemode_menu_timeout")
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
