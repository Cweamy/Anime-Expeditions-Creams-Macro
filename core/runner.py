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
START_GAME_BUTTON_WAIT_TIMEOUT = 5.0  # how long to poll for Start Game right after Pre Start hands off
EXPEDITION_WAVE_TIMEOUT = 8.0  # how long to wait for Continue_2/extract after clicking exp_continue/exp_extract
# A level-up "Select an upgrade!" reward modal can be on screen at the exact
# same moment as the extract/continue choice (confirmed via a real capture:
# vision_exp_extract.png caught both up at once), auto-selecting on its own
# after ~12s -- covering/intercepting the extract click until it clears.
# EXPEDITION_WAVE_TIMEOUT alone (8s) isn't enough to wait that out, so
# "extract" specifically gets a longer allowance; exp_extract itself doesn't
# go anywhere in the meantime (see _check_expedition_wave_result), so this
# just avoids burning a couple of whole retry cycles on the same modal.
EXPEDITION_EXTRACT_CONFIRM_TIMEOUT = 16.0
EXTRACT_CONFIRM_SETTLE = 5.0  # settle after clicking "extract" -- reported as a click that can visually land without registering
EXPEDITION_CONTINUE_COOLDOWN = 5.0  # settle after exp_continue/continue_2 -- a lingering banner right after the

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

# Expedition has no stage-row picker like Story/Raid -- just a map (School
# Grounds is whatever's selected by default when the screen opens, so it has
# no reference image at all; Flower Forest/Rose Kingdom are each picked by
# image search, see EXPEDITION_MAP_IMAGES) and a difficulty stepper: one "+"
# button at a fixed spot that increments the level by 1 per click, starting
# from 1. Difficulty "2" is one click, "3" is two, "1" is none.
EXPEDITION_MAP_IMAGES = {
    "Flower Forest": "expedition_flower_forest",
    "Rose Kingdom": "expedition_rose_kingdom",
}
# Regular Challenge is Story's own flow, just with the game picking a
# random one of these 5 maps for you instead of you picking it -- so
# there's no map-select step to skip past, only a "which map did it land
# on" check once you're in. Reference images live in Assets/ui/<map>.png
# (a different folder/purpose than Assets/maps/<map>.png, which is the
# scrolling map-CARD search used to pick a map by hand -- these instead
# confirm which map is already showing). Mirrors main.py's
# CHALLENGE_STORY_MAPS and ui/app.js's TASK_DATA.story.maps.
CHALLENGE_STORY_MAPS = ["School Grounds", "Rose Kingdom", "Fairy King Forest", "King's Tomb", "Flower Forest"]
# Mirrors main.py's CHALLENGE_STAGE_SLOTS.
CHALLENGE_STAGE_SLOTS = ["1", "2", "3"]
# Fixed click points for the 3 Regular Challenge stage rows -- no image
# search needed, same idea as Story's STAGE_CLICK_BASE.
CHALLENGE_STAGE_CLICK = {"1": (460, 277), "2": (460, 400), "3": (460, 533)}
CHALLENGE_SCREEN_TIMEOUT = 10.0  # how long to wait for challenge_loaded after clicking the Challenge card
CHALLENGE_MAP_DETECT_TIMEOUT = 20.0  # how long to poll for a recognizable map after teleporting in

EXPEDITION_DIFFICULTY_CLICK = (1094, 456)
EXPEDITION_DIFFICULTY_CLICK_DELAY = 0.1  # lets each increment register before the next click

# Clicking the stage row (or the map, for Expedition) fires an animation on
# the difficulty picker that immediately clicking it can outrun -- the click
# lands before the panel/toggle has actually settled into place.
DIFFICULTY_CLICK_DELAY = 1.0

RETURN_TO_LOBBY_CHECK_TIMEOUT = 2.5  # how long to poll for the "Return to Lobby" confirmation after Leave Stage

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
# reconnect.png/reconnect_2.png/reconnect_3.png/retry.png (Roblox's own
# disconnect prompt) are a DEFINITE signal on their own -- no continuous-
# visibility wait needed, unlike teleportstuck's spinner which can be a
# false alarm for a moment.
TELEPORT_STUCK_TIMEOUT = 10.0
TELEPORT_POLL_INTERVAL = 0.3
RECONNECT_IMAGE_NAMES = ("reconnect", "reconnect_2", "reconnect_3", "retry")

# Same idea as RECONNECT_IMAGE_NAMES -- these four buttons/cards each got a
# second visual variant (nav_play_2.png etc.) added alongside the original,
# so both are tried in turn (see vision.find_image_any/wait_for_image_any)
# instead of only ever matching the first one and treating the button as
# "not there" whenever a setup renders the other variant.
NAV_PLAY_IMAGE_NAMES = ("nav_play", "nav_play_2")
EXPEDITION_IMAGE_NAMES = ("expedition", "expedition_2")
CHALLENGE_IMAGE_NAMES = ("challenge", "challenge_2")
RAID_IMAGE_NAMES = ("raid", "raid_2")
STORY_IMAGE_NAMES = ("story", "story_2")
NAV_START_IMAGE_NAMES = ("nav_start", "nav_start_2")
NAV_DISBAND_IMAGE_NAMES = ("nav_disband", "nav_disband_2")
# 10 visual variants on file (Assets/ui/priority_upgrade/priority_upgrade.png,
# _1 through _9) -- tried in order, same idea as every other _2/_3/... set.
PRIORITY_UPGRADE_IMAGE_NAMES = ("priority_upgrade",) + tuple(f"priority_upgrade_{i}" for i in range(1, 10))

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
REPEAT_STAGE_MODAL_CLEAR_TIMEOUT = 5.0  # how long to wait for the Victory/Defeat banner to actually clear after Repeat Stage
REWARD_CARD_CLEAR_TIMEOUT = 6.0  # how long to spend dismissing "select upgrade card" before Repeat/Leave Stage
SETTLE_DELAY = 0.6  # lets a panel-open animation (e.g. Settings) finish before searching it

# A warning popup can block Start Game right after Pre Start (see
# _wait_out_start_game_warning) -- waited out instead of immediately
# treating a missing nav_start_game as "already started".
WARNING_WAIT_TIMEOUT = 10.0
WARNING_POLL_INTERVAL = 1.0

# Place Unit block execution: search a small box for a valid tile by its
# pixel color (see _find_valid_place_spot), click once a valid one's found,
# then verify. Replaced the old click-first-then-check-a-rejection-image-
# and-nudge approach -- this way a click only ever fires once a genuinely
# valid tile is confirmed, instead of firing blind and finding out after.
PLACE_VALID_PIXEL_TOLERANCE = 12  # each channel allowed to be this far under 0xff (white) -- antialiasing/compression can soften a genuinely-white tile just enough to miss an exact match
PLACE_SEARCH_BOX_SIZE = 38  # side length of the region captured/scanned around the saved spot (i.e. the saved spot +/-19px each way)
PLACE_PIXEL_SEARCH_SETTLE = 0.03  # brief settle after each move before capturing
# The placement-mode highlight overlay apparently needs to actually see the
# cursor move/hover, not just land on a coordinate -- a single move then one
# capture consistently found nothing even on spots that would have been
# valid a moment later. Small back-and-forth nudges (real relative moves,
# not a static cursor) keep prodding the game's own hover state along while
# repeatedly rescanning, up to PLACE_SEARCH_WIGGLE_TIMEOUT.
PLACE_SEARCH_WIGGLE_OFFSETS = [(2, 0), (-2, 0), (0, 2), (0, -2)]
PLACE_SEARCH_WIGGLE_TIMEOUT = 2.5
PLACE_HOTKEY_SETTLE = 0.35  # after pressing the hotkey, before the pixel search starts sampling -- the
# placement-mode overlay (what actually turns a tile white/red) needs real time to render; sampling too
# soon reads the tile's normal color instead and finds neither valid nor blocked
PLACE_UNIT_CLICK_SETTLE = 0.25   # lets the placement actually register before the next check
PLACE_UNIT_VERIFY_TIMEOUT = 2.0
PLACE_UNIT_VERIFY_ATTEMPTS = 3  # search-then-click retried up to this many times before giving up on verifying
MAX_PLACEMENT_THRESHOLD = 0.85
UNIT_INFO_RESET_CLICK = (3, 3)  # near-empty corner of the Roblox screen -- closes the unit info panel after verifying
SCREEN_MIDDLE_CLICK = (576, 378)  # dead center of the 1152x756 game client area -- see FIXED_WIN_W/H in core.config

# Battle-phase Upgrade/Sell Unit blocks (see _run_battle_blocks_tick):
# selecting a unit needs a beat to actually open its info panel before the
# upgradeable/not_upgradeable search means anything.
BATTLE_BLOCK_CLICK_SETTLE = 0.3
# How long an Upgrade Unit block waits before retrying after finding
# not_upgradeable (not enough gold yet, on cooldown, ...) -- not a failure,
# just not ready, so it keeps its remaining `times` budget and tries again
# later rather than giving up or burning through a poll every second.
UPGRADE_RETRY_WAIT = 5.0
UPGRADE_PANEL_LOAD_TIMEOUT = 3.0  # how long to wait for the info panel to actually finish loading after clicking the unit

# Auto Upgrade Unit's priority menu (see _run_auto_upgrade_unit_tick):
# right-clicking "priority_upgrade" (an icon/label found on the selected
# unit's info panel) opens a context menu with Priority 1-6 stacked rows,
# then a Disable row one more row-height below Priority 6. The row
# positions are computed from priority_upgrade's OWN matched width/height
# (self-scaling if the UI ever renders at a different size) instead of a
# second set of fixed coordinates -- these multipliers are eyeballed
# proportions, not measurements off a real capture, so they're the first
# thing to adjust if the priority rows land off:
AUTO_UPGRADE_PRIORITY_ROW_HEIGHT_MULT = 1.35  # one row's height, as a multiple of priority_upgrade's own height
AUTO_UPGRADE_PRIORITY_FIRST_ROW_MULT = 1.8    # icon center down to Priority 1's row, same unit (its own height)
AUTO_UPGRADE_PRIORITY_X_OFFSET_MULT = 2.4     # icon center right to a row's click point, in multiples of its width
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

    def __init__(self, mouse, keyboard, log, set_status=None, record_result=None,
                 get_challenge_settings=None, mark_challenge_stage_played=None):
        self._mouse = mouse
        self._keyboard = keyboard
        self._log = log
        self._set_status = set_status or (lambda **kw: None)
        # (result: "win"|"loss", map_name, duration_str, stats_dict, items_list) ->
        # persists to run history / win-loss counters (see main.Api._record_match_result).
        self._record_result = record_result or (lambda *a, **kw: None)
        # Challenge tab settings live in settings.json, owned by main.Api
        # (same reason record_result is a callback, not a direct import --
        # avoids core/ reaching back into main.py). None (the default, e.g.
        # in tests/CLI mode) just makes _run_challenges a no-op.
        self._get_challenge_settings = get_challenge_settings
        self._mark_challenge_stage_played = mark_challenge_stage_played or (lambda *a, **kw: None)
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

    def start_debug_test(self, hwnd_getter, mode: str, macro_name: str, debug_screenshots: bool = False) -> dict:
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
        self._debug_screenshots = bool(debug_screenshots)
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

    def _click_return_to_lobby_if_found(self, hwnd, stop_event: threading.Event = None) -> None:
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
            return
        if match is None:
            return
        debug_path = self._debug_save(hwnd, "return", match)
        suffix = f" Debug: {debug_path}" if debug_path else ""
        self._log(f"[Macro] Found \"Return to Lobby\" (score {match['score']:.2f}) -- clicking it.{suffix}")
        vision.click_match(self._mouse, hwnd, match)

    def _click_close_popup_if_found(self, hwnd) -> None:
        # Spirit City Act 3 (Raid) can throw up a "Click anywhere to close"
        # popup (a boss/cutscene intro) mid-battle -- one-shot/best-effort
        # like nav_disband, checked every poll tick while watching for the
        # match result (see watch_close_popup in _wait_for_match_result).
        # Two visual variants seen in practice (same idea as nav_start_game/
        # _2/_3/_4), tried in order.
        for name in ("click_anywhere_to_close", "click_anywhere_to_close_2"):
            try:
                match = vision.find_image(hwnd, name)
            except vision.TemplateNotFound:
                continue
            if match is None:
                continue
            debug_path = self._debug_save(hwnd, name, match)
            suffix = f" Debug: {debug_path}" if debug_path else ""
            self._log(f"[Macro] Found \"Click anywhere to close\" (score {match['score']:.2f}) -- clicking it.{suffix}")
            vision.click_match(self._mouse, hwnd, match)
            return

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
        self._mouse.click(left + SCREEN_MIDDLE_CLICK[0], top + SCREEN_MIDDLE_CLICK[1])
        return True

    def _detect_current_challenge_map(self, hwnd) -> str:
        """Regular Challenge is Story's own flow with the game picking a
        random one of CHALLENGE_STORY_MAPS for you -- this is the "which one
        did it land on" check, tried against each map's reference image
        (Assets/ui/<map>.png, a different purpose from Assets/maps/<map>.png's
        map-CARD search) in turn. Returns the matched map name, or None if
        none of them were found (not yet on a recognizable Challenge screen,
        or the wrong screen entirely)."""
        for map_name in CHALLENGE_STORY_MAPS:
            try:
                match = vision.find_image(hwnd, map_name)
            except vision.TemplateNotFound:
                continue
            if match is None:
                continue
            debug_path = self._debug_save(hwnd, map_name, match)
            suffix = f" Debug: {debug_path}" if debug_path else ""
            self._log(f'[Macro] Challenge map detected: "{map_name}" (score {match["score"]:.2f}).{suffix}')
            return map_name
        return None

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

        # Challenge runs ONCE per Start (not once per task-queue pass, see
        # the while loop below) -- if it's enabled, every ready stage slot
        # gets attempted before the Task Queue ever starts.
        if self._checkpoint(stop_event):
            return
        self._run_challenges(hwnd, stop_event, coords, scroll_power, scroll_nudges, default_walk_paths,
                               reward_region, stats_region, webhook)
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

            # The queue always loops back to task 1 once it finishes rather
            # than going Idle -- Stop (F2) is the only way to actually end
            # an unattended run now.
            if self._checkpoint(stop_event):
                return
            loop_pass += 1

    def _challenge_has_ready_stage(self) -> bool:
        """Quick side-effect-free check for whether Challenge automation
        has at least one enabled, not-yet-capped stage slot ready to run
        right now -- used by _run_task's repeat loop to decide whether to
        pause a task's repeats and go run Challenge before continuing
        (see challenge_wants_in there), not just once at the very start of
        a Start press. Same enabled/cap/ready checks _run_challenges itself
        makes per slot, just without actually running anything."""
        if self._get_challenge_settings is None:
            return False
        try:
            challenge = self._get_challenge_settings()
        except Exception:
            return False
        if not challenge.get("enabled"):
            return False
        cap = challenge.get("cap", 0)
        for slot in CHALLENGE_STAGE_SLOTS:
            info = challenge.get("stages", {}).get(slot) or {}
            if not info.get("enabled"):
                continue
            if cap and info.get("count", 0) >= cap:
                continue
            if info.get("ready"):
                return True
        return False

    def _run_challenges(self, hwnd, stop_event: threading.Event, coords: dict, scroll_power: int,
                          scroll_nudges: int, default_walk_paths: dict, reward_region: dict, stats_region: dict,
                          webhook: dict) -> None:
        """Runs every ready (enabled, under today's cap, off its own
        cooldown) Regular Challenge stage slot once each, in #1/#2/#3
        order, then returns -- called once before the Task Queue ever
        starts (see _run), AND again between repeats of an in-progress
        task whenever _challenge_has_ready_stage says a slot's ready (see
        _run_task's repeat loop), not just that one time at the start
        anymore. Challenge is Story's own flow with the
        game picking a random one of CHALLENGE_STORY_MAPS for you instead
        of you picking it, so the actual battle (Pre Start, Start Game,
        Victory/Defeat, reward reading) reuses _play_one_match/
        _handle_match_result unchanged via a synthetic Story-shaped task --
        see _run_one_challenge_stage."""
        if self._get_challenge_settings is None:
            return
        try:
            challenge = self._get_challenge_settings()
        except Exception as exc:
            self._log(f"[Macro] Couldn't read Challenge settings: {exc}")
            return
        if not challenge.get("enabled"):
            return

        self._log("[Macro] Challenge is enabled -- running any ready stage(s) before the Task Queue...")
        cap = challenge.get("cap", 0)
        for slot in CHALLENGE_STAGE_SLOTS:
            if self._checkpoint(stop_event):
                return
            # Re-fetched every slot -- a stage just played updates its own
            # count/cooldown, and this whole pass can span several minutes.
            try:
                challenge = self._get_challenge_settings()
            except Exception as exc:
                self._log(f"[Macro] Couldn't read Challenge settings: {exc}")
                return
            info = challenge.get("stages", {}).get(slot) or {}
            if not info.get("enabled"):
                continue
            if cap and info.get("count", 0) >= cap:
                self._log(f'[Macro] Challenge #{slot} is at today\'s cap ({cap}) -- skipping.')
                continue
            if not info.get("ready"):
                # Already played this slot since the current :00/:30 window
                # opened -- "ready" is computed by get_challenge_settings
                # against that single fixed clock, same for all 3 slots.
                self._log(f'[Macro] Challenge #{slot} already played this window -- skipping.')
                continue

            play_mode = challenge.get("play_mode") or "solo"
            result = self._run_one_challenge_stage(hwnd, stop_event, slot, play_mode, challenge, coords,
                                                     scroll_power, scroll_nudges, default_walk_paths,
                                                     reward_region, stats_region, webhook)
            if self._checkpoint(stop_event):
                return
            if result == "win":
                self._mark_challenge_stage_played(slot)
            elif result == "loss":
                # Only a WIN puts this slot on cooldown -- a loss still only
                # gets the one attempt per window everyone else gets, not an
                # extra free retry from being marked played when it wasn't
                # actually cleared. The match already ran its normal Leave
                # Stage + Return to Lobby (see _handle_match_result), so
                # there's nothing left to recover from here.
                self._log(f'[Macro] Challenge #{slot} was a loss -- not marking it played, '
                           f'still available this window.')
            else:
                self._log(f'[Macro] Challenge #{slot} didn\'t complete cleanly -- recovering to the lobby.')
                # A quick, targeted Leave Stage + Return to Lobby is tried
                # FIRST, on every failed slot (not just handled differently
                # for the first one) -- most failures here still have Leave
                # Stage sitting right there on screen (a stuck detection
                # mid-battle, a follow-up click that never showed up), and
                # clicking straight through it is faster and more reliable
                # than immediately reaching for the heavier generic
                # _recover_to_lobby (menu-backing-out, map-search-failure
                # handling, ...) that's built for recovering from states
                # Leave Stage doesn't even apply to. Only falls through to
                # that heavier recovery if Leave Stage genuinely isn't there.
                if not self._click_and_verify_gone(hwnd, stop_event, "leave_stage", NAV_CLICK_TIMEOUT):
                    if not self._recover_to_lobby(hwnd, stop_event):
                        return
                else:
                    self._click_return_to_lobby_if_found(hwnd, stop_event)

        self._log("[Macro] Challenge pass finished -- moving on to the Task Queue.")

    def _run_one_challenge_stage(self, hwnd, stop_event: threading.Event, slot: str, play_mode: str,
                                   challenge: dict, coords: dict, scroll_power: int, scroll_nudges: int,
                                   default_walk_paths: dict, reward_region: dict, stats_region: dict,
                                   webhook: dict) -> str:
        """Returns "win", "loss", or None -- None covers both a genuine
        technical failure (never got into the stage, map never recognized,
        etc.) AND the run being stopped mid-way, same as _play_one_match's
        own result convention. Callers (_run_challenges) only put the slot
        on cooldown for "win" -- a loss still only gets one shot per
        window, same as everyone else's, not an extra free retry from
        being falsely marked played."""
        self._log(f"[Macro] Challenge #{slot}: entering ({play_mode})...")
        self._set_status(current_task=f"Challenge #{slot}", map="-", action="Entering Challenge...",
                          mode="challenge", stage="-", difficulty="-", play_mode=play_mode, macro="-")
        if not self._enter_challenge_stage(hwnd, stop_event, slot, play_mode, coords, webhook):
            return None
        if self._checkpoint(stop_event):
            return None

        self._log(f"[Macro] Challenge #{slot}: identifying the map...")
        self._set_status(action="Identifying Challenge map...")
        deadline = time.time() + CHALLENGE_MAP_DETECT_TIMEOUT
        detected_map = None
        while time.time() < deadline:
            if self._checkpoint(stop_event):
                return None
            detected_map = self._detect_current_challenge_map(hwnd)
            if detected_map:
                break
            time.sleep(MATCH_RESULT_POLL_INTERVAL)
        if not detected_map:
            self._log(f"[Macro] Challenge #{slot}: never recognized a map -- stopping.")
            return None

        macro_name = (challenge.get("maps", {}).get(detected_map) or {}).get("macro") or ""
        if macro_name:
            self._log(f'[Macro] Challenge #{slot} landed on "{detected_map}" -- running "{macro_name}".')
        else:
            self._log(f'[Macro] Challenge #{slot} landed on "{detected_map}" -- no Macro Operation assigned for it.')

        # mode="story" (not "challenge") deliberately -- this reuses the
        # EXACT SAME Pre Start/Start Game/Victory-Defeat pipeline a real
        # Story task uses (see _play_one_match/_handle_match_result), since
        # that's genuinely what Challenge's own battle is. is_challenge is
        # the marker other code checks when it actually needs to tell the
        # two apart (see _log_expected_rewards -- Challenge isn't in
        # stage_data.json under this map's Story entry, so that reference-
        # reward lookup would otherwise silently show the wrong data).
        task = {
            "mode": "story", "is_challenge": True, "map": detected_map, "difficulty": "Normal",
            "macro": macro_name, "play_mode": play_mode, "repeat": 1, "team": "", "equipment": "include",
        }
        self._set_status(map=detected_map, action="Battle...", difficulty=task["difficulty"], macro=macro_name or "-")
        battle_started = time.time()
        result = self._play_one_match(hwnd, stop_event, task, default_walk_paths, first_repeat=True,
                                        webhook=webhook)
        if result is None:
            return None
        duration = self._format_duration(time.time() - battle_started)

        # Challenge always leaves + returns to lobby afterward (repeat=
        # False) -- there's no "Repeat Stage" concept here, the next
        # attempt (if another slot is still ready) goes through the full
        # Challenge -> stage-slot navigation again, not a quick requeue.
        if not self._handle_match_result(hwnd, stop_event, task, result, duration, reward_region, stats_region,
                                           webhook, repeat=False):
            return None
        return None if self._checkpoint(stop_event) else result

    def _enter_challenge_stage(self, hwnd, stop_event: threading.Event, slot: str, play_mode: str, coords: dict,
                                 webhook: dict) -> bool:
        """Lobby -> Play -> Challenge -> stage slot #1/#2/#3 -> Solo/
        Matchmaking entry (through teleport-in) -- Regular Challenge's
        equivalent of _run_task_setup, except there's no map/difficulty to
        pick (the game assigns both at random), just a fixed-position
        stage row and a screen-load confirmation."""
        if not self._ensure_lobby(hwnd, stop_event):
            return False
        if self._checkpoint(stop_event):
            return False
        if not self._click_play(hwnd, stop_event):
            return False
        if self._checkpoint(stop_event):
            return False
        if not self._click_gamemode(hwnd, stop_event, "challenge"):
            return False
        if self._checkpoint(stop_event):
            return False

        self._log("[Macro] Waiting for the Challenge screen to load...")
        self._set_status(action="Waiting for Challenge screen...")
        try:
            loaded_match = vision.wait_for_image(
                hwnd, "challenge_loaded", timeout=CHALLENGE_SCREEN_TIMEOUT, stop_event=stop_event)
        except vision.TemplateNotFound as exc:
            self._log(f"[Macro] Can't confirm the Challenge screen loaded: {exc}")
            return False
        if loaded_match is None:
            if not stop_event.is_set():
                self._log("[Macro] Challenge screen never loaded -- stopping.")
            return False

        if slot not in CHALLENGE_STAGE_CLICK:
            self._log(f'[Macro] Unknown Challenge stage slot "{slot}".')
            return False
        x, y = CHALLENGE_STAGE_CLICK[slot]
        self._log(f'[Macro] Challenge screen loaded -- clicking stage slot #{slot} at ({x}, {y}).')
        self._set_status(action=f"Clicking Challenge #{slot}...")
        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        self._mouse.click(left + x, top + y)
        if self._checkpoint(stop_event):
            return False

        challenge_task_stub = {"mode": "challenge", "is_challenge": True}
        if play_mode == "matchmaking":
            if not self._click_enter_matchmaking(hwnd, stop_event, coords, "challenge"):
                return False
            if self._checkpoint(stop_event):
                return False
            self._log(f"[Macro] Waiting for the lobby to fill (up to {MATCHMAKING_TELEPORT_TIMEOUT / 60:.0f} "
                       f"min) -- matchmaking has to find real players before it teleports in.")
            if not self._wait_teleport_in(hwnd, stop_event, webhook, challenge_task_stub,
                                            timeout=MATCHMAKING_TELEPORT_TIMEOUT):
                return False
        else:
            self._set_status(action="Clicking Select Stage...")
            if not self._click_and_verify_gone(hwnd, stop_event, "chal_select", CHALLENGE_SCREEN_TIMEOUT):
                self._log('[Macro] "chal_select" never showed up -- stopping.')
                return False
            if self._checkpoint(stop_event):
                return False
            self._log("[Macro] Solo mode -- clicking Start.")
            if not self._click_start_and_wait_teleport(hwnd, stop_event, webhook, challenge_task_stub):
                return False
        return not self._checkpoint(stop_event)

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
            if not self._run_task_setup(hwnd, stop_event, task, mode, map_name, coords, scroll_power,
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
                if not self._handle_match_result(hwnd, stop_event, task, result, duration,
                                                  reward_region, stats_region, webhook,
                                                  repeat=(not is_last_repeat) and not challenge_wants_in):
                    if stop_event.is_set():
                        return False
                    task_failed = True
                    break
                if self._checkpoint(stop_event):
                    return False

                if challenge_wants_in:
                    self._log(f'[Macro] Challenge stage ready -- pausing "{map_name}" to run it '
                               f'before continuing.')
                    self._run_challenges(hwnd, stop_event, coords, scroll_power, scroll_nudges,
                                          default_walk_paths, reward_region, stats_region, webhook)
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
                # The stage-detail panel is still animating in right after
                # the stage-row click -- clicking Normal/Hard immediately
                # landed before the panel (and its toggle) had settled.
                time.sleep(DIFFICULTY_CLICK_DELAY)
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
        # "continue" choice on this same screen) every sighting up to
        # extract_after, only accept the one right after that.
        self._expedition_extract_count = 0
        self._expedition_extract_accept_at = max(0, int(task.get("extract_after") or 0)) + 1
        # Spirit City Act 3's boss/cutscene "Click anywhere to close" popup
        # (see _click_close_popup_if_found) only ever shows up there.
        watch_close_popup = (task.get("mode") == "raid" and task.get("map") == "Spirit City"
                              and str(task.get("stage")) == "3")
        return self._wait_for_match_result(hwnd, stop_event, battle_blocks, first_repeat, task.get("macro"),
                                             task.get("mode"), watch_close_popup, webhook, task)

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
            return self._strip_auto_upgrade_for_expedition(blocks.get("battle") or [], task)
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
        return self._strip_auto_upgrade_for_expedition(legacy_battle, task)

    def _strip_auto_upgrade_for_expedition(self, blocks: list, task: dict) -> list:
        # Auto Upgrade Unit reads the unit's upgrade-cost/affordability UI to
        # decide when to click -- Expedition's version of that panel isn't
        # what it was built against, so it just spins without ever actually
        # upgrading. Rather than have it silently fail on every run, skip
        # the block entirely for Expedition tasks (Pre Start's copy of this
        # same block is skipped the same way -- see _run_prestart_blocks).
        if task.get("mode") != "expedition":
            return blocks
        filtered = [b for b in blocks if b.get("type") != "auto_upgrade_unit"]
        if len(filtered) != len(blocks):
            self._log("[Macro] Skipping Auto Upgrade Unit block(s) -- not reliable on Expedition, ignoring them.")
        return filtered

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
        self._log(f"[Macro] Timed out after {MATCH_RESULT_TIMEOUT / 60:.0f} min waiting for Victory/Defeat.")
        self._save_debug_screenshot_unconditional(hwnd, "match_result_timeout")
        return None

    def _check_expedition_wave_result(self, hwnd, stop_event: threading.Event) -> str:
        """Expedition doesn't show a Victory popup mid-run. exp_continue is
        every regular wave transition AND the mid-run checkpoint (the
        checkpoint is just another exp_continue, not exp_extract) -- click
        it, then Continue_2 (or plain Continue, whichever the game actually
        shows) to move on. exp_extract only shows up once, whenever the
        game itself decides the task's "Extract After" boss/checkpoint has
        been cleared -- there's nothing for the macro to count or decline,
        it just accepts it the moment it's seen (click exp_extract, then
        extract, landing on the reward screen -- the same terminal state
        Victory is for Story/Raid). Returns "win" once extracted, or None
        either while still mid-run (the caller just keeps polling) OR when
        a click's expected follow-up never showed up. Expedition has no
        "defeat" image to check the way Story/Raid do -- a missing
        follow-up here is a macro detection miss, not proof the run was
        actually lost, so it's deliberately NOT reported as "loss" (which
        _handle_match_result would record/webhook as a real Defeat). None
        makes the caller treat it the same as any other setup failure:
        recover to the lobby and move on, with nothing false recorded."""
        # The SAME "Start Game?" confirmation _play_one_match already
        # clicks once before entering Battle can show up AGAIN mid-run --
        # confirmed from a real stuck report: exp_continue/continue_2
        # advanced to a new wave, then the run just sat there silently for
        # over a minute on an identical "Start Game?" popup that nothing
        # was checking for anymore, since this poll loop only ever watched
        # for exp_continue/exp_extract once past the initial click. One-
        # shot per tick (not the full retried version _play_one_match uses)
        # is enough here -- a missed click just gets caught on the very
        # next poll a moment later.
        start_name, start_match = self._find_start_game_button(hwnd)
        if start_match is not None:
            debug_path = self._debug_save(hwnd, start_name, start_match)
            suffix = f" Debug: {debug_path}" if debug_path else ""
            self._log(f'[Macro] Found "{start_name}" again mid-run -- clicking it.{suffix}')
            vision.click_match(self._mouse, hwnd, start_match)
            # Also clicks dead center of the screen once -- a real stuck
            # report showed this same button getting re-found and re-clicked
            # for minutes straight without ever actually going away, which a
            # click landing but not registering (something invisible eating
            # it, or the game just not picking up a single click here) fits
            # better than a detection problem would. A follow-up click
            # somewhere neutral is cheap and harmless if the first one
            # already worked, but gives a real shot at clearing whatever's
            # actually blocking it if it didn't.
            left, top, _, _ = wm.get_window_rect_screen(hwnd)
            self._mouse.click(left + SCREEN_MIDDLE_CLICK[0], top + SCREEN_MIDDLE_CLICK[1])
            return None

        # Same idea as the nav_start_game re-check above -- a level-up
        # "Select an upgrade!" reward-card modal (confirmed via a real
        # capture sitting right on top of the extract/continue choice) gets
        # its own dedicated check here too, not just the middle-screen click
        # bundled into the exp_extract branch, since it can show up on ANY
        # tick, not only the one where exp_extract happens to also be found.
        if self._dismiss_reward_card_if_found(hwnd):
            return None

        # exp_extract is a recurring checkpoint choice -- Extract and
        # Continue offered side by side, not a one-shot terminal event (see
        # the counting reasoning in _play_one_match's reset of these two
        # fields). Decline every sighting up to extract_after (click the
        # "continue" choice THIS screen offers), only accept the sighting
        # right after that.
        try:
            extract_match = vision.find_image(hwnd, "exp_extract")
        except vision.TemplateNotFound:
            extract_match = None
        if extract_match is not None:
            self._expedition_extract_count += 1
            debug_path = self._debug_save(hwnd, "exp_extract", extract_match)
            suffix = f" Debug: {debug_path}" if debug_path else ""
            self._log(f'[Macro] Found "exp_extract" (occurrence {self._expedition_extract_count}/'
                       f'{self._expedition_extract_accept_at}, score {extract_match["score"]:.2f}).{suffix}')
            # A level-up "Select an upgrade!" reward-card modal can be on
            # screen at the exact same moment as this choice (confirmed via
            # a real capture -- 3 upgrade cards sitting right on top of the
            # Extract/Continue buttons), auto-selecting on its own after
            # ~12s but intercepting/covering whichever button gets clicked
            # next until then. No dedicated template for that modal to gate
            # this on, so it's unconditional instead: a middle-screen click
            # picks whatever card is there if one is, and does nothing
            # harmful if there wasn't one. Settled afterward -- the reward
            # modal's own dismiss animation and the extract/continue button
            # itself both need a beat to actually render, not just the
            # instant this match was found in.
            left, top, _, _ = wm.get_window_rect_screen(hwnd)
            self._mouse.click(left + SCREEN_MIDDLE_CLICK[0], top + SCREEN_MIDDLE_CLICK[1])
            time.sleep(0.5)

            if self._expedition_extract_count < self._expedition_extract_accept_at:
                self._log("[Macro] Not the configured sighting yet -- declining (continuing).")
                # _click_and_verify_gone, not the plain single-click
                # _click_found_image -- a laggy game can eat the first click
                # without the button actually going anywhere, and the next
                # poll tick would then just re-find the SAME still-showing
                # exp_extract sighting again without ever having actually
                # advanced past it (reported: the count stops incrementing
                # because it's stuck re-declining the identical checkpoint).
                # Retrying the click until it's confirmed gone is what
                # actually fixes that, not just clicking once and hoping.
                if not self._click_and_verify_gone(hwnd, stop_event, "continue", EXPEDITION_EXTRACT_CONFIRM_TIMEOUT):
                    self._log('[Macro] "continue" never showed up after exp_extract -- will retry next poll.')
                    return None
                if not self._click_and_verify_gone(hwnd, stop_event, "continue_2", EXPEDITION_WAVE_TIMEOUT):
                    self._log('[Macro] "continue_2" never showed up after declining exp_extract -- '
                               'will retry next poll.')
                    return None
                self._interruptible_sleep(EXPEDITION_CONTINUE_COOLDOWN, stop_event)
                return None

            self._log(f"[Macro] exp_extract sighting {self._expedition_extract_count}/"
                       f"{self._expedition_extract_accept_at} -- extracting for real.")
            # Double-clicked (not a single click like every other match in
            # this file) -- this specific button has been reported as only
            # sometimes actually registering on the first click.
            vision.double_click_match(self._mouse, hwnd, extract_match)
            try:
                confirm_match = vision.wait_for_image(
                    hwnd, "extract", timeout=EXPEDITION_EXTRACT_CONFIRM_TIMEOUT, stop_event=stop_event)
            except vision.TemplateNotFound as exc:
                self._log(f"[Macro] {exc}")
                return None
            if confirm_match is None:
                if not (stop_event is not None and stop_event.is_set()):
                    self._log('[Macro] "extract" never showed up after exp_extract -- will retry next poll '
                               '(exp_extract itself doesn\'t go away, a reward modal may just be covering it).')
                return None
            debug_path = self._debug_save(hwnd, "extract", confirm_match)
            suffix = f" Debug: {debug_path}" if debug_path else ""
            self._log(f'[Macro] Found "extract" (score {confirm_match["score"]:.2f}) -- clicking it.{suffix}')
            # Shuffled, not a plain click -- reported (confirmed by testing)
            # that this specific button's click can visually land without
            # actually registering game-side, apparently needing genuine
            # hover-in movement first, not just an absolute jump. Then a
            # real settle wait afterward too, on top of that -- the same
            # class of issue as a click not being given time to register
            # before the very next check runs right on top of it.
            vision.shuffle_click_match(self._mouse, hwnd, confirm_match)
            self._interruptible_sleep(EXTRACT_CONFIRM_SETTLE, stop_event)
            # A SECOND confirmation ("Extraction -- Are you sure you'd like
            # to end this run?", its own separate red Extract/Cancel
            # buttons, a rewards preview) can show up after this click --
            # confirmed from a real capture, stuck exactly here. Optional/
            # best-effort like nav_disband: extract_confirm.png being
            # missing just means this step is silently skipped (treated as
            # if this second modal never happens), not a failure, since not
            # everyone will have added it yet.
            try:
                second_confirm = vision.wait_for_image(
                    hwnd, "extract_confirm", timeout=EXPEDITION_EXTRACT_CONFIRM_TIMEOUT, stop_event=stop_event)
            except vision.TemplateNotFound:
                second_confirm = None
            if second_confirm is not None:
                debug_path = self._debug_save(hwnd, "extract_confirm", second_confirm)
                suffix = f" Debug: {debug_path}" if debug_path else ""
                self._log(f'[Macro] Found "extract_confirm" (score {second_confirm["score"]:.2f}) -- '
                           f'clicking it.{suffix}')
                vision.shuffle_click_match(self._mouse, hwnd, second_confirm)
                self._interruptible_sleep(EXTRACT_CONFIRM_SETTLE, stop_event)
            self._log("[Macro] Extracted -- on the reward screen.")
            return "win"

        try:
            continue_match = vision.find_image(hwnd, "exp_continue")
        except vision.TemplateNotFound:
            continue_match = None
        if continue_match is not None:
            debug_path = self._debug_save(hwnd, "exp_continue", continue_match)
            suffix = f" Debug: {debug_path}" if debug_path else ""
            self._log(f'[Macro] Found "exp_continue" (score {continue_match["score"]:.2f}) -- clicking it.{suffix}')
            vision.click_match(self._mouse, hwnd, continue_match)
            # continue_2 is the expected follow-up, but the plain "continue"
            # button can show up here instead depending on the wave --
            # checking for either one (same idea as the _2 image variants
            # elsewhere) instead of only continue_2 avoids a false "never
            # showed up" stop when the follow-up screen just wasn't the one
            # specifically expected.
            try:
                follow_match, follow_name = vision.wait_for_image_any(
                    hwnd, ("continue_2", "continue"), timeout=EXPEDITION_WAVE_TIMEOUT, stop_event=stop_event)
            except vision.TemplateNotFound:
                follow_match, follow_name = None, None
            if follow_match is None:
                if stop_event is not None and stop_event.is_set():
                    return None
                self._log('[Macro] Neither "continue_2" nor "continue" showed up after exp_continue -- '
                           'will retry next poll.')
                return None
            debug_path = self._debug_save(hwnd, follow_name, follow_match)
            suffix = f" Debug: {debug_path}" if debug_path else ""
            self._log(f'[Macro] Found "{follow_name}" (score {follow_match["score"]:.2f}) -- clicking it.{suffix}')
            # Retried until it's confirmed gone, not just clicked once --
            # same laggy-click issue as the exp_extract decline path (a
            # click that doesn't register still leaves this same button on
            # screen, and the very next poll tick just re-finds it, stuck).
            for _ in range(3):
                vision.click_match(self._mouse, hwnd, follow_match)
                time.sleep(1.0)
                if stop_event is not None and stop_event.is_set():
                    break
                try:
                    still_match, _ = vision.find_image_any(hwnd, (follow_name,))
                except vision.TemplateNotFound:
                    still_match = None
                if still_match is None:
                    break
                follow_match = still_match
            self._interruptible_sleep(EXPEDITION_CONTINUE_COOLDOWN, stop_event)
            return None

        return None

    def debug_check_expedition_wave(self, hwnd) -> str:
        """Settings > Debug > "Test Expedition Wave Check" -- runs exactly
        one tick of _check_expedition_wave_result against whatever's on
        screen in Roblox right now, with real clicks and all, but WITHOUT
        needing an actual task/run in progress first. Point of this: tuning
        nav_start_game/exp_continue/exp_extract detection used to mean
        restarting a whole macro run (lobby -> gamemode -> map -> stage ->
        teleport) every single time just to get back to the one screen
        being tested. Navigate to that screen by hand in Roblox instead,
        press this button, read what it found/clicked in the log, repeat
        as many times as needed. Uses a stop_event that's never set (there's
        no real run to interrupt), so a click's own wait-for-follow-up can
        still time out normally, it just can't be cancelled early."""
        wm.show_window(hwnd)
        if not wm.activate_window(hwnd):
            self._log("[Debug] Couldn't confirm Roblox actually took focus -- clicks may not register "
                       "until it does. Continuing anyway.")
        self._log("[Debug] Testing Expedition wave-result check (single tick)...")
        result = self._check_expedition_wave_result(hwnd, threading.Event())
        self._log(f"[Debug] Expedition wave-result check returned: {result!r}")
        return result

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
                # not a Pre Start starter) -- same pixel-search-place/verify
                # logic Pre Start uses, one-shot like Sell Unit. Continues
                # the SAME #ordinal count Pre Start's place_unit blocks left
                # off at, matching ui/app.js's listPlacedUnits() (which
                # numbers place_unit blocks across both phases as one list),
                # so Upgrade/Sell/Auto Upgrade Unit blocks targeting a
                # unit placed here by #index still resolve correctly.
                self._last_unit_ordinal += 1
                left, top, _, _ = wm.get_window_rect_screen(hwnd)
                next_index = self._battle_block_index + 1
                next_block = battle_blocks[next_index] if next_index < len(battle_blocks) else None
                next_is_same_unit = bool(
                    next_block and next_block.get("type") == "place_unit"
                    and block.get("hotkey") and next_block.get("hotkey") == block.get("hotkey"))
                self._run_place_unit_block(hwnd, stop_event, left, top, block, self._battle_block_index + 1,
                                             macro_name, self._last_unit_ordinal,
                                             next_is_same_unit=next_is_same_unit)
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

        # Waits for the info panel to actually finish loading instead of a
        # single check right after BATTLE_BLOCK_CLICK_SETTLE (0.3s) -- that
        # was reported as consistently too fast right after a unit was just
        # placed (the panel can still be settling), landing on neither
        # image and burning a full UPGRADE_RETRY_WAIT (5s) for nothing.
        # Polling for EITHER one to show up (whichever the panel actually
        # ends up in) is the real "wait until it's loaded" this needs,
        # not just a longer fixed sleep.
        try:
            upgrade_match, found_name = vision.wait_for_image_any(
                hwnd, ("upgradeable", "not_upgradeable"), timeout=UPGRADE_PANEL_LOAD_TIMEOUT, stop_event=stop_event)
        except vision.TemplateNotFound:
            upgrade_match, found_name = None, None
        if found_name == "not_upgradeable":
            not_upgrade_match, upgrade_match = upgrade_match, None
        else:
            not_upgrade_match = None
        if upgrade_match is not None:
            self._log(f'{label}: found Upgradeable (score {upgrade_match["score"]:.2f}) -- pressing T '
                       f'({state["remaining"]} left after this).')
            self._keyboard.tap(ord("T"))
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
        """One-shot: click the unit, right-click "priority_upgrade" (found
        on its info panel) to open its priority menu, click the configured
        priority row (or Disable for "None"), then a reset click. Always
        "done" after one try -- setting a priority isn't a repeated action
        the way Upgrade Unit's clicks are."""
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

        try:
            priority_match, priority_name = vision.find_image_any(hwnd, PRIORITY_UPGRADE_IMAGE_NAMES)
        except vision.TemplateNotFound as exc:
            self._log(f'{label}: {exc}')
            return True
        if priority_match is None:
            self._log(f'{label}: "priority_upgrade" not found on the info panel -- skipping.')
            return True

        debug_path = self._debug_save(hwnd, priority_name, priority_match)
        suffix = f" Debug: {debug_path}" if debug_path else ""
        self._log(f'{label}: found "{priority_name}" (score {priority_match["score"]:.2f}) -- '
                   f'right-clicking it.{suffix}')
        vision.right_click_match(self._mouse, hwnd, priority_match)
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
        # Row positions computed off priority_upgrade's OWN matched w/h
        # (see the constants' own comment) instead of a second set of fixed
        # coordinates -- self-scaling if the icon itself ever renders at a
        # different size.
        row_height = priority_match["h"] * AUTO_UPGRADE_PRIORITY_ROW_HEIGHT_MULT
        row_x = priority_match["cx"] + priority_match["w"] * AUTO_UPGRADE_PRIORITY_X_OFFSET_MULT
        first_row_y = priority_match["cy"] + priority_match["h"] * AUTO_UPGRADE_PRIORITY_FIRST_ROW_MULT
        row_y = first_row_y + row_index * row_height
        self._mouse.click(left + int(row_x), top + int(row_y))
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

        # A Battle-phase quick-place chain (see _run_place_unit_block) could
        # still be holding Shift down right up to the moment Victory/Defeat
        # actually landed -- released here, before anything else, so it's
        # never still held into the result screen and beyond.
        self._release_quick_place_shift()

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
            self._mouse.move_to(left + UNIT_INFO_RESET_CLICK[0], top + UNIT_INFO_RESET_CLICK[1])
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
        if not self._click_and_verify_gone(hwnd, stop_event, "leave_stage", NAV_CLICK_TIMEOUT):
            self._log('[Macro] "Leave Stage" not found -- stopping.')
            return False
        self._click_return_to_lobby_if_found(hwnd, stop_event)
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
        # Expedition has no entry in stage_data.json at all -- its stage/
        # difficulty values ("1"/"2"/"3", no Act number) would otherwise
        # alias onto the SAME map's Story Act 1 data below (get_stage falls
        # back to "Normal" for any difficulty that isn't literally "Hard"),
        # silently showing real Story rewards mislabeled as Expedition's.
        # Challenge (task["is_challenge"], see _run_one_challenge_stage) is
        # mode="story" on purpose to reuse the rest of this pipeline, but
        # for the SAME reason as Expedition its stage_data.json lookup
        # would alias onto that map's real Story Act 1 data -- Challenge
        # isn't in stage_data.json under any entry at all.
        if task.get("mode") == "expedition" or task.get("is_challenge"):
            return None, None
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
        try:
            self._apply_team_loadout_panel(hwnd, stop_event, team_match, team_num, equipment)
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

    def _apply_team_loadout_panel(self, hwnd, stop_event: threading.Event, team_match, team_num: int,
                                    equipment: str) -> None:
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
        elif team_num > 3:
            # 4-6 scrolled into the SAME row slots 1-3 sit in pre-scroll (see
            # the drag comment above) -- team_num must be rebased against
            # row 1 the way 1-3 already are (team_num - 1), not counted as
            # if the list never moved. Using (team_num - 1) unscrolled here
            # undercounted the shift by exactly the 3 rows the drag just
            # revealed, landing the click 3 * TEAM_LOADOUT_ROW_HEIGHT (378px)
            # below the real row -- past the panel and onto the game view
            # behind it, which is what turned into "the click/scroll goes
            # way too far, outside Roblox" for teams 5-6 (4 was off too, by
            # the same bug, just closer to a row that still did something).
            row_y = TEAM_LOADOUT_CLICK_1[1] + (team_num - 4) * TEAM_LOADOUT_ROW_HEIGHT
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

    def _run_prestart_blocks(self, hwnd, stop_event: threading.Event, task: dict, first_repeat: bool = True,
                               default_walk_paths: dict = None) -> None:
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
        prestart_blocks = self._strip_auto_upgrade_for_expedition(prestart_blocks or [], task)

        # Walk Path used to be saved as a separate top-level blocks["walk"]
        # config instead of a real block in this list -- ui/app.js's own
        # Creation UI migrates that into a real walk_path block the moment
        # a template's opened there, but a template that's never been
        # reopened+resaved since that change is still sitting on disk in
        # the OLD shape, and this runner has no other path left that reads
        # blocks["walk"] anymore (confirmed from a real report: Challenge's
        # "Kings Tomb" template silently stopped walking Auto -- it had
        # never been touched in Creation since the update). Migrated here
        # too, the same way (a synthesized block at the very top, where it
        # always effectively ran before), so a template someone never
        # happens to open in the editor still walks correctly.
        legacy_walk = blocks.get("walk")
        if legacy_walk and not any(b.get("type") == "walk_path" for b in prestart_blocks):
            prestart_blocks = [{
                "type": "walk_path", "params": {}, "once": False,
                "mode": "custom" if legacy_walk.get("mode") == "custom" else "auto",
                "pathName": legacy_walk.get("pathName") or "",
            }] + prestart_blocks

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
        self._quick_place_shift_down = False
        try:
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
                    next_block = prestart_blocks[i] if i < len(prestart_blocks) else None
                    next_is_same_unit = bool(
                        next_block and next_block.get("type") == "place_unit"
                        and block.get("hotkey") and next_block.get("hotkey") == block.get("hotkey"))
                    self._run_place_unit_block(hwnd, stop_event, left, top, block, i, macro_name,
                                                 self._last_unit_ordinal, next_is_same_unit=next_is_same_unit,
                                                 verify=False)
                elif btype == "setting_change":
                    self._run_setting_block(hwnd, stop_event, block, i)
                elif btype == "auto_upgrade_unit":
                    self._run_auto_upgrade_unit_tick(hwnd, stop_event, block, i)
                elif btype == "walk_path":
                    self._run_walk_path_block(hwnd, stop_event, task, default_walk_paths or {}, block, first_repeat)
                else:
                    self._log(f'[Macro] Skipping block #{i} ("{btype}") -- not runnable in Pre Start yet.')
                time.sleep(0.2)  # brief gap between blocks so the game UI can settle
        finally:
            # Safety net -- a "Once"-skipped block right after the last
            # quick-place placement (or the list just ending mid-chain)
            # would otherwise leave Shift stuck down for good, since
            # next_is_same_unit's own block never actually runs to release
            # it. Whatever else happens, Shift never leaves this function
            # still held.
            self._release_quick_place_shift()

    def _run_walk_path_block(self, hwnd, stop_event: threading.Event, task: dict, default_walk_paths: dict,
                               block: dict, first_repeat: bool) -> None:
        """Walk Path block -- Auto (the map's own default_walk_paths entry)
        or a specific recorded Custom path (block["mode"]/block["pathName"],
        same shape the old separate pinned row used to keep at the template
        level). Only makes sense the FIRST time a task enters a stage --
        once you're standing where the walk leaves you, repeating the same
        walk on every repeat would just walk you away from that spot again
        for no reason -- so this checks first_repeat itself regardless of
        the block's own "Once" toggle, same hardcoded skip the old fixed
        pre-step always had."""
        if not first_repeat:
            self._log('[Macro] Repeat of the same stage -- skipping the Walk Path block (already walked on entry).')
            return

        map_name = task.get("map")
        if block.get("mode") == "custom" and block.get("pathName"):
            path_name = block["pathName"]
        else:
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
            return

        self._log(f'[Macro] Walking path "{path_name}"...')
        self._set_status(action=f'Walking "{path_name}"...')
        data = walk_paths.load_path(path_name)
        events = data.get("events", [])
        if not events:
            self._log(f'[Macro] Walk path "{path_name}" has no recorded movement -- skipping.')
            return
        walk_paths.replay_events(events, self._keyboard, stop_event)
        self._log("[Macro] Walk finished.")

    def _release_quick_place_shift(self) -> None:
        if self._quick_place_shift_down:
            self._keyboard.key_up(keys.VK_SHIFT)
            self._quick_place_shift_down = False

    def _scan_place_search_box(self, left: int, top: int, orig_x: int, orig_y: int):
        """One capture of the PLACE_SEARCH_BOX_SIZE x PLACE_SEARCH_BOX_SIZE
        region centered on (orig_x, orig_y) -- window-client coords --
        scanned in memory for a pixel at/near 0xffffff (white, within
        PLACE_VALID_PIXEL_TOLERANCE per channel). Returns the (dx, dy)
        offset of whichever valid pixel is CLOSEST to the center, or None
        if nothing valid was found anywhere in the box."""
        import numpy as np
        from core.ocr import capture_region
        half = PLACE_SEARCH_BOX_SIZE // 2
        patch = capture_region(left + orig_x - half, top + orig_y - half,
                                 PLACE_SEARCH_BOX_SIZE, PLACE_SEARCH_BOX_SIZE)
        b, g, r = patch[:, :, 0].astype(int), patch[:, :, 1].astype(int), patch[:, :, 2].astype(int)
        floor = 255 - PLACE_VALID_PIXEL_TOLERANCE
        valid_mask = (r >= floor) & (g >= floor) & (b >= floor)
        ys, xs = np.where(valid_mask)
        if len(xs) == 0:
            return None
        dists = (xs - half) ** 2 + (ys - half) ** 2
        best = int(np.argmin(dists))
        return int(xs[best]) - half, int(ys[best]) - half

    def _find_valid_place_spot(self, hwnd, stop_event: threading.Event, left: int, top: int,
                                 orig_x: int, orig_y: int, name: str):
        """Moves onto (orig_x, orig_y) -- window-client coords -- then
        repeatedly wiggles the cursor a little and rescans a small box
        around it (see _scan_place_search_box) until a valid tile turns up
        or PLACE_SEARCH_WIGGLE_TIMEOUT runs out. The wiggling isn't
        cosmetic -- reported (and confirmed from a real run) that a single
        move-then-capture consistently found nothing even on spots that
        WOULD have read as valid a moment later: the placement-mode
        highlight overlay apparently needs to actually see the cursor
        moving/hovering there before it renders at all, not just land on a
        coordinate. Returns the (x, y) window-client offset it settled on,
        or None if nothing valid ever showed up in time."""
        self._mouse.move_to(left + orig_x, top + orig_y)
        time.sleep(PLACE_PIXEL_SEARCH_SETTLE)

        deadline = time.time() + PLACE_SEARCH_WIGGLE_TIMEOUT
        wiggle_idx = 0
        while True:
            if self._checkpoint(stop_event):
                return None
            found = self._scan_place_search_box(left, top, orig_x, orig_y)
            if found is not None:
                dx, dy = found
                cx, cy = orig_x + dx, orig_y + dy
                if (dx, dy) != (0, 0):
                    self._mouse.move_to(left + cx, top + cy)
                    time.sleep(PLACE_PIXEL_SEARCH_SETTLE)
                    self._log(f'[Macro] Place Unit "{name}": aligned to a valid tile at offset ({dx}, {dy}).')
                return cx, cy
            if time.time() >= deadline:
                return None
            wx, wy = PLACE_SEARCH_WIGGLE_OFFSETS[wiggle_idx % len(PLACE_SEARCH_WIGGLE_OFFSETS)]
            self._mouse.nudge(wx, wy)
            wiggle_idx += 1
            time.sleep(PLACE_PIXEL_SEARCH_SETTLE)

    def _run_place_unit_block(self, hwnd, stop_event: threading.Event, left: int, top: int, block: dict,
                                index: int, macro_name: str, unit_ordinal: int = None,
                                next_is_same_unit: bool = False, verify: bool = True) -> None:
        params = block.get("params") or {}
        name = params.get("name") or f"#{index}"
        hotkey = block.get("hotkey")
        orig_x, orig_y = params.get("x"), params.get("y")
        self._set_status(action=f'Placing unit "{name}"...')

        if not (orig_x or orig_y):
            self._log(f'[Macro] Place Unit "{name}" has no position set -- skipping.')
            return
        orig_x, orig_y = int(orig_x), int(orig_y)

        # Quick place: a run of consecutive Place Unit blocks for the SAME
        # unit (matched by hotkey) holds Left Shift down from right before
        # the first one is clicked through the last one -- while it's held,
        # the same unit stays selected, so every placement after the first
        # skips Z/the hotkey press entirely and just places straight into
        # the next spot. self._quick_place_shift_down being already True
        # here means this call IS one of those continuations.
        # Whether THIS placement is part of a quick-place run at all (either
        # continuing one, or about to start one that continues after it) --
        # used below to skip the unit_exist verify step, which otherwise
        # breaks the whole point of quick-place: a click, then wait, then
        # (if not immediately confirmed) ANOTHER click and up to
        # PLACE_UNIT_VERIFY_TIMEOUT more seconds, before the next hover-and-
        # click can even start. The pre-click pixel-white confirmation is
        # already solid evidence the placement landed -- good enough for a
        # fast consecutive run, even without also re-confirming after.
        is_quick_place = self._quick_place_shift_down or next_is_same_unit
        # verify=False for every Pre Start placement, not just quick-place
        # chains (see _run_prestart_blocks/_run_battle_blocks_tick's own
        # calls) -- the wait-for-unit_exist-then-maybe-double-click-to-
        # recheck step only makes sense for a mid-battle reinforcement,
        # where confirming it actually landed matters more than speed.
        # Pre Start already trusts the pre-click pixel-white confirmation
        # for quick-place; this extends that same trust to every other
        # Pre Start placement too instead of just the chained ones.
        skip_verify = is_quick_place or not verify

        if self._quick_place_shift_down:
            self._log(f'[Macro] Place Unit "{name}": quick-placing (Shift held, same unit as last).')
        else:
            # No hotkey (or one that isn't recognized) means nothing ever
            # gets selected -- the pixel search below would just be
            # hovering/clicking with no unit in hand at all, which is
            # exactly the "something's wrong" this was reported as during
            # quick-place chains. Skip the whole block outright instead of
            # only logging a warning and clicking anyway.
            if not hotkey:
                self._log(f'[Macro] Place Unit "{name}" has no hotkey set -- skipping this block.')
                return
            vk = keys.key_name_to_vk(hotkey)
            if vk is None:
                self._log(f'[Macro] Place Unit "{name}": hotkey "{hotkey}" isn\'t recognized -- '
                           f'skipping this block.')
                return

            # Z first, always -- clears whatever the cursor/UI was last doing
            # so the hotkey press right after it reliably starts a fresh
            # placement instead of potentially colliding with leftover state.
            self._keyboard.tap(ord("Z"))
            time.sleep(0.1)
            self._log(f'[Macro] Place Unit "{name}": pressing hotkey "{hotkey}" -- entering placing mode.')
            self._keyboard.tap(vk)
            time.sleep(PLACE_HOTKEY_SETTLE)

            if next_is_same_unit:
                self._log(f'[Macro] Place Unit "{name}": next placement is the same unit -- '
                           f'holding Shift for quick-place.')
                self._keyboard.key_down(keys.VK_SHIFT)
                self._quick_place_shift_down = True

        if block.get("ignoreHighlight"):
            # Skips the white-tile search entirely -- clicks the saved X/Y
            # directly, same as before the search existed at all. For a
            # spot where the highlight doesn't reliably show/detect,
            # searching for it is worse than just trusting the coordinate.
            self._mouse.move_to(left + orig_x, top + orig_y)
            time.sleep(PLACE_PIXEL_SEARCH_SETTLE)
            spot = (orig_x, orig_y)
        else:
            spot = self._find_valid_place_spot(hwnd, stop_event, left, top, orig_x, orig_y, name)
        if self._checkpoint(stop_event):
            self._release_quick_place_shift()
            return
        if spot is None:
            self._log(f'[Macro] Place Unit "{name}": no valid (white) tile found in the '
                       f'{PLACE_SEARCH_BOX_SIZE}x{PLACE_SEARCH_BOX_SIZE} box around ({orig_x}, {orig_y}) -- giving up.')
            if not next_is_same_unit:
                self._release_quick_place_shift()
            return
        cur_x, cur_y = spot

        self._mouse.click(left + cur_x, top + cur_y)
        time.sleep(PLACE_UNIT_CLICK_SETTLE)
        if self._checkpoint(stop_event):
            self._release_quick_place_shift()
            return

        # max_placement_reached is optional (like nav_disband) -- a missing
        # image just means this check is silently skipped, not that the
        # block fails, since not everyone will have added it.
        try:
            limit_match = vision.find_image(hwnd, "max_placement_reached", threshold=MAX_PLACEMENT_THRESHOLD)
        except vision.TemplateNotFound:
            limit_match = None
        if limit_match is not None:
            self._log(f'[Macro] Place Unit "{name}": max placement limit reached -- skipping this block.')
            if not next_is_same_unit:
                self._release_quick_place_shift()
            return

        # Last of this quick-place run (or not part of one at all) --
        # release Shift now that the click that needed it is done.
        if not next_is_same_unit:
            self._release_quick_place_shift()

        if skip_verify:
            # No verify here -- see skip_verify's own comment above.
            # Position is still recorded, just without waiting on
            # unit_exist first; the white-pixel hit before the click is
            # what's trusted instead.
            reason = 'quick-place' if is_quick_place else 'Pre Start'
            self._log(f'[Macro] Place Unit "{name}": placed at ({cur_x}, {cur_y}) ({reason}).')
            if unit_ordinal is not None:
                self._placed_unit_positions[unit_ordinal] = (cur_x, cur_y)
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
                match, _ = vision.find_image_any(current_hwnd, NAV_PLAY_IMAGE_NAMES, region=NAV_PLAY_REGION)
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

    def _find_start_game_button(self, hwnd, stop_event: threading.Event = None, timeout: float = 0):
        """Tries nav_start_game, then nav_start_game_2, nav_start_game_3,
        nav_start_game_4 in order -- different visual variants of the same
        button seen in practice, so the actual "start the round" click
        (see _play_one_match) isn't dependent on just one of them matching.
        Returns (name, match) for whichever was found first, or (None,
        None) if none of them were -- missing/not-yet-added variants are
        skipped silently, same as any other optional template.

        timeout=0 (the default) is a single instant pass -- used right
        after a click to check it's gone, where waiting around would just
        slow the retry loop down. Pass a real timeout for the FIRST check
        (right as Pre Start hands off), since the button can still be
        animating in at that exact moment and a one-shot check there was
        landing before it existed at all, especially on Expedition where
        Pre Start's place_unit clicks run right up until this point."""
        deadline = time.time() + max(0.0, timeout)
        while True:
            for name in ("nav_start_game", "nav_start_game_2", "nav_start_game_3", "nav_start_game_4"):
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

    def _select_difficulty(self, hwnd, difficulty: str, coords: dict) -> None:
        # Fixed spot on the stage-detail panel, same as the stage rows --
        # no image search needed, just like Story's click was.
        key_prefix = "difficulty_hard" if difficulty == "Hard" else "difficulty_normal"
        x, y = coords[f"{key_prefix}_x"], coords[f"{key_prefix}_y"]
        self._log(f'[Macro] Clicking difficulty "{difficulty}" at ({x}, {y}).')
        self._set_status(action=f'Clicking difficulty "{difficulty}"...')
        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        self._mouse.click(left + x, top + y)

    def _select_expedition_difficulty(self, hwnd, stop_event: threading.Event, difficulty: str) -> None:
        # One "+" button that steps the level up by 1 per click, starting
        # from 1 -- see EXPEDITION_DIFFICULTY_CLICK's comment.
        try:
            clicks = max(0, int(difficulty) - 1)
        except (TypeError, ValueError):
            clicks = 0
        if clicks == 0:
            self._log(f'[Macro] Difficulty "{difficulty}" is the default -- no click needed.')
            return
        self._log(f'[Macro] Clicking difficulty "+" {clicks} time(s) at {EXPEDITION_DIFFICULTY_CLICK} '
                   f'for difficulty {difficulty}.')
        self._set_status(action=f'Setting difficulty {difficulty}...')
        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        x, y = left + EXPEDITION_DIFFICULTY_CLICK[0], top + EXPEDITION_DIFFICULTY_CLICK[1]
        for _ in range(clicks):
            if stop_event.is_set():
                return
            self._mouse.click(x, y)
            time.sleep(EXPEDITION_DIFFICULTY_CLICK_DELAY)

    def _click_enter_matchmaking(self, hwnd, stop_event: threading.Event, coords: dict, mode: str = None) -> bool:
        # Expedition's and Challenge's matchmaking buttons are each their
        # own image (exp_enter_matchmaking / chal_enter) at an uncalibrated
        # position -- no matchmaking_region_* exists for either, so they're
        # searched full-window instead of the Story/Raid region restriction.
        image_name = {"expedition": "exp_enter_matchmaking", "challenge": "chal_enter"}.get(mode, "enter_matchmaking")
        region = None if mode in ("expedition", "challenge") else (
            coords["matchmaking_region_x"], coords["matchmaking_region_y"],
            coords["matchmaking_region_w"], coords["matchmaking_region_h"],
        )
        self._log("[Macro] Waiting for Enter Matchmaking...")
        self._set_status(action="Waiting for Enter Matchmaking...")
        try:
            match = vision.wait_for_image(
                hwnd, image_name, region=region,
                timeout=MATCHMAKING_WAIT_TIMEOUT, stop_event=stop_event)
        except vision.TemplateNotFound as exc:
            self._log(f"[Macro] Can't find Enter Matchmaking: {exc}")
            return False
        if match is None:
            if not stop_event.is_set():
                self._log("[Macro] Enter Matchmaking never showed up -- stopping.")
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
            match, _ = vision.wait_for_image_any(
                hwnd, NAV_PLAY_IMAGE_NAMES, region=NAV_PLAY_REGION,
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
        self._log("[Macro] Play button never showed up -- attempting a rejoin via deep link.")
        return self._attempt_rejoin(hwnd, stop_event)

    def _click_play(self, hwnd, stop_event: threading.Event) -> bool:
        self._set_status(action="Clicking Play...")
        try:
            match, name = vision.find_image_any(hwnd, NAV_PLAY_IMAGE_NAMES, region=NAV_PLAY_REGION)
        except vision.TemplateNotFound as exc:
            self._log(f"[Macro] {exc}")
            return False
        if match is None:
            self._log("[Macro] Play button vanished before it could be clicked -- stopping.")
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

    def _select_expedition_map(self, hwnd, stop_event: threading.Event, map_name: str) -> bool:
        image_name = EXPEDITION_MAP_IMAGES.get(map_name)
        if image_name is None:
            self._log(f'[Macro] "{map_name}" is selected by default on the Expedition screen -- no click needed.')
            return True
        self._log(f'[Macro] Looking for Expedition map "{map_name}"...')
        try:
            match = vision.wait_for_image(hwnd, image_name, timeout=GAMEMODE_CLICK_TIMEOUT, stop_event=stop_event)
        except vision.TemplateNotFound as exc:
            self._log(f"[Macro] Can't find \"{map_name}\": {exc}")
            return False
        if match is None:
            if not stop_event.is_set():
                self._log(f'[Macro] "{map_name}" never showed up -- stopping.')
            return False
        debug_path = self._debug_save(hwnd, image_name, match)
        suffix = f" Debug: {debug_path}" if debug_path else ""
        self._log(f'[Macro] Found "{map_name}" (score {match["score"]:.2f}) -- clicking it.{suffix}')
        vision.click_match(self._mouse, hwnd, match)
        return True

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
                    hwnd, EXPEDITION_IMAGE_NAMES, timeout=GAMEMODE_CLICK_TIMEOUT, stop_event=stop_event)
            except vision.TemplateNotFound as exc:
                self._log(f"[Macro] Can't find Expedition: {exc}")
                return False
            if match is None:
                if not stop_event.is_set():
                    self._log("[Macro] Expedition card never showed up -- stopping.")
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
                    hwnd, CHALLENGE_IMAGE_NAMES, timeout=GAMEMODE_CLICK_TIMEOUT, stop_event=stop_event)
            except vision.TemplateNotFound as exc:
                self._log(f"[Macro] Can't find Challenge: {exc}")
                return False
            if match is None:
                if not stop_event.is_set():
                    self._log("[Macro] Challenge card never showed up -- stopping.")
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
                    hwnd, RAID_IMAGE_NAMES, timeout=GAMEMODE_CLICK_TIMEOUT, stop_event=stop_event)
            except vision.TemplateNotFound as exc:
                self._log(f"[Macro] Can't find Raid: {exc}")
                return False
            if match is None:
                if not stop_event.is_set():
                    self._log("[Macro] Raid card never showed up -- stopping.")
                return False
            debug_path = self._debug_save(hwnd, name, match)
            suffix = f" Debug: {debug_path}" if debug_path else ""
            self._log(f"[Macro] Found Raid (score {match['score']:.2f}) -- clicking it.{suffix}")
            vision.click_match(self._mouse, hwnd, match)
            return True

        # story.png alone used to not be distinct enough to match reliably
        # (see STORY_CLICK's comment) -- story_2.png is a second reference
        # crop for the same card (same idea as nav_play/expedition/raid/
        # challenge's own _2 variants), so image search is worth trying
        # again here before falling back to the fixed coordinate.
        self._log("[Macro] Menu open -- searching for Story...")
        self._set_status(action="Clicking Story...")
        try:
            match, name = vision.wait_for_image_any(
                hwnd, STORY_IMAGE_NAMES, timeout=GAMEMODE_CLICK_TIMEOUT, stop_event=stop_event)
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
            self._log(f"[Macro] Story card not found by image search -- falling back to fixed coordinate {STORY_CLICK}.")
            left, top, _, _ = wm.get_window_rect_screen(hwnd)
            self._mouse.click(left + STORY_CLICK[0], top + STORY_CLICK[1])
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
