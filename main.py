"""
Cream's Macro | Anime Expeditions
Run:  python main.py            (launches the docked macro UI)
      python main.py --test     (CLI diagnostics for mouse/keyboard/window)
"""
import os
import re
import sys
import time
import json
import queue
import subprocess
import threading
from datetime import datetime, timedelta, timezone

from core import window as wm
from core import config
from core import constants
from core import keys
from core import settings as cfg
from core import templates as tpl
from core import share
from core import webhook
from core.window import WindowManager
from core.dock import GameDocker
from core.mouse import Mouse
from core.keyboard import Keyboard
from core.logger import Logger
from core.runner import MacroRunner
from core import updater
from core import auto_shop
from core.auto_shop import current_auto_shop_period
from core.runner_constants import (
    BOUNTY_MYTHIC_DEFAULT_REROLLS,
    BOUNTY_MYTHIC_MIN_REROLLS,
    BOUNTY_MYTHIC_MAX_REROLLS,
)

# Imported at module scope (not inside the darwin branches that use it) so the
# macOS-only geometry helpers below can be plain module functions. window_mac
# pulls in Quartz/AppKit, which don't exist on Windows -- hence the guard.
if sys.platform == "darwin":
    from core import window_mac
else:  # pragma: no cover -- Windows/Linux never reach the mac layout paths
    window_mac = None

wm.set_dpi_aware()
# Kill mss's CAPTUREBLT flag before the first screen grab -- with it set,
# every ~0.3s vision poll forces recording overlays (NVIDIA ShadowPlay etc.)
# to redraw, which users see as constant white flashing (see
# core.window_win.disable_mss_captureblt).
wm.disable_mss_captureblt()


def _debug_dir() -> str:
    # Every Settings > Debug capture (screenshot, reward-region preview)
    # lands here instead of loose next to main.py -- one folder to check,
    # and it stays out of the way of the actual source files. Writable, so
    # APP_DIR (see core.constants), not wherever a frozen build unpacks to.
    path = os.path.join(constants.APP_DIR, "debug")
    os.makedirs(path, exist_ok=True)
    return path

# Mirrors core.rewards.SCROLLBAR_PROBE/SCROLLBAR_COLOR (shared with core.
# runner's automatic post-match reward read) -- kept as a plain literal here
# rather than importing core.rewards at module level, which would force its
# cv2/numpy import eagerly on every launch (including `--test`) instead of
# only when a reward read actually happens, same as every other core.*
# import in this file being deferred into the function that needs it.
REWARD_SCROLLBAR_PROBE = (710, 428, 4, 2)  # (x, y, width, height)
REWARD_SCROLLBAR_COLOR = 0x373737

# Image Manager (Settings > General > Image Search) categories: tab key ->
# (subfolder under Assets/, label shown on the tab). A whitelist, not an
# os.listdir, on purpose -- only these two folders hold image-SEARCH
# reference crops (one folder per searched name, see core.vision.
# template_variant_paths), and the keys double as path components in the
# save/delete endpoints below, so an unexpected value must never reach a
# filesystem path. Assets/map (the Place Unit picker's full map art) and
# Assets/item_icons (reward icon matching) are different systems entirely
# and deliberately not editable from here.
IMAGE_MANAGER_CATEGORIES = {
    "ui": ("ui", "UI Buttons"),
    "maps": ("maps", "Map Names"),
    # Reusable images the Detect block searches for. Saved once here, then
    # referenced by name from any Detect block (core/detect.py). Same
    # folder-per-name layout every other category uses -- Assets/detect/<name>/.
    "detect": ("detect", "Detection Images"),
}

GUI_TITLE = "Cream's Macro | Anime Expeditions"
PANEL_WIDTH = 400
TITLEBAR_H = 44  # custom HTML titlebar, since the window is frameless (no native OS titlebar)
LOGS_H = 160  # log strip under the docked Roblox window, same width as the game
GUI_WIDTH_FULL = config.FIXED_WIN_W + PANEL_WIDTH
GUI_WIDTH_COMPACT = PANEL_WIDTH
GUI_HEIGHT_FULL = TITLEBAR_H + config.FIXED_WIN_H + LOGS_H
GUI_HEIGHT_COMPACT = TITLEBAR_H + 380  # tall enough for the waiting screen's full stack (emblem +
# expanding ping rings + tag + title + status + Skip + version badge) -- 280 clipped the emblem's
# animation and the bottom rows

# F7 compact view: trim the window to EXACTLY the docked game plus the bottom
# control strip, dropping the empty side-panel column and the log gap. Width
# is the game's own width and height is titlebar + game + strip, so the game
# is never clipped (only the empty margins are removed) or hidden -- it stays
# visible and clickable, and docking is never touched. Strip height mirrors
# #compact-strip's height in ui/style.css.
COMPACT_STRIP_H = 50
GUI_WIDTH_COMPACT_FIT = config.FIXED_WIN_W
GUI_HEIGHT_COMPACT_FIT = TITLEBAR_H + config.FIXED_WIN_H + COMPACT_STRIP_H

# ── macOS side-by-side geometry ─────────────────────────────────────────────
# On Windows the game is a child window INSIDE ours, so one window size covers
# both (GUI_*_FULL above). macOS can't embed another app's window at all (see
# core/dock.py), so panel and game are two top-level windows sharing the
# screen, and the panel's size is whatever the game doesn't need -- which is a
# different number on every Mac, hence computed at runtime from the visible
# frame rather than baked in as a constant.
MAC_GAP = 20  # breathing room BETWEEN the panel and the game -- also grabbable space to drag either
MAC_MARGIN = 24  # inset both windows from the top/left screen edges so their title bars aren't jammed
                 # against the menu bar / edge, which makes them fiddly to drag (reported)
MAC_PANEL_MIN_W = PANEL_WIDTH  # narrower than the Windows panel column and it stops being usable
MAC_PANEL_MAX_W = 560  # past this the single-column dashboard just looks stretched
UI_INDEX = os.path.join(constants.UI_DIR, "index.html")
LOGS_WINDOW_HTML = os.path.join(constants.UI_DIR, "logs_window.html")
WAVE_MONITOR_HTML = os.path.join(constants.UI_DIR, "wave_monitor.html")
LOGO_ICO = os.path.join(constants.BUNDLE_DIR, "logo.ico")
LOG_HISTORY_LIMIT = 500  # caps what a freshly popped-out window gets replayed with

# Log lines are coalesced and pushed to the UI in ~100ms batches (see
# Api._log_flush_worker) to avoid one evaluate_js IPC round-trip per line
# during bursty logging. A line whose text mentions one of these is flushed
# immediately instead of waiting for the next tick -- an error should show
# now, and if it's the last thing before a crash/shutdown the tick might not
# run at all.
_CRITICAL_LOG_KEYWORDS = ("error", "critical", "exception", "failed")


def _is_critical_log(message: str) -> bool:
    low = message.lower()
    return any(k in low for k in _CRITICAL_LOG_KEYWORDS)

# Auto-reopen throttle: after a run's Roblox window closes, the dock watchdog
# relaunches the game via deep link, but a fresh Roblox can take a good while
# to boot and show a window -- this is how long to wait before trying the
# launch again, so a slow startup isn't mistaken for a failed launch and
# spammed with more launches on top of it.
ROBLOX_RELAUNCH_COOLDOWN = 60.0

HOTKEY_DEFAULTS = {
    "toggle_game": "f4", "skip_waiting": "", "macro_start": "f1", "macro_stop": "f2", "macro_pause": "f5",
    "debug_screenshot": "f3",
    # Sent to Roblox by an Auto Upgrade Unit block whose Input is set to
    # Hotkey. This is deliberately not registered as an app-wide shortcut
    # in _register_hotkeys: it belongs to the game, not the macro UI.
    "game_auto_upgrade": "",
    # Toggles the Image Manager from anywhere -- capturing a missing crop
    # right when a search fails shouldn't need clicking back through
    # Settings > General first.
    "image_manager": "f6",
    # Collapses the whole dashboard to a small always-on-top strip (and back)
    # -- for when the macro's running fine and the full UI is just clutter.
    "toggle_compact": "f7",
}

# Stage-detail panel (shown after clicking a stage row on the Select Stage
# screen): the Normal/Hard difficulty toggle is always at a fixed spot, no
# image search needed, just like the Story card and stage rows before it.
# Enter Matchmaking gets an image search (over a region, not a blind click)
# since its exact readiness isn't otherwise confirmed the way nav_back/
# nav_select_stage confirm earlier screens. All exposed as settings (not
# hardcoded) since a game update could shift any of these -- see
# get_macro_coords/reset_macro_coords and Settings > Debug > Macro Coordinates.
MACRO_COORD_DEFAULTS = {
    "difficulty_normal_x": 311, "difficulty_normal_y": 315,
    "difficulty_hard_x": 364, "difficulty_hard_y": 315,
    "matchmaking_region_x": 277, "matchmaking_region_y": 543,
    "matchmaking_region_w": 437, "matchmaking_region_h": 45,
    # Every other fixed click point the runner uses, same override story --
    # mirrors core.runner's DEFAULT_COORDS (which documents what each one
    # is); all in the docked window's 1152x756 client space, each pickable
    # from a captured Roblox screenshot via the Pick buttons in Settings >
    # Debug > Macro Coordinates.
    "story_click_x": 666, "story_click_y": 147,
    "stage_row_x": 246, "stage_row_y": 230, "stage_row_height": 56,
    "act_row_x": 250, "act_row_y": 267, "act_row_height": 129,
    "challenge_stage_1_x": 460, "challenge_stage_1_y": 277,
    "challenge_stage_2_x": 460, "challenge_stage_2_y": 400,
    "challenge_stage_3_x": 460, "challenge_stage_3_y": 533,
    "expedition_difficulty_x": 441, "expedition_difficulty_y": 524,
    "team_loadout_x": 800, "team_loadout_y": 324, "team_loadout_row_height": 126,
    # Optional override for the first Teams click. None means use the live
    # image match center; the Macro Coordinates picker can save a safer point
    # inside the button for layouts where its lower/inner area registers more
    # reliably.
    "team_button_x": None, "team_button_y": None,
    "screen_middle_x": 576, "screen_middle_y": 378,
    "unit_info_reset_x": 3, "unit_info_reset_y": 3,
}

# Settings > Debug > "Reward Reader"/"Game Stats": OCR capture regions for
# the Victory screen. Same "expose + reset" treatment as MACRO_COORD_DEFAULTS
# above, for the same reason -- a UI change in the game shifts these too.
REWARD_REGION_DEFAULTS = {"x": 212, "y": 429, "width": 504, "height": 106}
STATS_REGION_DEFAULTS = {"x": 210, "y": 337, "width": 509, "height": 57}

RUN_HISTORY_LIMIT = 50  # oldest entries drop off past this -- a running log, not a permanent archive

# Challenge tab (Settings-adjacent, but its own screen -- see get_challenge_
# settings): Regular Challenge has 3 fixed stage slots that each rotate
# through the Story maps over time, so config/count-tracking is
# keyed by MAP (which macro to run for it, how many times it's been played
# today) while the 3 slots are just simple on/off toggles for "attempt
# whatever's in this slot". CHALLENGE_STORY_MAPS matches TASK_DATA.story's
# maps in ui/app.js -- a map the game can land on but this list omits gets no
# Story Map Setup row, so setup_ready reports green while that destination has
# no macro at all (test_challenge_maps.py guards the three copies). Daily
# counts and the once-a-day Daily Challenge use the game's shared 00:00 UTC
# rollover; the independent Regular Challenge stage-availability clock still
# rotates every :00/:30.
CHALLENGE_STORY_MAPS = ["School Grounds", "Rose Kingdom", "Fairy King Forest", "King's Tomb", "Flower Forest", "East Town"]
CHALLENGE_STAGE_SLOTS = ["1", "2", "3"]
CHALLENGE_DAILY_CAP = 10  # fixed, not user-editable -- see get_challenge_settings
CHALLENGE_RESET_SCHEDULE = "utc_midnight_v1"
BOUNTY_STORY_MAPS = list(CHALLENGE_STORY_MAPS)
BOUNTY_DAILY_TOTAL = 10
BOUNTY_RESET_SCHEDULE = CHALLENGE_RESET_SCHEDULE


def _current_challenge_reset_period(now: float = None) -> str:
    """Identifier for the game day containing *now*.

    Anime Expeditions rolls its daily state over at 00:00 UTC.  Deriving the
    period from UTC makes every player cross the boundary at the same instant,
    regardless of their computer's timezone or daylight-saving setting.
    """
    now = time.time() if now is None else now
    return datetime.fromtimestamp(now, timezone.utc).date().isoformat()


def _format_ago(epoch) -> str:
    """Turns a stored epoch timestamp into "just now"/"5m ago"/"3h ago"/
    "2d ago" -- computed fresh on every get_status() call (not stored as
    text) so it stays accurate as time passes between polls."""
    if not epoch:
        return ""
    delta = max(0, time.time() - epoch)
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def _current_challenge_window_start(now: float = None) -> float:
    """Epoch seconds for the most recent :00 or :30 mark (local time) --
    Regular Challenge resets on this single fixed clock, the same for all
    3 stage slots (not a per-slot timer). A slot is "ready" if it hasn't
    been played since this timestamp -- see get_challenge_settings, which
    is the only place that reads this."""
    now = time.time() if now is None else now
    local = time.localtime(now)
    minute = 0 if local.tm_min < 30 else 30
    return time.mktime((local.tm_year, local.tm_mon, local.tm_mday, local.tm_hour, minute, 0,
                          local.tm_wday, local.tm_yday, local.tm_isdst))


def _time_until_challenge_ready(challenge: dict) -> str:
    """"Ready" if Daily Challenge or any enabled Regular slot is ready.

    Regular slots rotate at :00/:30. Daily Challenge becomes ready at the
    shared 00:00 UTC game reset. The shortest relevant wait is returned
    without storing a countdown, so a long-running app crosses either
    boundary without a restart.
    """
    daily = challenge.get("daily") or {}
    daily_enabled = bool(daily.get("enabled"))
    if daily_enabled and daily.get("ready"):
        return "Ready"

    cap = challenge.get("cap", 0)
    any_enabled = False
    any_uncapped = False
    if challenge.get("enabled"):
        for info in challenge.get("stages", {}).values():
            if not info.get("enabled"):
                continue
            any_enabled = True
            if cap and info.get("count", 0) >= cap:
                continue
            any_uncapped = True
            if info.get("ready"):
                return "Ready"

    if not any_enabled and not daily_enabled:
        return "No stages enabled"
    if not any_uncapped and not daily_enabled:
        return "All capped"

    now = time.time()
    waits = []
    if any_uncapped:
        local = time.localtime(now)
        secs_into_hour = local.tm_min * 60 + local.tm_sec
        waits.append((1800 - secs_into_hour) if secs_into_hour < 1800 else (3600 - secs_into_hour))
    if daily_enabled:
        utc_now = datetime.fromtimestamp(now, timezone.utc)
        next_reset = (utc_now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        waits.append(max(1, int(next_reset.timestamp() - now)))

    remaining = min(waits)
    hours, remainder = divmod(int(remaining), 3600)
    mins, secs = divmod(remainder, 60)
    return f"{hours:02d}:{mins:02d}:{secs:02d}" if hours else f"{mins:02d}:{secs:02d}"


def _get_build_info() -> str:
    """A "sub-version" for the startup log line, below the granularity of
    VERSION (which only bumps on tagged releases) -- the exact git commit
    (+dirty flag for uncommitted local changes) when running from source,
    since that's most of this app's own testing between releases and a
    pasted debug.log with no way to tell WHICH of several untagged fixes
    it came from is a lot less useful. A packaged exe has no .git folder
    (see core.constants -- BUNDLE_DIR is a onefile build's temp extraction
    dir), so this just falls back to "release build" there instead of
    failing loudly over something that was never going to work."""
    try:
        repo_dir = os.path.dirname(os.path.abspath(__file__))
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=repo_dir, capture_output=True, text=True, timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if commit.returncode != 0:
            return "release build"
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo_dir, capture_output=True, text=True, timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        suffix = "+dirty" if dirty.returncode == 0 and dirty.stdout.strip() else ""
        return f"src {commit.stdout.strip()}{suffix}"
    except Exception:
        return "release build"


def _mac_panel_layout() -> dict:
    """Where the panel and the Roblox window go on macOS, in top-left origin
    points. Only meaningful on darwin -- see the MAC_* constants above.

    The panel takes the left strip and the game sits to its right at the
    reference size, both inside the *visible* frame so neither hides under the
    menu bar or the Dock. "expanded" width is the whole visible width: the
    non-Dashboard screens (Task, Macro Manager, Challenge, Settings) are
    multi-column layouts with nothing to look at beside them, so they get the
    full screen instead of a strip -- see Api.set_panel_expanded.

    On a display too narrow for both, panel width floors at MAC_PANEL_MIN_W and
    the game simply overflows the right edge; the startup check in _launch_ui
    already warns about that case rather than silently arranging off-screen."""
    x, y, width, height = window_mac.get_visible_frame()
    # Inset both windows from the top/left screen edges so there's grabbable
    # empty space to drag them -- parked flush against the menu bar and edge
    # they're fiddly to move on macOS (reported). Applied only when the
    # display still has room for the panel + game + gap afterward, so a tight
    # screen keeps a working (edge-to-edge) layout instead of shoving the game
    # off-screen; the arithmetic keeps the game's right/bottom edges exactly
    # where they were, so the inset only ever eats slack, never the game.
    if (width >= MAC_PANEL_MIN_W + config.FIXED_WIN_W + MAC_GAP + MAC_MARGIN
            and height >= config.FIXED_WIN_H + MAC_MARGIN):
        x += MAC_MARGIN
        y += MAC_MARGIN
        width -= MAC_MARGIN
        height -= MAC_MARGIN
    panel_w = max(MAC_PANEL_MIN_W, min(MAC_PANEL_MAX_W, width - config.FIXED_WIN_W - MAC_GAP))
    return {
        "x": x, "y": y,
        "panel_w": panel_w, "panel_h": height,
        "expanded_w": width,
        "game_x": x + panel_w + MAC_GAP, "game_y": y,
    }


def _capture_game_region(hwnd: int, region: dict):
    """Read the game's pixels for a settings-calibrated region.

    Region dicts from Settings > Debug are stored in game-client coordinates
    (see get_reward_region / get_stats_region): offsets from the game
    window's own top-left. On Windows the game is a child window inside this
    one, so the screen-space rect is the game's screen origin plus the
    region. On macOS the game is a separate top-level window that the panel
    can cover when it is expanded (see set_panel_expanded), so a screen grab
    would read the panel -- there we read the window's own backing store
    through vision's window-content path, which uses the same client-space
    convention. Raises if the window capture comes back empty so callers'
    existing error handling treats it as a failed capture.
    """
    if sys.platform == "darwin":
        from core.ocr import capture_region_from_window
        image = capture_region_from_window(
            hwnd, region["x"], region["y"], region["width"], region["height"])
        if image is None:
            raise RuntimeError("window capture returned no image")
        return image
    from core.ocr import capture_region
    game_left, game_top, _, _ = wm.get_window_rect_screen(hwnd)
    return capture_region(
        game_left + int(region["x"]), game_top + int(region["y"]),
        int(region["width"]), int(region["height"]))


def _game_region_color_matches(hwnd: int, x: int, y: int, width: int, height: int,
                               expected_rgb_hex: int, tolerance: int = 20) -> bool:
    """Color-probe twin of _capture_game_region, for a client-space rect."""
    if sys.platform == "darwin":
        from core.ocr import sample_color_matches_window
        return sample_color_matches_window(
            hwnd, x, y, width, height, expected_rgb_hex, tolerance)
    from core.ocr import sample_color_matches
    game_left, game_top, _, _ = wm.get_window_rect_screen(hwnd)
    return sample_color_matches(
        game_left + int(x), game_top + int(y), int(width), int(height),
        expected_rgb_hex, tolerance)


class Api:
    """Exposed to the frontend as `pywebview.api.*`: the JS <-> Python bridge.
    Grows as the task/placement/upgrade systems get built; for now it just
    reports docking status so the UI has something real to show."""

    def __init__(self):
        self._window = None
        self._log_window = None
        self._wave_window = None  # the Wave Monitor pop-out (see pop_out_wave_monitor)
        self._log_history = []
        self.docker = GameDocker()
        # Cutout mode (Settings > Debug, Windows only, applies at launch):
        # Roblox stays a top-level window glued BEHIND a literal hole cut in
        # this GUI over the game slot, instead of being reparented inside it
        # -- see core/dock.py's cutout notes. Captures must read the game's
        # own window contents in this mode (the screen shows our solid GUI
        # whenever the hole is closed), hence force_window_capture.
        _cfg = cfg.load()
        # Flicker-free capture: read the game via PrintWindow (its own backing
        # store) instead of a screen BitBlt, which flashes the display white
        # on some GPU/fullscreen-optimization setups. Default ON on Windows
        # (the fix); the capture layer auto-falls-back to screen grab if
        # PrintWindow renders black on a given setup (see
        # vision.capture_game_gray). macOS already always uses window capture.
        if sys.platform != "darwin" and _cfg.get("flicker_free_capture", True):
            from core import vision
            vision.force_window_capture()
        # Per-image match-threshold overrides (Image Manager sensitivity),
        # applied before any search runs.
        from core import vision as _vision
        _vision.set_name_thresholds(_cfg.get("image_thresholds", {}))
        self.game_cutout = sys.platform != "darwin" and bool(_cfg.get("game_cutout", False))
        # WGC capture (see core/wgc_capture.py) fixes black-frame capture on
        # hardware-accelerated / flip-model Roblox. Opt-in (default off): it
        # reads the game window by title, so it needs Roblox to stay a
        # top-level window -- enabling it therefore also forces cutout mode.
        # Windows-only. Off by default so nothing changes for the majority
        # whose stock capture works fine.
        self.use_wgc_capture = sys.platform == "win32" and bool(_cfg.get("use_wgc_capture", False))
        if self.use_wgc_capture:
            self.game_cutout = True  # WGC needs the game top-level -> cutout, not the child-reparent dock
            from core import wgc_capture
            wgc_capture.set_enabled(True)
        self.docker.cutout = self.game_cutout
        self._cutout_game_visible = False  # show_game/hide_game drive this; watchdog re-glues only while visible
        # NOTE for future attempts: a LITERAL see-through slot was tried two
        # ways and both are dead ends over this UI stack -- SetWindowRgn is
        # bypassed by WebView2's DirectComposition (on the top level AND on
        # the Chrome child hwnds), and LWA_COLORKEY only keys content in the
        # GDI redirection surface, which GPU-composited WebView2 skips
        # (behavior verified live either way, including under
        # --disable-gpu-compositing / --disable-gpu). A panel-collapse idle
        # layout was also tried and reverted (read as broken UI) -- with no
        # game docked, cutout mode simply shows the normal layout with an
        # empty slot.
        if self.game_cutout:
            from core import vision
            vision.force_window_capture()
        self.game_hwnd = None
        self.gui_hwnd = None
        # Manual multi-instance attach (Settings > Debug > "Select Roblox
        # Window") -- see _dock_watchdog and attach_roblox_window/
        # detach_roblox_window below. pinned_hwnd forces the watchdog's next
        # dock to a specific window instead of whatever find_roblox_window()
        # would grab on its own; dock_suspended stops the watchdog from
        # instantly re-attaching after an explicit Un-Attach.
        self.pinned_hwnd = None
        self.dock_suspended = False
        # Auto-reopen/auto-restart after Roblox closes mid-run (see
        # _dock_watchdog): _resume_after_relaunch is armed the moment a live
        # run's window vanishes and drives both reopening the game and picking
        # the run back up once it's re-docked; _roblox_relaunch_at throttles
        # the deep-link launches (see ROBLOX_RELAUNCH_COOLDOWN). Cleared by an
        # explicit Stop so closing Roblox to quit doesn't get undone.
        self._resume_after_relaunch = False
        self._roblox_relaunch_at = 0.0
        # macOS side-by-side layout state (see set_panel_expanded): the panel
        # only starts trading width against the game once it has actually been
        # arranged, and the lock keeps the screen-switch resizes from
        # interleaving with the dock watchdog's own re-arrange.
        self._mac_panel_ready = False
        self._mac_panel_width = None  # last applied width, so repeat calls are free
        self._mac_geometry_lock = threading.Lock()
        self.stopping = threading.Event()
        # Log lines queue here and a background worker flushes them to the UI
        # in batches (see push_log / _log_flush_worker) -- one evaluate_js per
        # ~100ms instead of one per line. Daemon so it dies with the process;
        # it also exits on self.stopping (set on shutdown).
        self._log_queue = queue.Queue()
        self._log_flush_lock = threading.Lock()
        self._log_thread = threading.Thread(target=self._log_flush_worker, daemon=True)
        self._log_thread.start()
        self.logger = Logger()
        self.session_start = time.time()
        self._all_time_base = cfg.load().get("all_time_seconds", 0)
        self._on_hotkeys_changed = None
        self.mouse = Mouse()
        self.keyboard = Keyboard()
        self._path_test_stop = None
        self._pending_recording_events = None  # stopped-but-not-yet-named Record block capture (see stop_input_capture)
        # Apply the persisted Macro Speed delay before anything can click
        # (see core.pacing + set_setting's live-update hook).
        from core import pacing
        pacing.set_action_delay_ms(cfg.load().get("action_delay_ms", 0))
        # Live readout for the Dashboard's status panel -- get_status() merges
        # this over its placeholder defaults; the runner is the only thing
        # that ever writes to it (via the set_status callback below), one
        # dict instead of a pile of separate instance attributes since it's
        # just read back out as a dict anyway.
        self._run_status = {
            "current_task": "-", "current_repeat": "-", "map": "-", "action": "Idle",
            "mode": "-", "stage": "-", "difficulty": "-", "play_mode": "-", "macro": "-",
        }
        # Session win/loss counts -- in-memory only, reset every launch, same
        # convention as session_start/elapsed time above. All-time counts and
        # run_history persist in settings.json instead (see _record_match_result).
        self._session_wins = 0
        self._session_losses = 0
        # Populated by a background GitHub check kicked off shortly after
        # launch (see _check_for_update_background) -- "not available" until
        # then, so an early get_update_info() poll from the UI just no-ops
        # instead of racing the check.
        self._update_info = {"available": False}
        # Populated by apply_update's background thread, polled by the UI
        # to drive the update modal's progress bar (see get_update_progress).
        self._update_progress = {}
        self.runner = MacroRunner(
            self.mouse, self.keyboard, self.push_log, self._set_run_status, self._record_match_result,
            self.get_challenge_settings, self.mark_challenge_stage_played, self._run_stats_snapshot,
            self.get_crafting_settings, self.set_crafting_count, self.get_bounty_settings,
            self.set_bounty_remaining, self.get_fuel_settings, self.mark_fuel_refill_result,
            self.get_hotkeys,
            self.get_auto_shop_settings, self._save_auto_shop_item_state,
            self._save_auto_shop_shop_state)

    def _run_stats_snapshot(self) -> dict:
        # Fed to the runner's match-result webhook so it can report the same
        # session/all-time win-loss picture the Dashboard shows, plus the
        # session runtime and app version -- read fresh at send time (a run
        # can span hours) rather than passed once at Start. Called right
        # after _record_match_result has already bumped the counters for the
        # match being reported, so these totals include it.
        data = cfg.load()
        try:
            challenge = self.get_challenge_settings()
            challenge_enabled = challenge.get("enabled") or (challenge.get("daily") or {}).get("enabled")
            time_until_challenge = (_time_until_challenge_ready(challenge)
                                     if challenge_enabled else "Disabled")
        except Exception:
            time_until_challenge = "Disabled"
        # run_history is newest-first (see _record_match_result) -- reversed to
        # oldest->newest booleans so the card's activity grid reads left (old)
        # to right (recent), GitHub-contribution style.
        history = data.get("run_history", [])
        return {
            "session_wins": self._session_wins,
            "session_losses": self._session_losses,
            "all_time_wins": data.get("all_time_wins", 0),
            "all_time_losses": data.get("all_time_losses", 0),
            "session_start": self.session_start,
            "version": updater.get_current_version(),
            "time_until_challenge": time_until_challenge,
            "results": [h.get("result") == "win" for h in reversed(history)],
            "runs_per_hour": self._calculate_runs_per_hour(history),  # Runs per hour rate over rolling window
        }

    def reset_run_status(self, action: str = "Idle") -> None:
        """Resets all task-specific status fields to default '-' placeholders while setting the action text."""
        self._run_status = {
            "current_task": "-",
            "current_repeat": "-",
            "map": "-",
            "action": action,
            "mode": "-",
            "stage": "-",
            "difficulty": "-",
            "play_mode": "-",
            "macro": "-",
        }

    def _set_run_status(self, **kwargs) -> None:
        action = kwargs.get("action")
        should_reset = kwargs.pop("reset", False)
        if should_reset or (action and (action == "Idle" or action.startswith("Stopped") or action.startswith("Completed")) and "current_task" not in kwargs):
            new_status = {
                "current_task": "-",
                "current_repeat": "-",
                "map": "-",
                "action": action if action else "Idle",
                "mode": "-",
                "stage": "-",
                "difficulty": "-",
                "play_mode": "-",
                "macro": "-",
            }
            new_status.update(kwargs)
            self._run_status = new_status
        else:
            self._run_status.update(kwargs)
        self._pending_path_events = None  # stopped-but-not-yet-named recording (see stop_path_capture)

    def set_window(self, window):
        self._window = window

    def get_version(self) -> str:
        return updater.get_current_version()

    def get_display_scale(self) -> dict:
        # Dashboard's scale-warning popup (see showScaleWarning in
        # ui/app.js) asks for this fresh rather than being passed a value
        # up front, same push_ui-then-poll-for-details pattern as the
        # update popup's get_update_info.
        return {"percent": wm.get_display_scale_percent()}

    def get_update_info(self) -> dict:
        # Populated by a background check kicked off a few seconds after
        # launch (see _check_for_update_background) -- polled once by the
        # UI on startup rather than re-hitting GitHub's API on every status
        # tick. Defaults to "not available" until that check actually lands.
        return self._update_info

    def check_for_updates(self) -> dict:
        # Settings > "Check for Updates" -- an on-demand re-check, same
        # background-thread pattern as the startup one so a slow/failed
        # GitHub request can't freeze the UI.
        def run():
            self._update_info = updater.check_for_update(log=self.push_log)
        threading.Thread(target=run, daemon=True).start()
        return {"ok": True}

    def apply_update(self) -> dict:
        if not self._update_info.get("available"):
            return {"ok": False, "reason": "no_update"}
        if self._update_progress.get("phase") in ("downloading", "staging", "restarting"):
            return {"ok": False, "reason": "already_updating"}
        # Runs in the background so the UI can poll get_update_progress()
        # and show a real progress bar/animation instead of a static
        # "Updating..." label with no feedback for however long the
        # download takes -- that dead air (window vanishes, then just a
        # wait with nothing visible) is what read as broken/"scary".
        self._update_progress = {"phase": "downloading", "percent": 0, "message": "Starting download..."}
        threading.Thread(target=self._apply_update_background, daemon=True).start()
        return {"ok": True}

    def _apply_update_background(self) -> None:
        def on_progress(downloaded: int, total: int) -> None:
            mb_downloaded = downloaded / (1024 * 1024)
            if total:
                self._update_progress = {
                    "phase": "downloading",
                    "percent": round(downloaded / total * 100),
                    "message": f"Downloading update... {mb_downloaded:.1f} / {total / (1024 * 1024):.1f} MB",
                }
            else:
                # No Content-Length header -- can't show a percentage, so
                # the JS side falls back to an indeterminate spinner.
                self._update_progress = {
                    "phase": "downloading",
                    "percent": None,
                    "message": f"Downloading update... {mb_downloaded:.1f} MB",
                }

        try:
            # Running as a built exe: swap the exe itself -- robocopying
            # loose .py source over a compiled exe's directory wouldn't do
            # anything, it doesn't read source files at runtime. Running
            # from source: the usual source-zip-over-the-install swap.
            if constants.IS_FROZEN:
                if not self._update_info.get("release_zip_url"):
                    msg = ("No release zip attached to this release -- can't self-update the build. "
                           f'Grab it manually: {self._update_info.get("url")}')
                    self.push_log(f"[Update] {msg}")
                    self._update_progress = {"phase": "error", "percent": None, "message": msg}
                    return
                # One download covers the whole update: the new build (the
                # exe on Windows, the whole .app bundle on macOS) is
                # extracted out of the release zip and staged for the swap,
                # and any reference images NEW in this release are add-only
                # merged from that same file (never overwriting the user's
                # own edited/added images -- see core.updater's Assets
                # section) before the restart.
                new_build = updater.download_release_update(
                    self._update_info["release_zip_url"], self.push_log, on_progress)
                self._update_progress = {"phase": "staging", "percent": 100, "message": "Preparing update..."}
                if sys.platform == "darwin":
                    helper_path = updater.stage_app_update(new_build)
                else:
                    helper_path = updater.stage_exe_update(new_build)
            else:
                helper_path = updater.stage_source_update(
                    self._update_info["zip_url"], constants.APP_DIR, self.push_log, on_progress)
                self._update_progress = {"phase": "staging", "percent": 100, "message": "Preparing update..."}
        except Exception as exc:
            self.push_log(f"[Update] Failed to prepare update: {exc}")
            self._update_progress = {"phase": "error", "percent": None, "message": str(exc)}
            return

        self.push_log(f'[Update] Update to {self._update_info["version"]} staged -- restarting to apply it...')
        self._update_progress = {"phase": "restarting", "percent": 100, "message": "Restarting..."}
        # Launch the detached helper BEFORE closing -- it waits for this
        # process to exit before touching any files, but it has to already
        # be running (and thus survive this process going away) first.
        updater.launch_helper(helper_path)
        threading.Timer(0.4, self.close_window).start()

    def get_update_progress(self) -> dict:
        return self._update_progress

    @staticmethod
    def _calculate_runs_per_hour(history: list, current_time: float = None) -> str:
        """Calculates completed runs per hour over a rolling 1-hour window (3600s)."""
        # Return "-" if history is empty or not a list
        if not history or not isinstance(history, list):
            return "-"
        now = current_time if current_time is not None else time.time()
        # Filter valid runs within the 1-hour window (0 to 3600 seconds)
        recent = [
            h for h in history
            if isinstance(h, dict)
            and isinstance(h.get("at"), (int, float))
            and 0 <= (now - h["at"]) <= 3600
        ]
        if not recent:
            return "-"
        oldest_at = min(h["at"] for h in recent)
        time_span = max(now - oldest_at, 60.0)  # Minimum 1 min to prevent division by zero / spikes
        rate = round((len(recent) * 3600.0) / time_span, 1)
        if rate.is_integer():
            return str(int(rate))
        return str(rate)


    def get_status(self) -> dict:
        # current_task/map/action come from the live runner (see
        # _set_run_status); wins/losses/run_history come from
        # _record_match_result -- session counts are in-memory (this
        # instance), all_time + run_history are persisted in settings.json.
        data = cfg.load()
        all_time_wins = data.get("all_time_wins", 0)
        all_time_losses = data.get("all_time_losses", 0)
        history = data.get("run_history", [])
        wins, losses = self._session_wins, self._session_losses
        challenge = self.get_challenge_settings()
        return {
            "docked": self.docker.docked,
            **self._run_status,
            "last_run": _format_ago(history[0]["at"]) if history else "-",
            "runs_per_hour": self._calculate_runs_per_hour(history),
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / (wins + losses) * 100) if (wins + losses) else None,
            "time_until_challenge": (
                _time_until_challenge_ready(challenge)
                if challenge.get("enabled") or (challenge.get("daily") or {}).get("enabled")
                else "Disabled"
            ),
            "all_time_wins": all_time_wins,
            "all_time_losses": all_time_losses,
            "all_time_win_rate": (
                round(all_time_wins / (all_time_wins + all_time_losses) * 100)
                if (all_time_wins + all_time_losses) else None
            ),
            "run_history": [
                {
                    "result": h.get("result"), "map": h.get("map"),
                    "duration": h.get("duration"), "ago": _format_ago(h.get("at")),
                }
                for h in history
            ],
        }

    def get_time_info(self) -> dict:
        return {"session_start": self.session_start, "all_time_base": self._all_time_base}

    def persist_all_time(self) -> None:
        elapsed = time.time() - self.session_start
        cfg.update({"all_time_seconds": self._all_time_base + elapsed})

    def _record_match_result(self, result: str, map_name: str, duration: str) -> None:
        # Called from core.runner (a background thread) right after a
        # Victory/Defeat screen is read -- session counts update in memory
        # immediately; all_time counts and run_history persist to disk so
        # they survive a restart, same split as session_start/all_time_seconds.
        is_win = result == "win"
        if is_win:
            self._session_wins += 1
        else:
            self._session_losses += 1

        data = cfg.load()
        key = "all_time_wins" if is_win else "all_time_losses"
        history = data.get("run_history", [])
        history.insert(0, {"result": result, "map": map_name or "-", "duration": duration or "-", "at": time.time()})
        cfg.update({key: data.get(key, 0) + 1, "run_history": history[:RUN_HISTORY_LIMIT]})

    def get_settings(self) -> dict:
        data = cfg.load()
        return {
            "start_minimized": data.get("start_minimized", False),
            "theme": data.get("theme", "default"),  # legacy combined value -- kept for one-time migration, see app.js
            "theme_base": data.get("theme_base", ""),
            "theme_accent": data.get("theme_accent", ""),
            "story_scroll_power": data.get("story_scroll_power", 3),
            "story_scroll_nudges": data.get("story_scroll_nudges", 8),
            "debug_screenshots": data.get("debug_screenshots", False),
            "action_delay_ms": data.get("action_delay_ms", 0),
            "expedition_color_buttons": data.get("expedition_color_buttons", True),
            "expedition_camera_o_ms": data.get("expedition_camera_o_ms", 100),
            # False until the welcome checklist's "Get Started" -- the UI
            # shows it exactly once per install (see app.js showOnboarding).
            "onboarding_done": data.get("onboarding_done", False),
            # One-time "subscribe to the creator's channel" prompt (see
            # app.js showSubscribePrompt) -- flips true the moment it's
            # dismissed either way, so it never reappears.
            "subscribe_prompted": data.get("subscribe_prompted", False),
            "game_cutout": data.get("game_cutout", False),
            "use_wgc_capture": data.get("use_wgc_capture", False),
            "flicker_free_capture": data.get("flicker_free_capture", True),
            # Reopen Roblox and resume the run if the game closes/crashes
            # mid-run (see _dock_watchdog). Default on -- it's the point of an
            # unattended overnight run surviving a Roblox crash.
            "auto_relaunch_roblox": data.get("auto_relaunch_roblox", True),
            # Optional long-run memory protection. The runner performs this
            # only at a completed-match/lobby boundary, never from a timer
            # thread during FPS-sensitive capture or input.
            "memory_refresh_enabled": data.get("memory_refresh_enabled", False),
            "memory_refresh_hours": data.get("memory_refresh_hours", 4.0),
            # Off by default -- see core.runner._apply_team_loadout_panel.
            # The strict "unitteams" OCR confirmation is correct for most
            # setups; this loosens it to also accept "teams"/"team"/"loadout"
            # for whoever's setup renders the Load Team list title in a way
            # the strict check keeps missing. Opt-in because a looser match
            # risks confirming the panel open on the WRONG screen right
            # before a fixed-coordinate row click.
            "loose_team_ocr_match": data.get("loose_team_ocr_match", False),
        }

    def get_tasks(self) -> list:
        return cfg.load().get("tasks", [])

    def get_macro_coords(self) -> dict:
        data = cfg.load()
        return {k: data.get(k, v) for k, v in MACRO_COORD_DEFAULTS.items()}

    def set_macro_coord(self, key: str, value: int) -> dict:
        if key not in MACRO_COORD_DEFAULTS:
            return {"ok": False}
        cfg.update({key: int(value)})  # atomic -- see cfg.update (fixes coords not saving)
        return {"ok": True}

    def set_macro_coords(self, changes: dict) -> dict:
        """Save several coordinate keys at ONCE (the picker sets x, y and
        row-height together) -- one atomic write instead of three racing
        ones, which is what was losing coordinates."""
        clean = {}
        for key, value in (changes or {}).items():
            if key in MACRO_COORD_DEFAULTS:
                try:
                    clean[key] = int(value)
                except (TypeError, ValueError):
                    continue
        if clean:
            cfg.update(clean)
        return {"ok": True, "saved": list(clean)}

    def clear_macro_coord(self, prefix: str) -> dict:
        """Clear an optional coordinate override back to automatic behavior."""
        if prefix != "team_button":
            return {"ok": False, "reason": "not_optional"}
        cfg.update({"team_button_x": None, "team_button_y": None})
        return {"ok": True, "cleared": ["team_button_x", "team_button_y"]}

    def reset_macro_coords(self) -> dict:
        cfg.update(dict(MACRO_COORD_DEFAULTS))
        return {"ok": True, "coords": dict(MACRO_COORD_DEFAULTS)}

    def debug_matchmaking_region(self) -> dict:
        # Settings > Debug > Macro Coordinates: saves exactly the region
        # core.runner searches for the Enter Matchmaking button, so it can
        # be visually checked/tuned against a reference crop in Assets/ui/.
        from core import vision
        hwnd = self.game_hwnd
        if not hwnd or not wm.is_window(hwnd):
            return {"ok": False, "reason": "no_roblox"}
        data = cfg.load()
        region = (
            data.get("matchmaking_region_x", MACRO_COORD_DEFAULTS["matchmaking_region_x"]),
            data.get("matchmaking_region_y", MACRO_COORD_DEFAULTS["matchmaking_region_y"]),
            data.get("matchmaking_region_w", MACRO_COORD_DEFAULTS["matchmaking_region_w"]),
            data.get("matchmaking_region_h", MACRO_COORD_DEFAULTS["matchmaking_region_h"]),
        )
        path = vision.save_region_debug(hwnd, "enter_matchmaking", region)
        return {"ok": True, "path": path}

    def get_default_walk_paths(self) -> dict:
        # map name -> saved path name: used so a template's Walk Path can be
        # left on Auto for a map that already has a good recorded route,
        # instead of every template that ever runs that map having to pick
        # the same Custom path by hand. Settings > Debug > Pathing manages
        # this list. A few maps ship a known-good default (see
        # core.paths.load_shipped_default_walk_paths /
        # Assets/default_walk_paths.json) -- your own settings.json entry
        # for the same map overrides it, same as core.paths.load_path lets
        # your own recording under the same name override the shipped one.
        from core import paths as walk_paths
        return {**walk_paths.load_shipped_default_walk_paths(), **cfg.load().get("default_walk_paths", {})}

    def set_default_walk_path(self, map_name: str, path_name: str) -> dict:
        defaults = dict(cfg.load().get("default_walk_paths", {}))
        if path_name:
            defaults[map_name] = path_name
        else:
            defaults.pop(map_name, None)
        cfg.update({"default_walk_paths": defaults})
        return {"ok": True, "default_walk_paths": defaults}

    # ---- Challenge tab ----
    def _default_challenge_settings(self) -> dict:
        return {
            "enabled": False,
            "play_mode": "solo",
            "daily": {
                "enabled": False,
                "last_completed_period": "",
            },
            "cap": CHALLENGE_DAILY_CAP,
            # The daily play limit tracks each STAGE SLOT (Regular Challenge
            # #1/#2/#3), not the map -- whichever map is currently rotated
            # into a slot, that slot's own count is what's capped. Slots
            # don't have their own configurable cooldown -- Regular
            # Challenge resets on a single fixed clock (every :00/:30, same
            # for all 3), not a per-slot timer. last_played_at (epoch
            # seconds, 0 = never played) is only used to tell whether a slot
            # has ALREADY been played in the CURRENT window (see
            # _current_challenge_window_start/get_challenge_settings's
            # "ready" field), not to measure an independent duration.
            # Macro Operation assignment stays per-map (see "maps" below)
            # since that's what needs to follow the map around as it
            # rotates through slots.
            "stages": {slot: {"enabled": True, "count": 0, "last_played_at": 0} for slot in CHALLENGE_STAGE_SLOTS},
            "maps": {m: {"macro": ""} for m in CHALLENGE_STORY_MAPS},
            "last_reset_date": _current_challenge_reset_period(),
            "reset_schedule": CHALLENGE_RESET_SCHEDULE,
        }

    def get_challenge_settings(self) -> dict:
        # Regular Challenge's daily play count resets at 00:00 UTC. This is
        # checked whenever status/settings are polled, so a macro left running
        # crosses the boundary without restarting or interrupting its task. If
        # the app was closed at reset time, the first check after launch catches
        # up. This does NOT touch last_played_at -- that's checked against the
        # current :00/:30 window independently of the daily count reset.
        data = cfg.load()
        saved = data.get("challenge") or {}
        defaults = self._default_challenge_settings()
        merged = {**defaults, **saved}
        merged["cap"] = CHALLENGE_DAILY_CAP  # fixed -- ignore any stale saved value from before this was hardcoded
        if merged.get("play_mode") not in ("solo", "matchmaking"):
            merged["play_mode"] = "solo"
        saved_daily = saved.get("daily") or {}
        merged["daily"] = {
            "enabled": bool(saved_daily.get("enabled", False)),
            "last_completed_period": str(saved_daily.get("last_completed_period") or ""),
        }
        window_start = _current_challenge_window_start()
        merged_stages = {}
        for slot in CHALLENGE_STAGE_SLOTS:
            saved_stage = (saved.get("stages") or {}).get(slot)
            # Migrates the old shapes (stages[slot] was a bare bool, or a
            # dict with a now-removed cooldown_minutes field) transparently
            # -- an old settings.json still loads instead of silently
            # losing its enabled/disabled choice.
            if isinstance(saved_stage, dict):
                last_played_at = float(saved_stage.get("last_played_at") or 0)
                merged_stages[slot] = {
                    "enabled": bool(saved_stage.get("enabled", True)),
                    "count": int(saved_stage.get("count") or 0),
                    "last_played_at": last_played_at,
                }
            else:
                merged_stages[slot] = {
                    "enabled": bool(saved_stage) if saved_stage is not None else True,
                    "count": 0, "last_played_at": 0,
                }
            # Computed, not stored -- "haven't played this slot since the
            # current :00/:30 window opened" is what "ready" actually means.
            merged_stages[slot]["ready"] = merged_stages[slot]["last_played_at"] < window_start
        merged["stages"] = merged_stages
        merged_maps = {}
        for m in CHALLENGE_STORY_MAPS:
            saved_map = (saved.get("maps") or {}).get(m) or {}
            merged_maps[m] = {"macro": saved_map.get("macro") or ""}
        merged["maps"] = merged_maps
        merged.update(self._challenge_macro_setup(merged))

        reset_period = _current_challenge_reset_period()
        merged["daily"]["ready"] = merged["daily"]["last_completed_period"] != reset_period
        if saved.get("reset_schedule") != CHALLENGE_RESET_SCHEDULE:
            # Older versions stored the computer's local date. That value
            # cannot be compared safely with a UTC game-day identifier,
            # especially east of UTC. Adopt the current period without
            # clearing counts; the next real UTC boundary will reset them.
            merged["last_reset_date"] = reset_period
            merged["reset_schedule"] = CHALLENGE_RESET_SCHEDULE
            cfg.update({"challenge": merged})
        elif merged.get("last_reset_date") != reset_period:
            for s in merged["stages"].values():
                s["count"] = 0
            merged["last_reset_date"] = reset_period
            cfg.update({"challenge": merged})
            self.push_log("[Challenge] Daily play counts reset.")
        return merged

    def mark_challenge_stage_played(self, stage: str, count_play: bool = True) -> dict:
        # "daily" rests the once-a-day challenge until the next game day.
        # A numbered Regular slot starts its :00/:30 cooldown and bumps its
        # daily count in one write.
        # count_play=False is the LOSS case: the cooldown still applies
        # (retrying the same rotated-in stage right away just loses again
        # -- wait for the next window), but a loss shouldn't eat one of the
        # day's capped plays the way a real completion does.
        if stage == "daily":
            challenge = self.get_challenge_settings()
            challenge["daily"]["last_completed_period"] = _current_challenge_reset_period()
            challenge["daily"]["ready"] = False
            cfg.update({"challenge": challenge})
            return {"ok": True}
        if stage not in CHALLENGE_STAGE_SLOTS:
            return {"ok": False, "reason": "bad_stage"}
        challenge = self.get_challenge_settings()
        challenge["stages"][stage]["last_played_at"] = time.time()
        if count_play:
            challenge["stages"][stage]["count"] += 1
        cfg.update({"challenge": challenge})
        return {"ok": True}

    def set_challenge_enabled(self, enabled: bool) -> dict:
        challenge = self.get_challenge_settings()
        if enabled and not challenge["setup_ready"]:
            challenge["enabled"] = False
            cfg.update({"challenge": challenge})
            missing = ", ".join(challenge["missing_maps"])
            invalid = ", ".join(
                f'{item["map"]} ("{item["macro"]}")'
                for item in challenge["invalid_maps"])
            details = "; ".join(part for part in (
                f"unassigned: {missing}" if missing else "",
                f"missing or old macros: {invalid}" if invalid else "",
            ) if part)
            self.push_log(
                "[Macro] Auto Challenge was not enabled. Assign a saved Macro Operation "
                f"to every Story map first ({details}).")
            return {
                "ok": False,
                "reason": "incomplete_challenge_maps",
                "missing_maps": challenge["missing_maps"],
                "invalid_maps": challenge["invalid_maps"],
            }
        challenge["enabled"] = bool(enabled)
        cfg.update({"challenge": challenge})
        return {"ok": True}

    def set_challenge_play_mode(self, play_mode: str) -> dict:
        if play_mode not in ("solo", "matchmaking"):
            return {"ok": False, "reason": "bad_play_mode"}
        challenge = self.get_challenge_settings()
        challenge["play_mode"] = play_mode
        cfg.update({"challenge": challenge})
        return {"ok": True}

    def set_daily_challenge_enabled(self, enabled: bool) -> dict:
        challenge = self.get_challenge_settings()
        if enabled and not challenge["setup_ready"]:
            challenge["daily"]["enabled"] = False
            cfg.update({"challenge": challenge})
            missing = ", ".join(challenge["missing_maps"])
            invalid = ", ".join(
                f'{item["map"]} ("{item["macro"]}")'
                for item in challenge["invalid_maps"])
            details = "; ".join(part for part in (
                f"unassigned: {missing}" if missing else "",
                f"missing or old macros: {invalid}" if invalid else "",
            ) if part)
            self.push_log(
                "[Macro] Daily Challenge was not enabled. Assign a saved Macro Operation "
                f"to every Story map first ({details}).")
            return {
                "ok": False,
                "reason": "incomplete_challenge_maps",
                "missing_maps": challenge["missing_maps"],
                "invalid_maps": challenge["invalid_maps"],
            }
        challenge["daily"]["enabled"] = bool(enabled)
        cfg.update({"challenge": challenge})
        return {"ok": True}

    def set_daily_challenge_count(self, count) -> dict:
        """Manually set today's once-per-day progress to 0 or 1."""
        try:
            count = int(count)
        except (TypeError, ValueError):
            return {"ok": False, "reason": "bad_count"}
        if count not in (0, 1):
            return {"ok": False, "reason": "bad_count"}
        challenge = self.get_challenge_settings()
        challenge["daily"]["last_completed_period"] = (
            _current_challenge_reset_period() if count else "")
        challenge["daily"]["ready"] = count == 0
        cfg.update({"challenge": challenge})
        return {"ok": True}

    def set_challenge_stage_enabled(self, stage: str, enabled: bool) -> dict:
        if stage not in CHALLENGE_STAGE_SLOTS:
            return {"ok": False, "reason": "bad_stage"}
        challenge = self.get_challenge_settings()
        challenge["stages"][stage]["enabled"] = bool(enabled)
        cfg.update({"challenge": challenge})
        return {"ok": True}

    def set_challenge_stage_count(self, stage: str, count) -> dict:
        # Editable by hand (Challenge screen's Count field) for whenever
        # someone plays a stage manually, outside the macro, and wants the
        # daily count to stay accurate without waiting for the next reset.
        if stage not in CHALLENGE_STAGE_SLOTS:
            return {"ok": False, "reason": "bad_stage"}
        try:
            count = max(0, int(count))
        except (TypeError, ValueError):
            return {"ok": False, "reason": "bad_count"}
        challenge = self.get_challenge_settings()
        challenge["stages"][stage]["count"] = count
        cfg.update({"challenge": challenge})
        return {"ok": True}

    def set_challenge_stage_cooldown(self, stage: str, on_cooldown: bool) -> dict:
        # Challenge screen's per-slot "Set on cooldown" / "Clear cooldown"
        # button. on_cooldown=True marks the slot played for THIS :00/:30
        # window (so the runner skips it until the next window) WITHOUT
        # bumping the daily count -- e.g. you already ran this challenge by
        # hand, or just want to skip it this window without disabling it.
        # on_cooldown=False clears that (last_played_at=0) so it's Ready
        # again right now. Same "ready = last_played_at < window_start" rule
        # get_challenge_settings computes, just driven by hand here.
        if stage not in CHALLENGE_STAGE_SLOTS:
            return {"ok": False, "reason": "bad_stage"}
        challenge = self.get_challenge_settings()
        challenge["stages"][stage]["last_played_at"] = time.time() if on_cooldown else 0
        cfg.update({"challenge": challenge})
        self.push_log(f"[Challenge] Slot #{stage} "
                       f"{'set on cooldown until the next window' if on_cooldown else 'cleared -- Ready now'}.")
        return {"ok": True}

    def set_challenge_map_macro(self, map_name: str, macro: str) -> dict:
        if map_name not in CHALLENGE_STORY_MAPS:
            return {"ok": False, "reason": "bad_map"}
        challenge = self.get_challenge_settings()
        challenge["maps"][map_name]["macro"] = macro or ""
        setup = self._challenge_macro_setup(challenge)
        auto_disabled = bool(
            (challenge.get("enabled") or challenge.get("daily", {}).get("enabled"))
            and not setup["setup_ready"])
        if auto_disabled:
            challenge["enabled"] = False
            challenge["daily"]["enabled"] = False
            self.push_log(
                f'[Macro] Auto Challenge was disabled because "{map_name}" no longer '
                "has a usable Macro Operation.")
        cfg.update({"challenge": challenge})
        return {"ok": True, "auto_disabled": auto_disabled, **setup}

    def reset_challenge_counts(self) -> dict:
        challenge = self.get_challenge_settings()
        for s in challenge["stages"].values():
            s["count"] = 0
            s["last_played_at"] = 0  # also clears cooldown -- every slot becomes available immediately
        challenge["daily"]["last_completed_period"] = ""
        challenge["daily"]["ready"] = True
        challenge["last_reset_date"] = _current_challenge_reset_period()
        challenge["reset_schedule"] = CHALLENGE_RESET_SCHEDULE
        cfg.update({"challenge": challenge})
        self.push_log("[Challenge] Daily status, play counts, and cooldowns reset manually.")
        return {"ok": True}

    # ── Auto Crafting (see core/runner_crafting.py) ──

    def _default_bounty_settings(self) -> dict:
        return {
            "enabled": False,
            "play_mode": "solo",
            "summon_banner": "standard",
            "mythic_only": False,
            "mythic_max_rerolls": BOUNTY_MYTHIC_DEFAULT_REROLLS,
            "remaining": BOUNTY_DAILY_TOTAL,
            "total": BOUNTY_DAILY_TOTAL,
            "last_reset_date": _current_challenge_reset_period(),
            "reset_schedule": BOUNTY_RESET_SCHEDULE,
            "maps": {name: {"macro": ""} for name in BOUNTY_STORY_MAPS},
        }

    @staticmethod
    def _story_macro_setup(settings: dict, map_names) -> dict:
        """Whether every possible Story destination has a usable macro.

        Both Auto Bounty and Auto Challenge choose a Story destination at
        runtime. Starting with only some maps configured therefore guarantees
        that a later objective can enter a battle with no Pre Start blocks and
        no units. Treat the full map assignment as one required setup instead
        of discovering the hole after teleporting.
        """
        maps = settings.get("maps") or {}
        missing_maps = []
        invalid_maps = []
        for map_name in map_names:
            macro_name = str((maps.get(map_name) or {}).get("macro") or "").strip()
            if not macro_name:
                missing_maps.append(map_name)
                continue
            if not tpl.template_exists(macro_name):
                invalid_maps.append({"map": map_name, "macro": macro_name})
                continue
            data = tpl.load_template(macro_name)
            if not isinstance(data.get("blocks"), dict):
                invalid_maps.append({"map": map_name, "macro": macro_name})
        return {
            "setup_ready": not missing_maps and not invalid_maps,
            "missing_maps": missing_maps,
            "invalid_maps": invalid_maps,
        }

    @staticmethod
    def _challenge_macro_setup(settings: dict) -> dict:
        return Api._story_macro_setup(settings, CHALLENGE_STORY_MAPS)

    @staticmethod
    def _bounty_macro_setup(settings: dict) -> dict:
        return Api._story_macro_setup(settings, BOUNTY_STORY_MAPS)

    @staticmethod
    def _save_bounty_settings(settings: dict) -> None:
        """Persist only settings, not the computed setup-status fields."""
        cfg.update({"bounty": {
            "enabled": bool(settings.get("enabled")),
            "play_mode": settings.get("play_mode") or "solo",
            "summon_banner": settings.get("summon_banner") or "standard",
            "mythic_only": bool(settings.get("mythic_only")),
            "mythic_max_rerolls": int(settings.get(
                "mythic_max_rerolls", BOUNTY_MYTHIC_DEFAULT_REROLLS)),
            "maps": settings.get("maps") or {},
        }})

    def get_bounty_settings(self) -> dict:
        saved = cfg.load().get("bounty") or {}
        merged = {**self._default_bounty_settings(), **saved}
        if merged.get("play_mode") not in ("solo", "matchmaking"):
            merged["play_mode"] = "solo"
        if merged.get("summon_banner") not in ("standard", "villain"):
            merged["summon_banner"] = "standard"
        merged["mythic_only"] = bool(merged.get("mythic_only"))
        try:
            merged["mythic_max_rerolls"] = max(
                BOUNTY_MYTHIC_MIN_REROLLS,
                min(
                    BOUNTY_MYTHIC_MAX_REROLLS,
                    int(merged.get(
                        "mythic_max_rerolls", BOUNTY_MYTHIC_DEFAULT_REROLLS)),
                ),
            )
        except (TypeError, ValueError):
            merged["mythic_max_rerolls"] = BOUNTY_MYTHIC_DEFAULT_REROLLS
        try:
            total = max(1, min(99, int(merged.get("total") or BOUNTY_DAILY_TOTAL)))
        except (TypeError, ValueError):
            total = BOUNTY_DAILY_TOTAL
        try:
            remaining = max(0, min(total, int(merged.get("remaining", total))))
        except (TypeError, ValueError):
            remaining = total
        merged["total"] = total
        merged["remaining"] = remaining
        saved_maps = saved.get("maps") or {}
        merged["maps"] = {
            name: {"macro": (saved_maps.get(name) or {}).get("macro") or ""}
            for name in BOUNTY_STORY_MAPS
        }
        merged.update(self._bounty_macro_setup(merged))
        reset_period = _current_challenge_reset_period()
        if saved.get("reset_schedule") != BOUNTY_RESET_SCHEDULE:
            # Adopt the shared UTC game-day schedule without changing a
            # pre-existing count during migration.
            merged["last_reset_date"] = reset_period
            merged["reset_schedule"] = BOUNTY_RESET_SCHEDULE
            cfg.update({"bounty": merged})
        elif merged.get("last_reset_date") != reset_period:
            merged["remaining"] = merged["total"]
            merged["last_reset_date"] = reset_period
            cfg.update({"bounty": merged})
            self.push_log("[Bounty] Daily bounty tracker reset.")
        return merged

    def set_bounty_enabled(self, enabled: bool) -> dict:
        settings = self.get_bounty_settings()
        if enabled and not settings["setup_ready"]:
            settings["enabled"] = False
            self._save_bounty_settings(settings)
            missing = ", ".join(settings["missing_maps"])
            invalid = ", ".join(
                f'{item["map"]} ("{item["macro"]}")'
                for item in settings["invalid_maps"])
            details = "; ".join(part for part in (
                f"unassigned: {missing}" if missing else "",
                f"missing or old macros: {invalid}" if invalid else "",
            ) if part)
            self.push_log(
                "[Macro] Auto Bounty was not enabled. Assign a saved Macro Operation "
                f"to every Story map first ({details}).")
            return {
                "ok": False,
                "reason": "incomplete_bounty_maps",
                "missing_maps": settings["missing_maps"],
                "invalid_maps": settings["invalid_maps"],
            }
        settings["enabled"] = bool(enabled)
        self._save_bounty_settings(settings)
        return {"ok": True}

    def set_bounty_play_mode(self, play_mode: str) -> dict:
        if play_mode not in ("solo", "matchmaking"):
            return {"ok": False, "reason": "bad_play_mode"}
        settings = self.get_bounty_settings()
        settings["play_mode"] = play_mode
        self._save_bounty_settings(settings)
        return {"ok": True}

    def set_bounty_summon_banner(self, banner: str) -> dict:
        if banner not in ("standard", "villain"):
            return {"ok": False, "reason": "bad_banner"}
        settings = self.get_bounty_settings()
        settings["summon_banner"] = banner
        self._save_bounty_settings(settings)
        return {"ok": True}

    def set_bounty_mythic_only(self, enabled: bool) -> dict:
        settings = self.get_bounty_settings()
        settings["mythic_only"] = bool(enabled)
        self._save_bounty_settings(settings)
        return {"ok": True}

    def set_bounty_mythic_max_rerolls(self, value) -> dict:
        try:
            limit = int(value)
        except (TypeError, ValueError):
            return {"ok": False, "reason": "bad_mythic_max_rerolls"}
        if not (BOUNTY_MYTHIC_MIN_REROLLS <= limit <= BOUNTY_MYTHIC_MAX_REROLLS):
            return {"ok": False, "reason": "bad_mythic_max_rerolls"}
        settings = self.get_bounty_settings()
        settings["mythic_max_rerolls"] = limit
        self._save_bounty_settings(settings)
        return {"ok": True}

    def set_bounty_map_macro(self, map_name: str, macro: str) -> dict:
        if map_name not in BOUNTY_STORY_MAPS:
            return {"ok": False, "reason": "bad_map"}
        settings = self.get_bounty_settings()
        settings["maps"][map_name]["macro"] = macro or ""
        setup = self._bounty_macro_setup(settings)
        auto_disabled = bool(settings.get("enabled") and not setup["setup_ready"])
        if auto_disabled:
            settings["enabled"] = False
            self.push_log(
                f'[Macro] Auto Bounty was disabled because "{map_name}" no longer '
                "has a usable Macro Operation.")
        self._save_bounty_settings(settings)
        return {"ok": True, "auto_disabled": auto_disabled, **setup}

    def set_bounty_remaining(self, remaining, total=None) -> dict:
        settings = self.get_bounty_settings()
        if total is not None:
            try:
                settings["total"] = max(1, min(99, int(total)))
            except (TypeError, ValueError):
                return {"ok": False, "reason": "bad_total"}
        try:
            settings["remaining"] = max(
                0, min(settings["total"], int(remaining)))
        except (TypeError, ValueError):
            return {"ok": False, "reason": "bad_remaining"}
        settings["last_reset_date"] = _current_challenge_reset_period()
        settings["reset_schedule"] = BOUNTY_RESET_SCHEDULE
        cfg.update({"bounty": settings})
        return {"ok": True}

    def reset_bounty_remaining(self) -> dict:
        settings = self.get_bounty_settings()
        return self.set_bounty_remaining(
            settings["total"], settings["total"])

    def _default_crafting_settings(self) -> dict:
        from core.runner_constants import CRAFT_SPRITES, CRAFT_DEFAULT_EVERY
        return {
            "enabled": False,
            "every": CRAFT_DEFAULT_EVERY,   # craft after this many qualifying wins
            "count": 0,                     # running win counter toward the next pass
            # One row per sprite, in PRIORITY order (the runner crafts top-down).
            # amount is "max" or a positive int.
            "items": [{"key": k, "enabled": False, "amount": "max"} for k in CRAFT_SPRITES],
        }

    def get_crafting_settings(self) -> dict:
        # Merges saved settings over the defaults and normalizes them, same
        # shape/ownership as get_challenge_settings. The item list is rebuilt
        # preserving the user's saved priority ORDER, with any newly-added
        # sprite appended at the end and any removed one dropped -- so the
        # roster can grow (see CRAFT_SPRITES) without stranding old configs.
        from core.runner_constants import (CRAFT_SPRITES, CRAFT_DEFAULT_EVERY, CRAFT_EVERY_MIN,
                                           CRAFT_EVERY_MAX, CRAFT_AMOUNT_MAX)
        data = cfg.load()
        saved = data.get("crafting") or {}
        merged = {**self._default_crafting_settings(), **saved}
        merged["enabled"] = bool(merged.get("enabled"))
        try:
            merged["every"] = min(CRAFT_EVERY_MAX, max(CRAFT_EVERY_MIN, int(merged.get("every", CRAFT_DEFAULT_EVERY))))
        except (TypeError, ValueError):
            merged["every"] = CRAFT_DEFAULT_EVERY
        try:
            merged["count"] = max(0, int(merged.get("count", 0)))
        except (TypeError, ValueError):
            merged["count"] = 0

        valid = set(CRAFT_SPRITES)
        ordered, seen = [], set()

        def _norm(entry: dict) -> dict:
            amount = entry.get("amount", "max")
            if str(amount).lower() == "max":
                amount = "max"  # canonical lowercase
            else:
                try:
                    amount = min(CRAFT_AMOUNT_MAX, max(1, int(amount)))
                except (TypeError, ValueError):
                    amount = "max"
            return {"key": entry["key"], "enabled": bool(entry.get("enabled", False)), "amount": amount}

        for entry in (saved.get("items") or []):
            if isinstance(entry, dict) and entry.get("key") in valid and entry["key"] not in seen:
                ordered.append(_norm(entry))
                seen.add(entry["key"])
        for k in CRAFT_SPRITES:  # append sprites not in the saved order (new roster additions)
            if k not in seen:
                ordered.append({"key": k, "enabled": False, "amount": "max"})
        merged["items"] = ordered
        return merged

    def set_crafting_count(self, count) -> dict:
        # The runner's callback (bump on a qualifying win / reset to 0 after a
        # pass); also usable by hand. Persists just the counter.
        try:
            count = max(0, int(count))
        except (TypeError, ValueError):
            return {"ok": False, "reason": "bad_count"}
        crafting = self.get_crafting_settings()
        crafting["count"] = count
        cfg.update({"crafting": crafting})
        return {"ok": True}

    def set_crafting_enabled(self, enabled: bool) -> dict:
        crafting = self.get_crafting_settings()
        crafting["enabled"] = bool(enabled)
        cfg.update({"crafting": crafting})
        return {"ok": True}

    def set_crafting_every(self, every) -> dict:
        from core.runner_constants import CRAFT_EVERY_MIN, CRAFT_EVERY_MAX
        try:
            every = min(CRAFT_EVERY_MAX, max(CRAFT_EVERY_MIN, int(every)))
        except (TypeError, ValueError):
            return {"ok": False, "reason": "bad_every"}
        crafting = self.get_crafting_settings()
        crafting["every"] = every
        cfg.update({"crafting": crafting})
        return {"ok": True}

    def set_crafting_item_enabled(self, key: str, enabled: bool) -> dict:
        crafting = self.get_crafting_settings()
        found = False
        for it in crafting["items"]:
            if it["key"] == key:
                it["enabled"] = bool(enabled)
                found = True
                break
        if not found:
            return {"ok": False, "reason": "bad_key"}
        cfg.update({"crafting": crafting})
        return {"ok": True}

    def set_crafting_item_amount(self, key: str, amount) -> dict:
        # amount is the string "max" or a positive integer (typed into the
        # quantity box at craft time).
        from core.runner_constants import CRAFT_AMOUNT_MAX
        if str(amount).lower() == "max":
            amount = "max"
        else:
            try:
                amount = min(CRAFT_AMOUNT_MAX, max(1, int(amount)))
            except (TypeError, ValueError):
                return {"ok": False, "reason": "bad_amount"}
        crafting = self.get_crafting_settings()
        found = False
        for it in crafting["items"]:
            if it["key"] == key:
                it["amount"] = amount
                found = True
                break
        if not found:
            return {"ok": False, "reason": "bad_key"}
        cfg.update({"crafting": crafting})
        return {"ok": True}

    def set_crafting_order(self, order: list) -> dict:
        # Reorder the item list to the given list of keys (the UI's drag/
        # up-down priority control). Unknown keys are ignored; any item not
        # named in `order` keeps its relative place at the end.
        if not isinstance(order, list):
            return {"ok": False, "reason": "bad_order"}
        crafting = self.get_crafting_settings()
        by_key = {it["key"]: it for it in crafting["items"]}
        new_items, seen = [], set()
        for k in order:
            if k in by_key and k not in seen:
                new_items.append(by_key[k])
                seen.add(k)
        for it in crafting["items"]:
            if it["key"] not in seen:
                new_items.append(it)
        crafting["items"] = new_items
        cfg.update({"crafting": crafting})
        return {"ok": True}

    def reset_crafting_count(self) -> dict:
        return self.set_crafting_count(0)

    def test_crafting(self) -> dict:
        # Auto Crafting screen's "Run now" -- one crafting pass immediately
        # against the current game window, ignoring the win threshold and the
        # enabled toggle (see runner_crafting.start_crafting_test / force).
        data = cfg.load()
        coords = {k: data.get(k, v) for k, v in MACRO_COORD_DEFAULTS.items()}
        return self.runner.start_crafting_test(lambda: self.game_hwnd, coords)

    # -- Auto Shop (see core/auto_shop.py and core/runner_shop.py) --

    @staticmethod
    def _auto_shop_item_map(items) -> dict:
        if isinstance(items, dict):
            return items
        if isinstance(items, list):
            return {
                str(item.get("key")): item
                for item in items
                if isinstance(item, dict) and item.get("key")
            }
        return {}

    def _canonical_auto_shop_settings(self, source) -> dict:
        period = current_auto_shop_period()
        source_shops = source.get("shops") if isinstance(source, dict) else {}
        source_shops = source_shops if isinstance(source_shops, dict) else {}
        source_gold = source_shops.get("gold_shop")
        source_gold = source_gold if isinstance(source_gold, dict) else {}
        source_items = self._auto_shop_item_map(source_gold.get("items"))
        normalized = auto_shop.normalize_auto_shop_settings({
            "enabled": source.get("enabled", False) if isinstance(source, dict) else False,
            "shops": {
                "gold_shop": {
                    **source_gold,
                    "items": source_items,
                },
            },
        })

        gold_config = normalized["shops"]["gold_shop"]
        canonical = {
            "enabled": normalized["enabled"],
            "shops": {
                "gold_shop": {
                    "enabled": gold_config["enabled"],
                    "state": {},
                    "items": {},
                },
            },
        }
        for definition in auto_shop.AUTO_SHOP_ITEMS:
            item_key = definition["key"]
            source_item = source_items.get(item_key)
            source_item = source_item if isinstance(source_item, dict) else {}
            config_item = gold_config["items"][item_key]
            canonical["shops"]["gold_shop"]["items"][item_key] = {
                "enabled": config_item["enabled"],
                "target": config_item["target"],
                "state": auto_shop.normalize_item_state(
                    source_item.get("state"),
                    period,
                ),
            }

        shop_state = auto_shop.normalize_shop_state(
            source_gold.get("state"),
            period,
        )
        has_pending = any(
            (item.get("state") or {}).get("status") == auto_shop.STATUS_PENDING
            for item in canonical["shops"]["gold_shop"]["items"].values()
        )
        if has_pending and shop_state.get("status") == auto_shop.STATUS_FAILED_TODAY:
            shop_state = auto_shop.fresh_shop_state(period)
        canonical["shops"]["gold_shop"]["state"] = shop_state

        return canonical

    def _save_auto_shop_settings(self, settings: dict) -> dict:
        canonical = self._canonical_auto_shop_settings(settings)
        cfg.update({"auto_shop": canonical})
        return canonical

    def get_auto_shop_settings(self) -> dict:
        saved = cfg.load().get("auto_shop") or {}
        canonical = self._canonical_auto_shop_settings(saved)
        if canonical != saved:
            cfg.update({"auto_shop": canonical})

        gold_shop = canonical["shops"]["gold_shop"]
        return {
            "enabled": canonical["enabled"],
            "reset_schedule": auto_shop.AUTO_SHOP_RESET_SCHEDULE,
            "shops": {
                "gold_shop": {
                    "name": "Gold Shop",
                    "enabled": gold_shop["enabled"],
                    "state": gold_shop["state"],
                    "items": [
                        {
                            "key": definition["key"],
                            "name": definition["name"],
                            "daily_maximum": definition["stock"],
                            **gold_shop["items"][definition["key"]],
                        }
                        for definition in auto_shop.AUTO_SHOP_ITEMS
                    ],
                },
            },
        }

    def set_auto_shop_enabled(self, enabled: bool) -> dict:
        settings = self.get_auto_shop_settings()
        settings["enabled"] = bool(enabled)
        self._save_auto_shop_settings(settings)
        return {"ok": True}

    def set_auto_shop_shop_enabled(self, shop_key: str, enabled: bool) -> dict:
        settings = self.get_auto_shop_settings()
        if shop_key not in settings["shops"]:
            return {"ok": False, "reason": "bad_shop"}
        settings["shops"][shop_key]["enabled"] = bool(enabled)
        self._save_auto_shop_settings(settings)
        return {"ok": True}

    def set_auto_shop_item_enabled(
            self, shop_key: str, item_key: str, enabled: bool) -> dict:
        settings = self.get_auto_shop_settings()
        shop = settings["shops"].get(shop_key)
        if shop is None:
            return {"ok": False, "reason": "bad_shop"}
        item = next(
            (entry for entry in shop["items"] if entry["key"] == item_key),
            None,
        )
        if item is None:
            return {"ok": False, "reason": "bad_item"}
        item["enabled"] = bool(enabled)
        self._save_auto_shop_settings(settings)
        return {"ok": True}

    def set_auto_shop_item_target(
            self, shop_key: str, item_key: str, target) -> dict:
        settings = self.get_auto_shop_settings()
        shop = settings["shops"].get(shop_key)
        if shop is None:
            return {"ok": False, "reason": "bad_shop"}
        item = next(
            (entry for entry in shop["items"] if entry["key"] == item_key),
            None,
        )
        if item is None:
            return {"ok": False, "reason": "bad_item"}
        try:
            normalized_target = auto_shop.normalize_target(
                target,
                item["daily_maximum"],
            )
        except ValueError:
            return {"ok": False, "reason": "bad_target"}
        previous_target = item["target"]
        item["target"] = normalized_target
        if (
                normalized_target != previous_target
                and (item.get("state") or {}).get("status")
                == auto_shop.STATUS_COMPLETED):
            item["state"]["status"] = auto_shop.STATUS_PENDING
            item["state"]["verification"] = None
        self._save_auto_shop_settings(settings)
        return {"ok": True}

    def reset_auto_shop_item_today(
            self, shop_key: str, item_key: str) -> dict:
        settings = self.get_auto_shop_settings()
        shop = settings["shops"].get(shop_key)
        if shop is None:
            return {"ok": False, "reason": "bad_shop"}
        item = next(
            (entry for entry in shop["items"] if entry["key"] == item_key),
            None,
        )
        if item is None:
            return {"ok": False, "reason": "bad_item"}
        period = current_auto_shop_period()
        item["state"] = auto_shop.fresh_item_state(period)
        shop["state"] = auto_shop.fresh_shop_state(period)
        self._save_auto_shop_settings(settings)
        return {"ok": True}

    def _save_auto_shop_shop_state(self, shop_key: str, state: dict) -> dict:
        settings = self.get_auto_shop_settings()
        shop = settings["shops"].get(shop_key)
        if shop is None:
            return {"ok": False, "reason": "bad_shop"}
        shop["state"] = auto_shop.normalize_shop_state(
            state,
            current_auto_shop_period(),
        )
        self._save_auto_shop_settings(settings)
        return {"ok": True}

    def _save_auto_shop_item_state(
            self, shop_key: str, item_key: str, state: dict) -> dict:
        settings = self.get_auto_shop_settings()
        shop = settings["shops"].get(shop_key)
        if shop is None:
            return {"ok": False, "reason": "bad_shop"}
        item = next(
            (entry for entry in shop["items"] if entry["key"] == item_key),
            None,
        )
        if item is None:
            return {"ok": False, "reason": "bad_item"}
        item["state"] = auto_shop.normalize_item_state(
            state,
            current_auto_shop_period(),
        )
        self._save_auto_shop_settings(settings)
        return {"ok": True}

    # ── Auto Fuel (see core/runner_fuel.py) ──

    @staticmethod
    def _default_fuel_settings() -> dict:
        return {
            "enabled": False,
            "interval_minutes": 0,
            "resources": {
                "resource_drill": {
                    "enabled": False,
                    "amount": "max",
                    "last_refilled_at": 0.0,
                    "next_attempt_at": 0.0,
                },
                "gold_mine": {
                    "enabled": False,
                    "amount": "max",
                    "last_refilled_at": 0.0,
                    "next_attempt_at": 0.0,
                },
            },
            "paths": {
                "hub_to_resource_drill": "Auto Fuel - Hub to Resource Drill",
                "hub_to_gold_mine": "Auto Fuel - Hub to Gold Mine",
                "resource_drill_to_gold_mine": "Auto Fuel - Resource Drill to Gold Mine",
            },
        }

    def _save_fuel_settings(self, fuel: dict) -> dict:
        """Persist only the canonical fields, never the derived timer values."""
        from core.runner_constants import FUEL_PATH_KEYS, FUEL_RESOURCES

        canonical = {
            "enabled": bool(fuel.get("enabled")),
            "interval_minutes": int(fuel.get("interval_minutes") or 0),
            "resources": {},
            "paths": {},
        }
        for key in FUEL_RESOURCES:
            source = (fuel.get("resources") or {}).get(key) or {}
            canonical["resources"][key] = {
                "enabled": bool(source.get("enabled")),
                "amount": source.get("amount", "max"),
                "last_refilled_at": float(source.get("last_refilled_at") or 0),
                "next_attempt_at": float(source.get("next_attempt_at") or 0),
            }
        for key in FUEL_PATH_KEYS:
            canonical["paths"][key] = str((fuel.get("paths") or {}).get(key) or "")
        cfg.update({"fuel_refill": canonical})
        return canonical

    def get_fuel_settings(self) -> dict:
        from core.runner_constants import (
            FUEL_AMOUNT_MAX,
            FUEL_INTERVAL_MINUTES_MAX,
            FUEL_INTERVAL_SECONDS,
            FUEL_PATH_KEYS,
            FUEL_RESOURCES,
            FUEL_RETRY_SECONDS,
            fuel_interval_override_seconds,
            fuel_refill_interval_seconds,
        )

        defaults = self._default_fuel_settings()
        saved = cfg.load().get("fuel_refill") or {}
        try:
            interval_minutes = int(saved.get("interval_minutes", defaults["interval_minutes"]))
        except (TypeError, ValueError):
            interval_minutes = 0
        interval_minutes = min(FUEL_INTERVAL_MINUTES_MAX, max(0, interval_minutes))
        default_interval_seconds = (
            fuel_interval_override_seconds(interval_minutes)
            if interval_minutes else FUEL_INTERVAL_SECONDS
        )
        fuel = {
            "enabled": bool(saved.get("enabled", defaults["enabled"])),
            "resources": {},
            "paths": {},
            "interval_minutes": interval_minutes,
            "interval_seconds": default_interval_seconds,
            "retry_seconds": FUEL_RETRY_SECONDS,
        }
        saved_resources = saved.get("resources") if isinstance(saved.get("resources"), dict) else {}
        now = time.time()
        for key in FUEL_RESOURCES:
            saved_source = saved_resources.get(key) if isinstance(saved_resources.get(key), dict) else {}
            source = {
                **defaults["resources"][key],
                **saved_source,
            }
            amount = source.get("amount", "max")
            if str(amount).lower() == "max":
                amount = "max"
            else:
                try:
                    amount = min(FUEL_AMOUNT_MAX, max(1, int(amount)))
                except (TypeError, ValueError):
                    amount = "max"
            effective_interval_seconds = (
                fuel_interval_override_seconds(interval_minutes)
                if interval_minutes else fuel_refill_interval_seconds(amount)
            )
            try:
                last_refilled_at = max(0.0, float(source.get("last_refilled_at") or 0))
            except (TypeError, ValueError):
                last_refilled_at = 0.0
            try:
                next_attempt_at = max(0.0, float(source.get("next_attempt_at") or 0))
            except (TypeError, ValueError):
                next_attempt_at = 0.0
            # Legacy development builds used retry_after only for failures.
            # Derive the attempt once using the amount interval or user override.
            if "next_attempt_at" not in saved_source:
                try:
                    retry_after = max(0.0, float(source.get("retry_after") or 0))
                except (TypeError, ValueError):
                    retry_after = 0.0
                next_attempt_at = max(
                    (
                        last_refilled_at + effective_interval_seconds
                        if last_refilled_at else 0.0
                    ),
                    retry_after,
                )
            resource_enabled = bool(source.get("enabled"))
            due = bool(fuel["enabled"] and resource_enabled and now >= next_attempt_at)
            fuel["resources"][key] = {
                "enabled": resource_enabled,
                "amount": amount,
                "last_refilled_at": last_refilled_at,
                "next_attempt_at": next_attempt_at,
                "next_due_at": next_attempt_at,
                "remaining_seconds": max(0, int(next_attempt_at - now + 0.999)),
                "interval_seconds": effective_interval_seconds,
                "due": due,
            }

        saved_paths = saved.get("paths") if isinstance(saved.get("paths"), dict) else {}
        for key in FUEL_PATH_KEYS:
            fuel["paths"][key] = str(
                saved_paths[key] if key in saved_paths else defaults["paths"][key])
        return fuel

    def set_fuel_enabled(self, enabled: bool) -> dict:
        fuel = self.get_fuel_settings()
        fuel["enabled"] = bool(enabled)
        self._save_fuel_settings(fuel)
        return {"ok": True}

    def set_fuel_resource_enabled(self, resource: str, enabled: bool) -> dict:
        from core.runner_constants import FUEL_RESOURCES

        if resource not in FUEL_RESOURCES:
            return {"ok": False, "reason": "bad_resource"}
        fuel = self.get_fuel_settings()
        fuel["resources"][resource]["enabled"] = bool(enabled)
        self._save_fuel_settings(fuel)
        return {"ok": True}

    def set_fuel_interval(self, minutes) -> dict:
        from core.runner_constants import (
            FUEL_INTERVAL_MINUTES_MAX,
            FUEL_INTERVAL_MINUTES_MIN,
            fuel_interval_override_seconds,
            fuel_refill_interval_seconds,
        )

        try:
            interval_minutes = int(minutes)
        except (TypeError, ValueError):
            return {"ok": False, "reason": "bad_interval"}
        if interval_minutes != 0:
            interval_minutes = min(
                FUEL_INTERVAL_MINUTES_MAX,
                max(FUEL_INTERVAL_MINUTES_MIN, interval_minutes),
            )
        fuel = self.get_fuel_settings()
        fuel["interval_minutes"] = interval_minutes
        for resource in fuel["resources"].values():
            if not resource.get("enabled"):
                continue
            last_refilled_at = float(resource.get("last_refilled_at") or 0)
            if not last_refilled_at:
                resource["next_attempt_at"] = 0.0
                continue
            interval_seconds = (
                fuel_interval_override_seconds(interval_minutes)
                if interval_minutes else fuel_refill_interval_seconds(resource.get("amount"))
            )
            resource["next_attempt_at"] = last_refilled_at + interval_seconds
        self._save_fuel_settings(fuel)
        return {"ok": True, "interval_minutes": interval_minutes}

    def set_fuel_resource_amount(self, resource: str, amount) -> dict:
        from core.runner_constants import FUEL_AMOUNT_MAX, FUEL_RESOURCES

        if resource not in FUEL_RESOURCES:
            return {"ok": False, "reason": "bad_resource"}
        if str(amount).lower() == "max":
            amount = "max"
        else:
            try:
                amount = min(FUEL_AMOUNT_MAX, max(1, int(amount)))
            except (TypeError, ValueError):
                return {"ok": False, "reason": "bad_amount"}
        fuel = self.get_fuel_settings()
        fuel["resources"][resource]["amount"] = amount
        self._save_fuel_settings(fuel)
        return {"ok": True}

    def set_fuel_path(self, path_key: str, path_name: str) -> dict:
        from core.runner_constants import FUEL_PATH_KEYS

        if path_key not in FUEL_PATH_KEYS:
            return {"ok": False, "reason": "bad_path_key"}
        fuel = self.get_fuel_settings()
        fuel["paths"][path_key] = str(path_name or "")
        self._save_fuel_settings(fuel)
        return {"ok": True}

    def reset_fuel_timer(self) -> dict:
        fuel = self.get_fuel_settings()
        for resource in fuel["resources"].values():
            if resource.get("enabled"):
                resource["last_refilled_at"] = 0.0
                resource["next_attempt_at"] = 0.0
        self._save_fuel_settings(fuel)
        self.push_log("[Fuel] Enabled resource timers reset. Auto Fuel is ready at the next safe point.")
        return {"ok": True}

    def mark_fuel_refill_result(self, resource: str, succeeded: bool) -> dict:
        from core.runner_constants import (
            FUEL_RESOURCES,
            FUEL_RETRY_SECONDS,
            fuel_interval_override_seconds,
            fuel_refill_interval_seconds,
        )

        if resource not in FUEL_RESOURCES:
            return {"ok": False, "reason": "bad_resource"}
        fuel = self.get_fuel_settings()
        state = fuel["resources"][resource]
        now = time.time()
        if succeeded:
            state["last_refilled_at"] = now
            interval_seconds = (
                fuel_interval_override_seconds(fuel.get("interval_minutes"))
                if fuel.get("interval_minutes") else fuel_refill_interval_seconds(state.get("amount"))
            )
            state["next_attempt_at"] = now + interval_seconds
        else:
            state["next_attempt_at"] = now + FUEL_RETRY_SECONDS
        self._save_fuel_settings(fuel)
        return {"ok": True}

    def test_fuel(self) -> dict:
        # Runs the checked resources immediately, ignoring the master toggle
        # and persistent timer while preserving the real navigation flow.
        return self.runner.start_fuel_test(lambda: self.game_hwnd)

    def start_macro(self) -> dict:
        preflight = self.run_preflight_check()
        if preflight.get("has_blocker", False):
            self.push_log("[Preflight] Start blocked due to environment/configuration issue.")
            self.reset_run_status("Idle")
            return {"ok": False, "reason": "preflight_blocker", "preflight": preflight}

        self.reset_run_status("Starting macro execution...")
        data = cfg.load()
        scroll_power = data.get("story_scroll_power", 3)
        scroll_nudges = data.get("story_scroll_nudges", 8)
        coords = {k: data.get(k, v) for k, v in MACRO_COORD_DEFAULTS.items()}
        debug_screenshots = data.get("debug_screenshots", False)
        default_walk_paths = self.get_default_walk_paths()
        webhook_settings = self.get_webhook_settings()
        return self.runner.start(
            lambda: self.game_hwnd, self.get_tasks, scroll_power, coords, scroll_nudges, debug_screenshots,
            default_walk_paths, webhook_settings,
            expedition_color_buttons=data.get("expedition_color_buttons", True),
            expedition_camera_o_ms=data.get("expedition_camera_o_ms", 100),
            loose_team_ocr_match=data.get("loose_team_ocr_match", False),
            memory_refresh_enabled=data.get("memory_refresh_enabled", False),
            memory_refresh_hours=data.get("memory_refresh_hours", 4.0))

    def stop_macro(self) -> dict:
        # An explicit Stop cancels any pending auto-reopen/auto-restart -- if
        # the user is deliberately stopping (and may then close Roblox to
        # quit), the watchdog must not helpfully reopen the game and start the
        # run back up behind them.
        self._resume_after_relaunch = False
        res = self.runner.stop()
        self.reset_run_status("Idle")
        return res

    def pause_macro(self) -> dict:
        return self.runner.pause()

    def resume_macro(self) -> dict:
        return self.runner.resume()

    def is_macro_running(self) -> dict:
        return {"running": self.runner.is_running(), "paused": self.runner.is_paused()}

    def reload_vision_templates(self) -> dict:
        # Drops the in-memory cache of Assets/ui/*.png so a replaced
        # reference image takes effect on the next macro run without
        # restarting the whole app.
        from core import vision
        vision.clear_template_cache()
        return {"ok": True}

    def save_tasks(self, tasks: list) -> dict:
        # The Task screen edits its queue as one live list (inline edits,
        # reorder, clone) rather than discrete add/remove events, so the
        # whole list is saved as a unit on every change instead of trying
        # to diff individual mutations.
        cfg.update({"tasks": tasks})
        return {"ok": True}

    # ------------------------------------------------------------------
    # Task Queue presets (Task screen > Preset bar) -- save the current
    # queue under a name and switch back to it later from a dropdown.
    #
    # Distinct from Export/Import above, which goes through a native file
    # dialog because it exists to move a queue BETWEEN installs. These stay
    # on this machine, so picking one is two clicks instead of navigating a
    # file picker. See core/task_presets.py.
    # ------------------------------------------------------------------

    def list_task_presets(self) -> list:
        from core import task_presets
        try:
            return task_presets.list_presets()
        except OSError:
            return []

    def save_task_preset(self, name: str, tasks: list) -> dict:
        from core import task_presets
        name = (name or "").strip()
        if not name:
            return {"ok": False, "reason": "no_name"}
        try:
            saved = task_presets.save_preset(name, tasks or [])
        except OSError as exc:
            self.push_log(f"[Task] Couldn't save preset: {exc}")
            return {"ok": False, "reason": str(exc)}
        self.push_log(f'[Task] Saved preset "{saved}" ({len(tasks or [])} task(s)).')
        return {"ok": True, "name": saved, "count": len(tasks or [])}

    def load_task_preset(self, name: str) -> dict:
        """The preset's tasks, plus the names of any Macro Operations those
        tasks reference that no longer exist. A preset saved months ago can
        easily point at a template since renamed or deleted -- the queue
        still loads, but the caller warns instead of leaving the user with
        tasks that silently have no macro attached."""
        from core import task_presets
        from core import templates as tpl
        data = task_presets.load_preset(name)
        tasks = data.get("tasks") or []
        try:
            have = set(tpl.list_templates())
        except OSError:
            have = set()
        missing = sorted({t.get("macro") for t in tasks
                          if isinstance(t, dict) and t.get("macro") and t.get("macro") not in have})
        label = data.get("name") or name
        # Three different empty-queue outcomes, each worth saying differently:
        # the file won't parse, there's no such preset, or it genuinely holds
        # no tasks. Reporting all three as "Loaded (0 task(s))" is how a
        # broken file on disk goes unnoticed.
        if tasks:
            self.push_log(f'[Task] Loaded preset "{label}" ({len(tasks)} task(s)).')
        elif data.get("error"):
            self.push_log(f'[Task] Preset "{label}" is on disk but couldn\'t be read '
                          f'({data["error"]}) -- open the folder and check the file.')
        elif not data.get("found"):
            self.push_log(f'[Task] No saved preset named "{label}" -- nothing loaded.')
        else:
            self.push_log(f'[Task] Preset "{label}" has no tasks saved in it.')
        return {"ok": True, "name": label, "tasks": tasks, "missing_macros": missing,
                "found": bool(data.get("found")), "error": data.get("error") or ""}

    def open_task_presets_folder(self) -> dict:
        """Task screen > Preset > "Folder". Presets are plain .json files, so
        renaming/deleting/backing one up (or dropping one in from another
        machine) is just file management -- this saves hunting for the
        folder, which on macOS lives under ~/Library/Application Support and
        is hidden in Finder by default."""
        from core import task_presets
        try:
            os.makedirs(task_presets.PRESETS_DIR, exist_ok=True)
            self._open_in_file_manager(task_presets.PRESETS_DIR)
        except OSError as exc:
            self.push_log(f"[Task] Couldn't open the presets folder: {exc}")
            return {"ok": False, "reason": str(exc)}
        return {"ok": True}

    @staticmethod
    def _open_in_file_manager(path: str) -> None:
        """Reveal a folder in the OS file manager. os.startfile is
        Windows-only (AttributeError elsewhere), so mac gets `open` and
        Linux `xdg-open` -- same three-way split open_assets_folder uses."""
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif os.name == "nt":
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", path])

    def delete_task_preset(self, name: str) -> dict:
        from core import task_presets
        ok = task_presets.delete_preset(name)
        if ok:
            self.push_log(f'[Task] Deleted preset "{name}".')
        return {"ok": ok}

    def start_path_recording(self) -> dict:
        # Macro Manager > Custom Path > "Record": begins polling the player's own
        # WASD state (see core.paths) -- the player then walks the route
        # in-game themselves and clicks Stop when they've reached the end.
        # GetAsyncKeyState reads real key state regardless of focus, but the
        # recording is only useful if Roblox is actually the window
        # *processing* those WASD presses as movement -- otherwise the
        # player's character never walks and there's nothing meaningful to
        # capture. The Macro Manager screen hides the docked game window entirely
        # (see hide_game()) and clicking Record leaves the macro's own
        # webview panel focused, same focus gap that broke reward-scroll
        # wheel input before -- so this shows Roblox and hands it real OS
        # focus (same activate_window() trick) before polling starts.
        hwnd = self.game_hwnd
        if not hwnd or not wm.is_window(hwnd):
            return {"ok": False, "reason": "no_roblox"}

        wm.show_window(hwnd)
        wm.activate_window(hwnd)

        from core import paths
        try:
            paths.start_recording()
        except paths.RecordingAlreadyActive as exc:
            return {"ok": False, "reason": str(exc)}
        return {"ok": True}

    def stop_path_recording(self, name: str) -> dict:
        from core import paths
        events = paths.stop_recording()
        if not events:
            return {"ok": False, "reason": "no_movement_recorded"}
        saved_name = paths.save_path(name, events)
        self.push_log(f"[Macro Manager] Recorded path \"{saved_name}\" ({len(events)} key events).")
        return {"ok": True, "name": saved_name}

    def cancel_path_recording(self) -> dict:
        from core import paths
        paths.cancel_recording()
        return {"ok": True}

    # Stop and save are split (vs stop_path_recording's stop+save in one)
    # because naming now happens in a dialog AFTER stopping: the WASD poll
    # must already be dead while the player types the name, or the letters
    # w/a/s/d in the name itself (GetAsyncKeyState reads keys regardless of
    # focus) would get appended to the recording as phantom movement.
    def stop_path_capture(self) -> dict:
        from core import paths
        self._pending_path_events = paths.stop_recording()
        return {"ok": True, "count": len(self._pending_path_events)}

    def save_pending_path(self, name: str) -> dict:
        from core import paths
        events = self._pending_path_events or []
        if not events:
            return {"ok": False, "reason": "no_movement_recorded"}
        saved_name = paths.save_path(name, events)
        self._pending_path_events = None
        self.push_log(f"[Macro Manager] Recorded path \"{saved_name}\" ({len(events)} key events).")
        return {"ok": True, "name": saved_name}

    def discard_pending_path(self) -> dict:
        from core import paths
        paths.cancel_recording()
        self._pending_path_events = None
        return {"ok": True}

    def list_paths(self) -> list:
        from core import paths
        return paths.list_paths()

    def list_custom_paths(self) -> list:
        from core import paths
        return paths.list_custom_paths()

    # ------------------------------------------------------------------
    # Record block (Macro Manager > Setup > Record): a general-purpose
    # mouse+keyboard recorder -- everything Click/Send Key don't cover on
    # their own, not just WASD movement (see core.paths above for that).
    # Same start/stop-then-name/save/discard split as path recording, for
    # the same reason: naming happens in a dialog AFTER the hooks are torn
    # down, so typing the name can't leak into the recording.
    # ------------------------------------------------------------------
    def start_input_recording(self) -> dict:
        hwnd = self.game_hwnd
        if not hwnd or not wm.is_window(hwnd):
            return {"ok": False, "reason": "no_roblox"}
        wm.show_window(hwnd)
        wm.activate_window(hwnd)

        from core import input_record
        try:
            input_record.start_recording(hwnd)
        except input_record.RecordingAlreadyActive as exc:
            return {"ok": False, "reason": str(exc)}
        except ImportError:
            return {"ok": False, "reason": "the 'mouse'/'keyboard' packages aren't installed"}
        return {"ok": True}

    def stop_input_capture(self) -> dict:
        from core import input_record
        self._pending_recording_events = input_record.stop_recording()
        return {"ok": True, "count": len(self._pending_recording_events)}

    def save_pending_recording(self, name: str) -> dict:
        from core import input_record
        events = self._pending_recording_events or []
        if not events:
            return {"ok": False, "reason": "no_input_recorded"}
        saved_name = input_record.save_recording(name, events)
        self._pending_recording_events = None
        self.push_log(f'[Macro Manager] Recorded input "{saved_name}" ({len(events)} events).')
        return {"ok": True, "name": saved_name}

    def discard_pending_recording(self) -> dict:
        from core import input_record
        input_record.cancel_recording()
        self._pending_recording_events = None
        return {"ok": True}

    def list_recordings(self) -> list:
        from core import input_record
        return input_record.list_recordings()

    def export_recordings_bundle(self, names) -> dict:
        # Task/Template file export (ui/app.js's exportCustomRecordings) --
        # unlike the Share Code path (export_template_code, which already
        # zlib-compresses the whole payload), the exported .json file isn't
        # compressed at all otherwise, so a Record block with a dense mouse
        # path bundled in raw would dominate the file's size on its own.
        from core import input_record
        if not isinstance(names, list):
            return {}
        return input_record.collect_recordings_compressed(names)

    def import_recordings_bundle(self, bundle: dict) -> dict:
        from core import input_record
        if not isinstance(bundle, dict):
            return {"ok": False, "added": 0}
        return {"ok": True, "added": input_record.import_recordings_compressed(bundle)}

    def set_setting(self, key: str, value) -> dict:
        cfg.update({key: value})  # atomic -- see cfg.update (fixes settings not saving)
        if key == "action_delay_ms":
            # Applied live -- the runner's Mouse/Keyboard read this at
            # every action (see core.pacing), so a mid-run change takes
            # effect on the very next click, no restart/re-Start needed.
            from core import pacing
            pacing.set_action_delay_ms(value)
        return {"ok": True}

    def get_hotkeys(self) -> dict:
        data = cfg.load()
        keys_ = dict(HOTKEY_DEFAULTS)
        keys_.update(data.get("hotkeys", {}))
        return keys_

    def set_hotkey(self, action: str, key: str) -> dict:
        if action not in HOTKEY_DEFAULTS:
            return {"ok": False}
        keys_ = dict(HOTKEY_DEFAULTS)
        keys_.update(cfg.load().get("hotkeys", {}))
        keys_[action] = (key or "").lower()
        cfg.update({"hotkeys": keys_})
        if self._on_hotkeys_changed:
            self._on_hotkeys_changed(keys_)
        return {"ok": True}

    def reset_hotkeys(self) -> dict:
        cfg.update({"hotkeys": dict(HOTKEY_DEFAULTS)})
        if self._on_hotkeys_changed:
            self._on_hotkeys_changed(dict(HOTKEY_DEFAULTS))
        return {"ok": True, "hotkeys": dict(HOTKEY_DEFAULTS)}

    # Task screen > Export/Import: shares a task queue (plus the Macro Manager
    # templates those tasks reference, bundled in by the JS side) as a single
    # JSON file through native save/open dialogs. Also reused by Macro Manager's
    # template Export/Import (see ui/app.js's exportTemplates) -- the file
    # shape is just whatever payload/dict the caller hands in, so nothing
    # here is actually task-specific except the default filename.
    @staticmethod
    def _transfer_directory(folder_kind: str) -> str:
        if folder_kind == "tasks":
            from core import task_presets
            return task_presets.PRESETS_DIR
        return tpl.TEMPLATES_DIR

    def export_tasks_file(self, payload: dict, filename_prefix: str = "tasks") -> dict:
        import json
        import time as _time
        import webview
        if not self._window:
            return {"ok": False, "reason": "no_window"}
        fname = f"AnimeExpeditions-{filename_prefix}-{_time.strftime('%Y%m%d-%H%M%S')}.json"
        transfer_dir = self._transfer_directory(filename_prefix)
        os.makedirs(transfer_dir, exist_ok=True)
        dialog_type = getattr(getattr(webview, "FileDialog", None), "SAVE", getattr(webview, "SAVE_DIALOG", 2))
        try:
            result = self._window.create_file_dialog(
                dialog_type, directory=transfer_dir, save_filename=fname,
                file_types=("JSON files (*.json)",))
        except Exception as exc:
            return {"ok": False, "reason": f"dialog_error: {exc}"}
        if not result:
            return {"ok": False, "reason": "cancelled"}
        path = result[0] if isinstance(result, (list, tuple)) else result
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except OSError as exc:
            return {"ok": False, "reason": str(exc)}
        return {"ok": True, "path": path}

    def import_tasks_file(self, folder_kind: str = "templates") -> dict:
        import json
        import webview
        if not self._window:
            return {"ok": False, "reason": "no_window"}
        transfer_dir = self._transfer_directory(folder_kind)
        os.makedirs(transfer_dir, exist_ok=True)
        dialog_type = getattr(getattr(webview, "FileDialog", None), "OPEN", getattr(webview, "OPEN_DIALOG", 1))
        try:
            result = self._window.create_file_dialog(
                dialog_type, directory=transfer_dir, file_types=("JSON files (*.json)",))
        except Exception as exc:
            # The native dialog can fail (window hidden/minimized, WebView2
            # hiccup, etc.) -- surface it as a normal result so the JS side
            # always has something to show instead of a silently-rejected
            # promise that reads as "nothing happened".
            return {"ok": False, "reason": f"dialog_error: {exc}"}
        if not result:
            return {"ok": False, "reason": "cancelled"}
        path = result[0] if isinstance(result, (list, tuple)) else result
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            return {"ok": False, "reason": str(exc)}
        return {"ok": True, "data": data}

    def load_walk_path(self, name: str) -> dict:
        from core import paths
        return paths.load_path(name)

    def save_walk_path(self, name: str, events: list) -> dict:
        from core import paths
        if not isinstance(events, list):
            return {"ok": False, "reason": "bad_events"}
        return {"ok": True, "name": paths.save_path(name, events)}

    def list_templates(self) -> list:
        return tpl.list_templates()

    def list_example_templates(self) -> list:
        """The bundled example routines, for the Macro Manager's Examples
        picker. Blocks are included so the picker can show what each one
        actually contains rather than just a name."""
        return tpl.list_examples()

    def use_example_template(self, name: str) -> dict:
        """Copy a bundled example into the user's own templates.

        A copy, not a reference: the example stays untouched, and picking
        the same one twice gives a second copy rather than overwriting
        edits made to the first (see tpl.copy_example -- it has to work the
        free name out itself, because save_template's own rule is to
        overwrite a matching name, which is what makes Save behave).
        """
        saved_name = tpl.copy_example(name)
        if not saved_name:
            return {"ok": False, "reason": "no_such_example"}
        self.push_log(f"Added example template '{saved_name}'.")
        return {"ok": True, "name": saved_name}

    def save_template(self, name: str, blocks: list) -> dict:
        saved_name = tpl.save_template(name, blocks)
        self.push_log(f"Saved template '{saved_name}'.")
        return {"ok": True, "name": saved_name}

    def load_template(self, name: str) -> dict:
        return tpl.load_template(name)

    def delete_template(self, name: str) -> dict:
        ok = tpl.delete_template(name)
        if ok:
            self.push_log(f"Deleted template '{name}'.")
        return {"ok": ok}

    def export_template_code(self, names=None) -> dict:
        from core import input_record, paths

        def _bundle_paths(*block_sets):
            # Recorded walks that the macro's custom Walk Path blocks reference,
            # packed alongside so they work on the importer's machine (auto-mode
            # walks use shipped defaults everyone already has -- see
            # share.collect_walk_path_names).
            needed = set()
            for blocks in block_sets:
                needed |= share.collect_walk_path_names(blocks)
            return paths.collect_paths(needed)

        def _bundle_recordings(*block_sets):
            # Same idea for Record block input recordings.
            needed = set()
            for blocks in block_sets:
                needed |= share.collect_recording_names(blocks)
            return input_record.collect_recordings(needed)

        def _add_bundles(payload, *block_sets):
            bundled_paths = _bundle_paths(*block_sets)
            if bundled_paths:
                payload["paths"] = bundled_paths
            bundled_recordings = _bundle_recordings(*block_sets)
            if bundled_recordings:
                payload["recordings"] = bundled_recordings

        if isinstance(names, str) and names.strip():
            if not tpl.template_exists(names):
                return {"ok": False, "reason": f'Macro "{names}" is not saved -- save it before exporting.'}
            single_tpl = tpl.load_template(names)
            blocks = single_tpl.get("blocks", {})
            payload = {
                "kind": "anime-expeditions-template",
                "version": 1,
                "name": names,
                "blocks": blocks,
            }
            _add_bundles(payload, blocks)
            code = share.encode_template_code(payload)
            return {"ok": True, "code": code, "count": 1}
        elif isinstance(names, list) and len(names) > 0:
            if len(names) == 1:
                t_name = names[0]
                if not tpl.template_exists(t_name):
                    return {"ok": False, "reason": f'Macro "{t_name}" is not saved -- save it before exporting.'}
                single_tpl = tpl.load_template(t_name)
                blocks = single_tpl.get("blocks", {})
                payload = {
                    "kind": "anime-expeditions-template",
                    "version": 1,
                    "name": t_name,
                    "blocks": blocks,
                }
                _add_bundles(payload, blocks)
                code = share.encode_template_code(payload)
                return {"ok": True, "code": code, "count": 1}
            else:
                templates = {}
                for t_name in names:
                    if not tpl.template_exists(t_name):
                        return {"ok": False, "reason": f'Macro "{t_name}" is not saved -- save it before exporting.'}
                    loaded = tpl.load_template(t_name)
                    templates[t_name] = loaded.get("blocks", {})
                payload = {
                    "kind": "anime-expeditions-template-pack",
                    "version": 1,
                    "templates": templates,
                }
                _add_bundles(payload, *templates.values())
                code = share.encode_template_code(payload)
                return {"ok": True, "code": code, "count": len(templates)}
        else:
            all_names = tpl.list_templates()
            templates = {}
            for t_name in all_names:
                loaded = tpl.load_template(t_name)
                templates[t_name] = loaded.get("blocks", {})
            payload = {
                "kind": "anime-expeditions-template-pack",
                "version": 1,
                "templates": templates,
            }
            _add_bundles(payload, *templates.values())
            code = share.encode_template_code(payload)
            return {"ok": True, "code": code, "count": len(templates)}

    def import_template_code(self, code_str: str) -> dict:
        from core import input_record, paths

        res = share.decode_template_code(code_str)
        if not res.get("ok"):
            return {"ok": False, "reason": res.get("reason", "Failed to decode input.")}

        templates = res.get("templates", {})
        if not templates:
            return {"ok": False, "reason": "No valid templates found in code/URL."}

        # Recreate any recorded walks bundled with the macro FIRST, then remap
        # the blocks to whatever name each landed under (import_path avoids
        # clobbering a different recording of the same name), so a shared
        # macro's custom Walk Path blocks resolve on this machine too.
        bundled_paths = res.get("paths", {}) or {}
        rename_map = {}
        for pname, pdata in bundled_paths.items():
            events = pdata.get("events", []) if isinstance(pdata, dict) else pdata
            saved_path = paths.import_path(pname, events)
            if saved_path != pname:
                rename_map[pname] = saved_path

        # Same recreate-then-remap dance for Record block input recordings.
        bundled_recordings = res.get("recordings", {}) or {}
        recording_rename_map = {}
        for rname, rdata in bundled_recordings.items():
            events = rdata.get("events", []) if isinstance(rdata, dict) else rdata
            saved_recording = input_record.import_recording(rname, events)
            if saved_recording != rname:
                recording_rename_map[rname] = saved_recording

        imported_names = []
        for tname, blocks in templates.items():
            share.remap_walk_path_names(blocks, rename_map)
            share.remap_recording_names(blocks, recording_rename_map)
            saved = tpl.save_template(tname, blocks)
            imported_names.append(saved)

        walk_note = f" (+{len(bundled_paths)} walk path(s))" if bundled_paths else ""
        rec_note = f" (+{len(bundled_recordings)} recording(s))" if bundled_recordings else ""
        self.push_log(f"Imported {len(imported_names)} template(s) via Share Code: "
                       f"{', '.join(imported_names)}{walk_note}{rec_note}")
        return {"ok": True, "count": len(imported_names), "templates": imported_names,
                "walk_paths": len(bundled_paths), "recordings": len(bundled_recordings)}

    def preview_template_code(self, code_str: str) -> dict:
        return share.preview_template_code(code_str)

    def read_clipboard_text(self) -> dict:
        try:
            import win32clipboard
            win32clipboard.OpenClipboard()
            text = win32clipboard.GetClipboardData()
            win32clipboard.CloseClipboard()
            return {"ok": True, "text": text or ""}
        except Exception:
            try:
                import tkinter as tk
                root = tk.Tk()
                root.withdraw()
                text = root.clipboard_get()
                root.destroy()
                return {"ok": True, "text": text or ""}
            except Exception as e:
                return {"ok": False, "text": "", "reason": str(e)}

    def get_webhook_settings(self) -> dict:
        data = cfg.load()
        return {
            "url": data.get("webhook_url", ""),
            "enabled": data.get("webhook_enabled", False),
            "silent": data.get("webhook_silent", False),
            "mention_id": data.get("webhook_mention_id", ""),
            "progress": data.get("webhook_progress_enabled", False),
        }

    def save_webhook_settings(self, url: str, enabled: bool, silent: bool, mention_id: str = "",
                              progress: bool = False) -> dict:
        cfg.update({
            "webhook_url": url or "",
            "webhook_enabled": bool(enabled),
            "webhook_silent": bool(silent),
            "webhook_mention_id": (mention_id or "").strip(),
            "webhook_progress_enabled": bool(progress),
        })
        return {"ok": True}

    def validate_webhook_url(self, url: str) -> dict:
        return webhook.validate(url or "")

    def test_webhook(self, url: str) -> dict:
        embed = {
            "title": "Test",
            "description": "If you can see this, the webhook is working.",
            "color": 0x5865F2,  # Discord blurple
            "footer": {"text": "Cream's Macro | Anime Expeditions"},
        }
        return webhook.send(url or "", embed)

    def push_log(self, message: str, immediate: bool = False) -> None:
        self.logger.log(message)
        self._log_history.append(message)
        if len(self._log_history) > LOG_HISTORY_LIMIT:
            self._log_history = self._log_history[-LOG_HISTORY_LIMIT:]
        # Queue for the batching worker rather than firing an IPC call per line
        # (see _log_flush_worker). Errors jump the queue so they don't wait a
        # tick -- or get lost if that line is the last thing before a crash.
        self._log_queue.put(message)
        if immediate or _is_critical_log(message):
            self._flush_log_queue()

    def _log_flush_worker(self) -> None:
        """Drains the log queue to the UI ~10x/sec so a burst of lines costs one
        evaluate_js round-trip instead of one per line."""
        while not self.stopping.is_set():
            time.sleep(0.1)
            try:
                self._flush_log_queue()
            except Exception:
                pass

    def _flush_log_queue(self) -> None:
        with self._log_flush_lock:
            batch = []
            while True:
                try:
                    batch.append(self._log_queue.get_nowait())
                except queue.Empty:
                    break
            if not batch:
                return
            wins = [w for w in (self._window, self._log_window) if w]
            if not wins:
                # No window yet -- the lines are already in _log_history, which
                # is what a window opened later replays from. Matches the old
                # behaviour where a line pushed before the window existed never
                # hit the live view.
                return
            payload = json.dumps(batch)
            # appendLogBatch (ui/log_view.js) renders the whole batch in one
            # pass; fall back to per-line addLog if an older page is loaded.
            js = (f"if (window.appendLogBatch) {{ window.appendLogBatch({payload}); }} "
                  f"else if (window.addLog) {{ {payload}.forEach(function(l){{ window.addLog(l); }}); }}")
            for win in wins:
                try:
                    win.evaluate_js(js)
                except Exception:
                    pass

    def clear_logs(self) -> None:
        # Drops the replay buffer too, so a log window popped out *after* this
        # won't come back seeded with lines the user just cleared.
        self._log_history = []
        # Drop anything queued but not yet flushed, or it would repaint the
        # view right after the clear.
        with self._log_flush_lock:
            while not self._log_queue.empty():
                try:
                    self._log_queue.get_nowait()
                except queue.Empty:
                    break
        for win in (self._window, self._log_window):
            if not win:
                continue
            try:
                # clearLogs() on the dashboard is the user action that calls
                # this API. Calling it from Python re-enters clear_logs()
                # recursively and can stall all later log delivery. Invoke
                # the shared view-only helper in both windows instead.
                win.evaluate_js(
                    "window.clearLogView && window.clearLogView()")
            except Exception:
                pass

    def pop_out_logs(self) -> dict:
        import webview  # imported lazily so --test works without pywebview installed

        if self._log_window:
            try:
                self._log_window.restore()  # un-minimize if needed; raises if the window is already gone
                return {"ok": True}
            except Exception:
                self._log_window = None

        win = webview.create_window(
            "Logs | Cream's Macro",
            url=LOGS_WINDOW_HTML,
            width=480,
            height=420,
            background_color="#11131c",  # matches --bg-deep, avoids a white flash before the page loads
        )
        self._log_window = win

        def _seed():
            if not self._log_history:
                return
            payload = json.dumps(self._log_history)
            try:
                win.evaluate_js(
                    f"if (window.appendLogBatch) {{ window.appendLogBatch({payload}); }} "
                    f"else if (window.addLog) {{ {payload}.forEach(function(l){{ window.addLog(l); }}); }}")
            except Exception:
                pass

        def _on_closed():
            self._log_window = None

        win.events.shown += _seed
        win.events.closed += _on_closed
        return {"ok": True}

    def read_current_wave(self) -> dict:
        """Polled by the Wave Monitor pop-out: reads the wave HUD from
        Roblox's OWN window contents (works while tabbed out -- see
        vision.capture_window_region_bgr) and returns the OCR reading plus
        the raw crop as a data URI, so the monitor can show the actual
        pixels even when OCR misreads a busy background."""
        import base64
        from core import vision, wave as wave_module
        from core.runner_constants import WAVE_REGION
        hwnd = self.game_hwnd
        if not hwnd or not wm.is_window(hwnd):
            return {"ok": False, "reason": "no_roblox"}
        try:
            img = vision.capture_window_region_bgr(hwnd, WAVE_REGION)
            if img is None or img.size == 0:
                return {"ok": False, "reason": "capture_failed"}
            current, maximum = wave_module.read_wave(img)
            import cv2
            up = cv2.resize(img, (img.shape[1] * 3, img.shape[0] * 3), interpolation=cv2.INTER_NEAREST)
            ok, png = cv2.imencode(".png", up)
            data_uri = "data:image/png;base64," + base64.b64encode(png.tobytes()).decode("ascii") if ok else None
            return {"ok": True, "current": current, "max": maximum, "crop": data_uri}
        except Exception as exc:
            return {"ok": False, "reason": str(exc)}

    def pop_out_wave_monitor(self) -> dict:
        # Settings > Debug > "Wave Monitor": a small always-on-top window
        # that polls read_current_wave so the wave count is visible while
        # you're tabbed out of Roblox. Same one-window-reused pattern as
        # pop_out_logs; js_api=self so its own JS can call the bridge.
        import webview
        if self._wave_window:
            try:
                self._wave_window.restore()
                return {"ok": True}
            except Exception:
                self._wave_window = None
        win = webview.create_window(
            "Wave Monitor | Cream's Macro",
            url=WAVE_MONITOR_HTML,
            width=300, height=280,
            on_top=True,
            background_color="#11131c",
            js_api=self,
        )
        self._wave_window = win

        def _on_closed():
            self._wave_window = None
        win.events.closed += _on_closed
        return {"ok": True}

    def push_ui(self, js_call: str) -> None:
        if not self._window:
            return
        try:
            self._window.evaluate_js(f"window.{js_call} && window.{js_call}()")
        except Exception:
            pass

    def minimize_window(self):
        if self._window:
            self._window.minimize()

    def show_game(self):
        # Only touches visibility, not docking state: the Roblox window stays
        # parented/borderless the whole time, so this is just a toggle.
        # Cutout mode expresses visibility as LAYERING instead: the game
        # floats topmost exactly over the game slot (see core/dock.py's
        # cutout notes for why a literal region hole can't work over
        # WebView2), and "hidden" is parked at the bottom of the z-order.
        if self.game_cutout:
            self._cutout_game_visible = True
            if self.docker.docked and self.game_hwnd and wm.is_window(self.game_hwnd) and self.gui_hwnd:
                self.docker.dock(self.game_hwnd, self.gui_hwnd, x=0, y=TITLEBAR_H)
            return
        if self.game_hwnd and wm.is_window(self.game_hwnd):
            wm.show_window(self.game_hwnd)

    def hide_game(self):
        # Cutout mode: demote to the bottom of the z-order -- everything
        # (this GUI included) covers the game, which keeps rendering for
        # the window-content captures this mode forces.
        if self.game_cutout:
            self._cutout_game_visible = False
            if self.game_hwnd and wm.is_window(self.game_hwnd):
                wm.send_to_bottom(self.game_hwnd)
            return
        if self.game_hwnd and wm.is_window(self.game_hwnd):
            wm.hide_window(self.game_hwnd)

    def get_platform(self) -> dict:
        """Lets the frontend branch on the one difference it genuinely can't
        infer: on macOS the game is NOT inside our window, so the Dashboard's
        1152x756 game slot is dead space that has to be laid out away rather
        than reserved (see :root[data-platform="mac"] in ui/style.css)."""
        return {"platform": sys.platform, "mac": sys.platform == "darwin"}

    def set_panel_expanded(self, expanded: bool) -> None:
        """macOS only: trade panel width against the game being visible.

        The Dashboard is the only screen that has anything to look at beside
        it, so it keeps the narrow strip with Roblox alongside; every other
        screen is a multi-column editor that was designed against a 1552px
        window and is genuinely unusable in a ~500px strip, so it takes the
        whole visible frame instead. Covering Roblox costs nothing while it's
        covered: mac captures read the window's own backing store even when
        it's behind something (see core/window_mac.capture_window_rgb), and
        the runner activates/raises Roblox before it clicks anyway.

        No-op until the panel has actually been laid out once (_mac_panel_ready
        -- set by the dock arranger or skip_waiting); before that the window is
        still the small waiting-screen box and must stay that way.

        Expansion is safe even WHILE THE MACRO IS RUNNING: core/vision.py was
        already immune (it reads the window's own backing store on mac -- see
        _use_window_capture there), and the OCR crops that used to be plain
        screen grabs (reward/stats reads, the scrollbar color probe) now go
        through core.ocr.capture_region_from_window on mac too, so the panel's
        own pixels are never mistaken for the game. The runner activates/
        raises Roblox before it clicks anyway."""
        if sys.platform != "darwin" or not self._window or not self._mac_panel_ready:
            return
        if not expanded and not self.docker.docked:
            # Nothing arranged beside us to make room for (Roblox not open, or
            # the user pressed Skip) -- narrowing to the strip would just leave
            # the panel squeezed against empty desktop. The dock arranger
            # collapses it for real once a game window actually shows up.
            return
        with self._mac_geometry_lock:
            layout = _mac_panel_layout()
            width = layout["expanded_w"] if expanded else layout["panel_w"]
            # Idempotent: switchScreen fires this on every navigation (and F4
            # can auto-repeat), and a move+resize round trip costs ~300ms.
            # Compared with tolerance because the cache holds the MEASURED
            # width; None means "unknown, re-apply".
            if self._mac_panel_width is not None and abs(width - self._mac_panel_width) <= 2:
                return
            self._apply_panel_geometry(layout["x"], layout["y"], width, layout["panel_h"])

    def _apply_panel_geometry(self, x: int, y: int, width: int, height: int) -> None:
        """Move+resize our own window on macOS, and verify it took.

        Order matters: pywebview's Cocoa resize() anchors NORTH|WEST, so the
        move has to land first or the resize would grow from wherever the
        window happened to be. resize() also defers onto the AppKit main thread
        (AppHelper.callAfter) while this runs on the JS-bridge/watchdog thread,
        hence the settle sleep before reading the result back.

        There is deliberately no AX fallback like the Windows path's
        MoveWindow: the window is created resizable=False, and the
        Accessibility API honours that style mask -- setting kAXSizeAttribute
        on it fails with kAXErrorFailure (-25200), verified on a real Mac.
        setFrame_display_ (what pywebview calls) is not restricted that way,
        so it is the only mechanism that works here.

        Records what the window ACTUALLY ended up at in _mac_panel_width, not
        what was asked for: that field is the short-circuit for repeat calls,
        so caching the requested width after a resize that silently did nothing
        would wedge the panel at the wrong size forever (every later call would
        match the cache and return early). On failure it is cleared to None,
        which just means the next call retries instead of trusting a lie.

        Only WIDTH is verified. window_mac.get_window_rect_screen subtracts a
        hardcoded 28pt title bar that this frameless window does not have, so
        the measured height is always 28 short and could never match."""
        measured = None
        try:
            self._window.move(x, y)
            time.sleep(0.05)
            self._window.resize(width, height)
            time.sleep(0.25)
            gui_hwnd = self.gui_hwnd or WindowManager(GUI_TITLE).find()
            if gui_hwnd:
                left, _, right, _ = wm.get_window_rect_screen(gui_hwnd)
                measured = right - left
                if abs(measured - width) > 2:
                    # One retry: a resize issued while the window is mid-
                    # animation (or minimized) can be dropped entirely.
                    self._window.resize(width, height)
                    time.sleep(0.25)
                    left, _, right, _ = wm.get_window_rect_screen(gui_hwnd)
                    measured = right - left
        except Exception as exc:
            self.push_log(f"[Macro] Couldn't resize the panel: {exc}")
            measured = None
        self._mac_panel_width = measured if measured and measured > 0 else None

    def _resize_gui_keep_pos(self, w: int, h: int) -> None:
        """Resize our own frameless window to (w, h), keeping its current
        top-left corner. Mirrors the sequence skip_waiting/_dock_watchdog use
        on this exact window -- the only resize path proven to take here:
        pywebview's resize() can be silently dropped on a minimized/odd-DPI
        window, so restore() first, then verify and fall back to a native
        MoveWindow if it was lost. Position is read back and preserved so the
        window shrinks toward its bottom-right instead of jumping to (0,0)."""
        gui_hwnd = self.gui_hwnd or WindowManager(GUI_TITLE).find()
        x, y = 0, 0
        if gui_hwnd and wm.is_window(gui_hwnd):
            try:
                l, t, _, _ = wm.get_window_rect_screen(gui_hwnd)
                x, y = l, t
            except Exception:
                pass
        try:
            self._window.restore()
            time.sleep(0.1)
            self._window.resize(w, h)
            time.sleep(0.2)
        except Exception as exc:
            self.push_log(f"[Compact] window resize failed: {exc}")
        if gui_hwnd and wm.is_window(gui_hwnd):
            try:
                l, t, r, b = wm.get_window_rect_screen(gui_hwnd)
                if (r - l, b - t) != (w, h):
                    wm.move_window(gui_hwnd, x, y, w, h)
            except Exception as exc:
                self.push_log(f"[Compact] native resize failed: {exc}")

    def enter_compact(self) -> None:
        """F7 compact view: trim the window down to just the docked game plus
        the bottom control strip (drops the empty side column + log gap). The
        game is neither clipped (the window is only trimmed to the game's own
        size) nor hidden nor undocked, so it stays visible and clickable and
        the macro keeps clicking it exactly as before -- only this window's
        outer size changes."""
        if not self._window or sys.platform == "darwin":
            # mac: the game sits BESIDE the panel, not inside the window, so
            # there's no empty in-window column to trim -- the CSS-hidden
            # panels are the whole effect there.
            return
        self._resize_gui_keep_pos(GUI_WIDTH_COMPACT_FIT, GUI_HEIGHT_COMPACT_FIT)

    def exit_compact(self) -> None:
        """Undo enter_compact: grow the window back to full size so the side
        panel + log have room again."""
        if not self._window or sys.platform == "darwin":
            return
        self._resize_gui_keep_pos(GUI_WIDTH_FULL, GUI_HEIGHT_FULL)

    def skip_waiting(self):
        # Lets the panel be used (config, etc.) before Roblox is even open.
        # The window has to actually resize to full size here, not just in
        # JS/CSS, since the two-column layout is wider than the compact
        # window (see index.html's #main-layout comment -- it's 1552px
        # wide and assumes it never gets shown without that resize having
        # actually happened first).
        if sys.platform == "darwin" and self._window:
            # macOS never uses the 1552px two-column size: there is no game
            # inside the window to leave room for. Skipping straight past
            # docking still needs the panel laid out though, or the UI stays
            # trapped in the compact waiting-screen box. Start expanded --
            # there's no game arranged yet to sit beside.
            self._window.restore()
            time.sleep(0.2)
            layout = _mac_panel_layout()
            with self._mac_geometry_lock:
                self._apply_panel_geometry(
                    layout["x"], layout["y"], layout["expanded_w"], layout["panel_h"])
                self._mac_panel_ready = True
            self.push_log("Skipped waiting for Roblox.")
            return
        if self._window:
            # A resize issued on a minimized window -- or, it turns out,
            # under some DPI-scaling states -- can be silently dropped,
            # leaving the window at the old compact size (verified against
            # pywebview 6.2.1; this is the same known quirk
            # _dock_watchdog already guards against for the docking
            # resize, just never applied here too). Restore first, then
            # verify the resize actually took, falling back to a native
            # MoveWindow if it didn't -- otherwise every screen except the
            # waiting placeholder renders squeezed into ~400px.
            self._window.restore()
            time.sleep(0.2)
            self._window.resize(GUI_WIDTH_FULL, GUI_HEIGHT_FULL)
            self._window.move(0, 0)
            time.sleep(0.3)
            gui_hwnd = WindowManager(GUI_TITLE).find()
            if gui_hwnd:
                left, top, right, bottom = wm.get_window_rect_screen(gui_hwnd)
                if (right - left, bottom - top) != (GUI_WIDTH_FULL, GUI_HEIGHT_FULL):
                    wm.move_window(gui_hwnd, 0, 0, GUI_WIDTH_FULL, GUI_HEIGHT_FULL)
                    time.sleep(0.2)
                    left, top, right, bottom = wm.get_window_rect_screen(gui_hwnd)
                    if (right - left, bottom - top) != (GUI_WIDTH_FULL, GUI_HEIGHT_FULL):
                        self.push_log("Warning: the window didn't fully resize -- some screens may look "
                                      "cramped. Try resizing or maximizing it by hand.")
        self.push_log("Skipped waiting for Roblox.")

    def launch_roblox(self) -> dict:
        """Opens Roblox directly into Anime Expeditions via protocol deeplink."""
        from core.runner_constants import REJOIN_DEEPLINK
        try:
            if hasattr(os, "startfile"):
                os.startfile(REJOIN_DEEPLINK)
            else:
                import webbrowser
                webbrowser.open(REJOIN_DEEPLINK)
            self.push_log("Launching Roblox via deeplink...")
            return {"ok": True}
        except Exception as exc:
            self.push_log(f"Failed to launch Roblox: {exc}")
            return {"ok": False, "error": str(exc)}



    def save_debug_screenshot(self) -> dict:
        # Settings > Debug > "Screenshot": grabs just the Roblox region (its
        # own window rect works whether docked or not -- no need to touch
        # parenting/undock at all, which is what made the old "move to
        # top-left" debug button fight the dock watchdog and thrash the UI)
        # and saves it to the debug folder instead of posting it anywhere.
        hwnd = self.game_hwnd
        if not hwnd or not wm.is_window(hwnd):
            return {"ok": False, "reason": "no_roblox"}

        left, top, right, bottom = wm.get_window_rect_screen(hwnd)
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0:
            return {"ok": False, "reason": "bad_region"}

        # Numbered instead of overwritten -- each press (button or hotkey)
        # keeps its own screenshot instead of clobbering the last one, so a
        # quick "before/after" or "try a few angles" capture session doesn't
        # lose everything but the final shot.
        debug_dir = _debug_dir()
        n = 1
        while os.path.isfile(os.path.join(debug_dir, f"debug_screenshot_{n}.png")):
            n += 1
        path = os.path.join(debug_dir, f"debug_screenshot_{n}.png")
        try:
            import mss
            from mss.tools import to_png
            with mss.MSS() as sct:
                shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
                to_png(shot.rgb, shot.size, output=path)
        except Exception as exc:
            self.push_log(f"Debug screenshot capture failed: {exc}")
            return {"ok": False, "reason": "capture_failed"}

        self.push_log(f"[Debug] Saved screenshot to {path}")
        return {"ok": True, "path": path}

    def debug_test_detect(self, block: dict) -> dict:
        """Test one Detect block against the current full Roblox window.

        This is deliberately read-only: it captures one normalized frame,
        evaluates the block's existing image/region/threshold logic against
        that frame, and returns an annotated preview. It never runs either
        branch or changes the saved macro.
        """
        if not isinstance(block, dict):
            return {"ok": False, "reason": "bad_block"}
        hwnd = self.game_hwnd
        if not hwnd or not wm.is_window(hwnd):
            return {"ok": False, "reason": "no_roblox"}

        temporarily_shown = False
        try:
            import base64
            import cv2
            from core import detect, vision

            # Detect blocks are commonly edited on Settings/Macro Manager,
            # where the native Roblox child is hidden behind the UI. A screen
            # grab in that state sees our own panel, not Roblox. Show it only
            # for this read-only capture and restore the prior visibility
            # before returning the preview to the frontend.
            is_visible = getattr(wm, "is_window_visible", None)
            was_visible = bool(is_visible(hwnd)) if is_visible else True
            if not was_visible:
                self.show_game()
                temporarily_shown = True
                time.sleep(0.05)

            # capture_game_bgr returns the whole normalized Roblox client in
            # the same reference space normal Detect searches use. Keeping it
            # in memory means every score and every drawn box describes the
            # exact frame shown in the preview.
            frame = vision.capture_game_bgr(hwnd)
            if frame is None or frame.size == 0:
                return {"ok": False, "reason": "capture_failed"}
            report = detect.diagnose_frame(frame, block)
            preview = detect.render_diagnostic(frame, report)
            encoded_ok, encoded = cv2.imencode(".png", preview)
            if not encoded_ok:
                return {"ok": False, "reason": "encode_failed"}

            region = report.get("region")
            self.push_log(
                f"[Debug] Detect test: {'FOUND' if report['found'] else 'not found'} -- "
                f"{len(report.get('details', []))} image condition(s) checked."
            )
            return {
                "ok": True,
                "found": bool(report["found"]),
                "region": ({"x": region[0], "y": region[1], "w": region[2], "h": region[3]}
                            if region is not None else None),
                "details": report.get("details", []),
                "data_uri": "data:image/png;base64," + base64.b64encode(encoded.tobytes()).decode("ascii"),
                "width": int(preview.shape[1]),
                "height": int(preview.shape[0]),
            }
        except Exception as exc:
            self.push_log(f"[Debug] Detect test failed: {exc}")
            return {"ok": False, "reason": "test_failed"}
        finally:
            if temporarily_shown:
                self.hide_game()

    def debug_test_expedition_wave(self) -> dict:
        # Settings > Debug > "Test Expedition Wave Check": runs one tick of
        # the Expedition nav_start_game/exp_continue/exp_extract check
        # against Roblox as it is right now, no active macro run needed --
        # navigate to the screen being tested by hand, press this, read the
        # log. See MacroRunner.debug_check_expedition_wave.
        hwnd = self.game_hwnd
        if not hwnd or not wm.is_window(hwnd):
            return {"ok": False, "reason": "no_roblox"}
        # The test tick should exercise the same checkpoint engine a real
        # run would use (see the Expedition Color Detection toggle).
        self.runner._expedition_color_buttons = cfg.load().get("expedition_color_buttons", True)
        result = self.runner.debug_check_expedition_wave(hwnd)
        return {"ok": True, "result": result}

    def debug_force_rejoin(self) -> dict:
        # Settings > Debug > "Force Rejoin": manually triggers the deep-link
        # rejoin on demand -- a quick way to reset Roblox back to the lobby
        # between test iterations without alt-tabbing over and closing/
        # reopening it by hand every time. See MacroRunner.debug_force_rejoin.
        hwnd = self.game_hwnd
        if not hwnd or not wm.is_window(hwnd):
            return {"ok": False, "reason": "no_roblox"}
        ok = self.runner.debug_force_rejoin(hwnd, lambda: self.game_hwnd)
        return {"ok": ok}

    def debug_test_macro_operation(self, mode: str, macro_name: str) -> dict:
        # Settings > Debug > "Test Pre Start"/"Test Battle": runs a chosen
        # Macro Operation's Pre Start or Battle blocks against Roblox as it
        # is right now, no lobby/gamemode/map/stage/teleport setup needed
        # first -- navigate to wherever the blocks should actually run
        # (the unit-placement screen for Pre Start, an actual battle for
        # Battle blocks) by hand, press this, watch it go. Runs as a real
        # tracked run (self.runner.is_running() reports True the same as a
        # normal Start), so the existing Stop/Pause buttons and F2/F5
        # hotkeys work on it unchanged -- see MacroRunner.start_debug_test.
        hwnd = self.game_hwnd
        if not hwnd or not wm.is_window(hwnd):
            return {"ok": False, "reason": "no_roblox"}
        data = cfg.load()
        coords = {k: data.get(k, v) for k, v in MACRO_COORD_DEFAULTS.items()}
        return self.runner.start_debug_test(lambda: self.game_hwnd, mode, macro_name,
                                              data.get("debug_screenshots", False), coords)

    def open_assets_folder(self) -> dict:
        # Settings > General > "Open Assets Folder" (also the Image
        # Manager's "Open Folder" button) -- THE assets location now, not an
        # override tier: Assets/ ships loose beside the exe (see core.
        # constants.ASSETS_DIR) precisely so its images can be opened/
        # replaced/added to directly. One folder per searched name; every
        # image inside gets tried (core.vision.template_variant_paths).
        # Creates ui/ and maps/ (empty) if somehow absent, so there's
        # somewhere obvious to drop files into instead of a folder that
        # doesn't exist yet.
        try:
            for sub in ("ui", "maps"):
                os.makedirs(os.path.join(constants.ASSETS_OVERRIDE_DIR, sub), exist_ok=True)
            # Platform split lives in _open_in_file_manager -- mac needs
            # `open` (os.startfile is Windows-only), and there the folder is
            # under ~/Library/Application Support, which Finder hides by
            # default, so opening it directly is the only way a user gets to it.
            self._open_in_file_manager(constants.ASSETS_OVERRIDE_DIR)
        except OSError as exc:
            self.push_log(f"[Settings] Couldn't open the Assets folder: {exc}")
            return {"ok": False, "reason": str(exc)}
        return {"ok": True}

    # ------------------------------------------------------------------
    # Image Manager (Settings > General > Image Search) -- browse every
    # reference image the macro searches for, grouped one-folder-per-name
    # (see core.vision.template_variant_paths), and add new crops straight
    # from a live Roblox screenshot without ever leaving the app: Capture ->
    # drag a box over the button/text -> pick/type a name -> saved into that
    # name's folder as an extra variant the very next search will try.
    # ------------------------------------------------------------------

    def _image_manager_root(self, category: str) -> str:
        """Absolute folder for a category key, or None for anything not in
        the IMAGE_MANAGER_CATEGORIES whitelist -- category strings come from
        the JS side and end up in filesystem paths, so unknown values are
        rejected outright instead of being joined into a path."""
        entry = IMAGE_MANAGER_CATEGORIES.get(category)
        if not entry:
            return None
        return os.path.join(constants.ASSETS_DIR, entry[0])

    @staticmethod
    def _safe_image_name(name: str) -> str:
        """Search names double as folder/file names, so strip anything that
        isn't alnum/space/dash/underscore/apostrophe (apostrophe allowed --
        real map names like "King's Tomb" need it; core.templates' stricter
        _safe_name has no such names to deal with) and any leading/trailing
        dots so a name can't traverse out of its category folder."""
        cleaned = re.sub(r"[^A-Za-z0-9 _\-']", "", name or "").strip().strip(".")
        return cleaned

    @staticmethod
    def _image_file_entry(path: str) -> dict:
        """One image file as the JS side renders it: filename + a data URI
        thumbnail. Reference crops are tiny (a few KB each), so base64ing
        every one of them into the listing is cheap and saves the UI from
        needing any http server/file:// access to render them."""
        import base64
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return {"file": os.path.basename(path), "data_uri": "data:image/png;base64," + b64}

    def list_vision_templates(self) -> dict:
        # The Image Manager's library view: every search name in every
        # category with a thumbnail of each of its variant images. Reads the
        # folder fresh on every call (no cache) -- the whole point of this
        # screen is showing what's REALLY on disk right now, including files
        # the user just dropped in by hand.
        categories = []
        for key, (sub, label) in IMAGE_MANAGER_CATEGORIES.items():
            root = os.path.join(constants.ASSETS_DIR, sub)
            names = []
            if os.path.isdir(root):
                for entry in sorted(os.listdir(root), key=str.lower):
                    full = os.path.join(root, entry)
                    try:
                        if os.path.isdir(full):
                            # Folder-per-name layout: primary crop
                            # (<name>.png) first, extras alphabetically --
                            # same try-order core.vision uses, so the UI
                            # shows them in the order they get matched.
                            primary = f"{entry}.png".lower()
                            files = sorted(
                                (f for f in os.listdir(full) if f.lower().endswith(".png")),
                                key=lambda f: (f.lower() != primary, f.lower()),
                            )
                            if files:
                                names.append({
                                    "name": entry,
                                    "images": [self._image_file_entry(os.path.join(full, f)) for f in files],
                                })
                        elif entry.lower().endswith(".png"):
                            # Loose legacy/hand-dropped file -- still a valid
                            # single-variant name (see template_variant_paths
                            # rule 1), shown the same as a one-image folder.
                            names.append({
                                "name": entry[:-4],
                                "images": [self._image_file_entry(full)],
                                "loose": True,
                            })
                    except OSError:
                        continue  # unreadable entry -- skip it rather than kill the whole listing
            categories.append({"key": key, "label": label, "names": names})
        # Attach each name's current match threshold (its override, or the
        # default) so the Image Manager can show/edit the sensitivity slider.
        from core import vision
        thresholds = cfg.load().get("image_thresholds", {})
        default_t = vision.DEFAULT_THRESHOLD
        for cat in categories:
            for entry in cat["names"]:
                entry["threshold"] = float(thresholds.get(entry["name"], default_t))
        return {"ok": True, "categories": categories, "default_threshold": default_t}

    def set_image_threshold(self, name: str, value) -> dict:
        """Save a per-name match-threshold override (Image Manager slider)
        and apply it live. value == the default (or None) clears the
        override rather than storing a redundant entry."""
        from core import vision
        try:
            v = float(value)
        except (TypeError, ValueError):
            return {"ok": False}
        data = cfg.load()
        thresholds = dict(data.get("image_thresholds", {}))
        if abs(v - vision.DEFAULT_THRESHOLD) < 1e-6:
            thresholds.pop(name, None)  # back to default -- don't persist it
        else:
            thresholds[name] = max(0.5, min(1.0, v))
        cfg.update({"image_thresholds": thresholds})
        vision.set_name_thresholds(thresholds)  # live, no restart
        return {"ok": True, "threshold": thresholds.get(name, vision.DEFAULT_THRESHOLD)}

    def capture_image_search_screen(self) -> dict:
        # The Capture button: one frozen screenshot of the docked Roblox
        # window, shown on the crop canvas. Reuses get_roblox_snapshot's
        # proven raise-grab-restore dance verbatim, and ALSO caches the PNG
        # bytes server-side -- save_image_search_crop cuts the crop from
        # this exact cached frame rather than round-tripping the (large)
        # image back through the JS bridge.
        result = self.get_roblox_snapshot()
        if result.get("ok"):
            import base64
            self._image_search_png = base64.b64decode(result["data_uri"].split(",", 1)[1])
        return result

    def save_image_search_crop(self, category: str, name: str, x, y, w, h) -> dict:
        # Crop the cached capture (see capture_image_search_screen) down to
        # the dragged box and save it as a variant image of `name`:
        # Assets/<category>/<name>/<name>.png if the name is brand new,
        # otherwise <name>_altN.png beside the existing image(s). "_alt" on
        # purpose, NOT the bare "_2"/"_3" style: numbered names like
        # nav_start_game_2 are their own distinct search names in the runner
        # (a different button, not a variant), so a saved variant must never
        # be confusable with -- or collide with -- one of those.
        import cv2
        import numpy as np
        from core import vision

        root = self._image_manager_root(category)
        if not root:
            return {"ok": False, "reason": "bad_category"}
        name = self._safe_image_name(name)
        if not name:
            return {"ok": False, "reason": "bad_name"}
        png = getattr(self, "_image_search_png", None)
        if not png:
            return {"ok": False, "reason": "no_capture"}

        image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return {"ok": False, "reason": "decode_failed"}
        ih, iw = image.shape[:2]
        # Clamp the box to the frame -- a drag can start/end slightly outside
        # the canvas image area and JS sends it through as-is.
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1, y1 = min(iw, int(x) + int(w)), min(ih, int(y) + int(h))
        if x1 - x0 < 4 or y1 - y0 < 4:
            # Anything smaller than 4px a side is a misdrag, not a usable
            # reference crop -- matching needs actual shape/edge content.
            return {"ok": False, "reason": "too_small"}
        crop = image[y0:y1, x0:x1]

        folder = os.path.join(root, name)
        os.makedirs(folder, exist_ok=True)
        filename = f"{name}.png"
        n = 2
        while os.path.exists(os.path.join(folder, filename)):
            filename = f"{name}_alt{n}.png"
            n += 1
        path = os.path.join(folder, filename)
        # imencode + plain write instead of cv2.imwrite -- imwrite silently
        # fails on paths cv2 can't encode (and returns False rather than
        # raising), while an ordinary open() write of the encoded bytes
        # works for any path the OS accepts and raises loudly if not.
        ok, encoded = cv2.imencode(".png", crop)
        if not ok:
            return {"ok": False, "reason": "encode_failed"}
        with open(path, "wb") as f:
            f.write(encoded.tobytes())

        # Drop vision's in-memory cache so the very next search actually
        # tries the new image -- without this it wouldn't exist to the
        # matcher until an app restart (see vision.clear_template_cache).
        vision.clear_template_cache()
        self.push_log(f'[Images] Saved {os.path.join("Assets", IMAGE_MANAGER_CATEGORIES[category][0], name, filename)} '
                      f'({x1 - x0}x{y1 - y0}px) -- image search will try it immediately.')
        return {"ok": True, "name": name, "entry": self._image_file_entry(path)}

    def delete_vision_template_image(self, category: str, name: str, filename: str) -> dict:
        # The library view's per-image delete. filename is basename-checked
        # (no separators/dots-only tricks) since it comes from JS; the empty
        # folder is removed too so a fully-cleared name disappears from the
        # library instead of lingering as a zero-image box.
        from core import vision
        root = self._image_manager_root(category)
        if not root:
            return {"ok": False, "reason": "bad_category"}
        name = self._safe_image_name(name)
        if (not name or not filename or os.path.basename(filename) != filename
                or not filename.lower().endswith(".png")):
            return {"ok": False, "reason": "bad_name"}
        path = os.path.join(root, name, filename)
        if not os.path.isfile(path) and filename == f"{name}.png":
            # A loose top-level file (legacy layout / hand-dropped) has no
            # <name>/ folder -- fall back to deleting it directly.
            path = os.path.join(root, filename)
        try:
            os.remove(path)
        except OSError as exc:
            return {"ok": False, "reason": str(exc)}
        try:
            folder = os.path.join(root, name)
            if os.path.isdir(folder) and not os.listdir(folder):
                os.rmdir(folder)
        except OSError:
            pass  # non-empty or locked -- fine, it just stays
        vision.clear_template_cache()
        self.push_log(f"[Images] Deleted {filename} from {name}.")
        return {"ok": True}

    def install_tesseract(self) -> dict:
        # Settings > General > "Install Tesseract OCR": one-click install via
        # winget (see core.tesseract_installer) for anyone who's hit
        # TesseractNotAvailable (match-stats OCR) instead of having to find/
        # run the UB-Mannheim installer by hand. Runs on a background thread
        # since the winget download/install can take a while; the button's
        # own JS polls for completion the same way Camera Setup's does.
        def run():
            from core import tesseract_installer, ocr
            ok = tesseract_installer.install_tesseract(log=self.push_log)
            if ok:
                ocr.reset_tesseract_cache()
            self.push_ui("tesseractInstallDone" if ok else "tesseractInstallFailed")

        threading.Thread(target=run, daemon=True).start()
        return {"ok": True}

    def get_ocr_status(self) -> dict:
        # Returns whether Windows OCR is available and whether Tesseract is installed.
        from core import ocr_windows, ocr
        win_available = ocr_windows.is_available()
        tess_ok = False
        try:
            ocr.get_pytesseract()
            tess_ok = True
        except Exception:
            tess_ok = False
        return {
            "ok": True,
            "windows_ocr": win_available,
            "tesseract_installed": tess_ok,
        }


    def list_roblox_windows(self) -> list:
        # Settings > Debug > "Select Roblox Window": every standalone Roblox
        # window NOT already docked (see core.window.list_roblox_windows),
        # for picking a specific one when multiple are open.
        try:
            return wm.list_roblox_windows()
        except Exception:
            return []

    def attach_roblox_window(self, hwnd) -> dict:
        # Settings > Debug > "Attach Selected Roblox": pins the dock
        # watchdog's next attempt to this specific window instead of
        # whichever one find_roblox_window() would grab on its own (see
        # _dock_watchdog's pinned_hwnd handling). If something else is
        # currently docked, let it go first so the watchdog's normal dock
        # step is free to reparent the newly chosen window in cleanly.
        try:
            hwnd = int(hwnd)
        except (TypeError, ValueError):
            return {"ok": False, "reason": "bad_hwnd"}
        if not wm.is_window(hwnd):
            return {"ok": False, "reason": "not_found"}

        if self.docker.docked and self.game_hwnd and self.game_hwnd != hwnd:
            self.docker.undock(self.game_hwnd)
        self.game_hwnd = None
        self.docker.docked = False
        self.dock_suspended = False
        self.pinned_hwnd = hwnd
        self.push_log(f"[Debug] Attaching Roblox window (pid {wm.get_window_pid(hwnd)})...")
        return {"ok": True}

    def detach_roblox_window(self) -> dict:
        # Settings > Debug > "Un-Attach Roblox": detaches whatever's
        # currently docked and suspends the watchdog's auto re-dock (see
        # _dock_watchdog's dock_suspended check) until Attach is used again
        # -- without that, the watchdog would just find the same still-open
        # window on its next tick and redock it right back.
        hwnd = self.game_hwnd
        self.dock_suspended = True
        self.pinned_hwnd = None
        if hwnd and wm.is_window(hwnd):
            self.docker.undock(hwnd)
        self.game_hwnd = None
        self.push_ui("showWaiting")
        self.push_log("[Debug] Roblox un-attached -- won't auto re-dock until you Attach again.")
        return {"ok": True}

    def debug_camera_setup(self) -> dict:
        # Settings > Debug > "Camera Setup": puts the Roblox camera into the
        # standard macro viewpoint. Actual sequence lives in core.camera
        # (shared with the macro run's automatic Pre Start step) -- this is
        # just the on-demand trigger, run on a background thread since the
        # whole sequence takes ~3s and none of it needs anything else
        # coordinated.
        hwnd = self.game_hwnd
        if not hwnd or not wm.is_window(hwnd):
            return {"ok": False, "reason": "no_roblox"}

        # Same focus dance as reward-scroll/path-recording: the click that
        # triggered this left the macro's own panel focused, and Roblox only
        # processes mouse/keyboard input while it's the foreground window.
        wm.show_window(hwnd)
        wm.activate_window(hwnd)

        def run():
            from core import camera
            try:
                camera.run_camera_setup(self.mouse, self.keyboard, hwnd)
                self.push_log("[Debug] Camera setup done -- tilted down, zoomed out.")
            except Exception as exc:
                self.push_log(f"[Debug] Camera setup failed: {exc}")

        threading.Thread(target=run, daemon=True).start()
        return {"ok": True}

    def debug_camera_setup_2(self, hold_ms) -> dict:
        # Settings > Debug > "Camera Setup 2": same drag-down-then-zoom
        # sequence as Camera Setup, but with a caller-supplied O-hold
        # duration instead of the fixed 2s -- for testing how long the
        # zoom-out actually needs.
        hwnd = self.game_hwnd
        if not hwnd or not wm.is_window(hwnd):
            return {"ok": False, "reason": "no_roblox"}
        try:
            hold_ms = max(0.0, float(hold_ms))
        except (TypeError, ValueError):
            return {"ok": False, "reason": "bad_hold_ms"}

        wm.show_window(hwnd)
        wm.activate_window(hwnd)

        def run():
            from core import camera
            try:
                camera.run_camera_setup(self.mouse, self.keyboard, hwnd, hold_ms=hold_ms)
                self.push_log(f"[Debug] Camera setup 2 done ({hold_ms:.0f}ms hold).")
            except Exception as exc:
                self.push_log(f"[Debug] Camera setup 2 failed: {exc}")

        threading.Thread(target=run, daemon=True).start()
        return {"ok": True}

    def debug_camera_setup_3(self, hold_ms) -> dict:
        # Settings > Debug > "Camera Setup 3": the standard right-click
        # drag-down pitch pin, then HOLD the Left arrow key for a
        # caller-supplied time instead of the O zoom-hold -- the same
        # sequence Expedition's Pre Start runs with a 750ms hold (see
        # core.camera.run_camera_drag_hold), runnable here with any hold
        # time for tuning.
        hwnd = self.game_hwnd
        if not hwnd or not wm.is_window(hwnd):
            return {"ok": False, "reason": "no_roblox"}
        try:
            hold_ms = max(0.0, float(hold_ms))
        except (TypeError, ValueError):
            return {"ok": False, "reason": "bad_hold_ms"}

        wm.show_window(hwnd)
        wm.activate_window(hwnd)

        def run():
            from core import camera
            try:
                camera.run_camera_drag_hold(self.mouse, self.keyboard, hwnd, hold_ms=hold_ms)
                self.push_log(f"[Debug] Camera setup 3 done (drag down, {hold_ms:.0f}ms Left-arrow hold).")
            except Exception as exc:
                self.push_log(f"[Debug] Camera setup 3 failed: {exc}")

        threading.Thread(target=run, daemon=True).start()
        return {"ok": True}

    # Reference names the macro genuinely cannot run without -- the health
    # check flags these missing rather than every optional nicety.
    HEALTH_CRITICAL_IMAGES = ("nav_play", "nav_start_game", "victory", "defeat", "leave_stage", "exp_continue")

    def run_preflight_check(self) -> dict:
        """Run deterministic prerequisite checks before macro start."""
        checks_list = []
        warnings_list = []
        has_blocker = False

        def add_check(is_ok: bool, code: str, msg: str, action: str, is_blocker: bool = True):
            nonlocal has_blocker
            check_data = {
                "ok": is_ok,
                "code": code,
                "message": msg,
                "action": action,
                "blocker": is_blocker
            }
            checks_list.append(check_data)
            if not is_ok:
                if is_blocker:
                    has_blocker = True
                else:
                    warnings_list.append(check_data)

        # 1. Roblox window attached
        hwnd = self.game_hwnd
        roblox_attached = bool(hwnd and wm.is_window(hwnd))
        add_check(
            roblox_attached,
            "ROBLOX_NOT_FOUND",
            "Roblox window is not open or not docked.",
            "Launch Roblox and allow the macro to attach.",
            True
        )

        # 2. Elevation match
        if roblox_attached:
            elevation_mismatch = wm.is_process_elevated(hwnd) and not wm.is_self_elevated()
            add_check(
                not elevation_mismatch,
                "ELEVATION_MISMATCH",
                "Roblox is running as Administrator but the macro is not.",
                "Relaunch the macro as Administrator.",
                True
            )

        # 3. Display scale
        scale = wm.get_display_scale_percent()
        add_check(
            scale == 100,
            "DISPLAY_SCALE_WARNING",
            f"Windows display scale is set to {scale}%.",
            "Set Windows Display Scale to 100% in Settings > Display.",
            False
        )

        # 4. Assets folder
        assets_exist = os.path.isdir(constants.ASSETS_DIR)
        assets_populated = False
        if assets_exist:
            # Check if empty (or at least contains some items)
            assets_populated = len(os.listdir(constants.ASSETS_DIR)) > 0

        add_check(
            assets_exist and assets_populated,
            "ASSETS_MISSING",
            "Assets folder is missing.",
            "Download the complete release ZIP from GitHub and extract all files.",
            True
        )

        # 5. Critical reference images
        missing_images = []
        if assets_exist:
            from core import vision
            for name in self.HEALTH_CRITICAL_IMAGES:
                try:
                    if not vision.template_variant_paths(name):
                        missing_images.append(name)
                except Exception:
                    missing_images.append(name)

        if missing_images:
            add_check(
                False,
                "CRITICAL_IMAGES_MISSING",
                f"Missing critical reference images: {', '.join(missing_images)}",
                "Re-extract release ZIP keeping folder structure.",
                True
            )
        else:
            # Add a pass for critical images if we got this far without adding one
            if assets_exist and assets_populated:
                add_check(
                    True,
                    "CRITICAL_IMAGES_MISSING",
                    "All critical reference images found.",
                    "",
                    True
                )

        return {
            "ok": not has_blocker,
            "has_blocker": has_blocker,
            "warnings": warnings_list,
            "checks": checks_list
        }

    def run_health_check(self) -> dict:
        """Settings > Debug > "Health Check" (also offered by the first-run
        welcome): verifies the handful of environmental things that cause
        the vast majority of "it just doesn't work" reports, end to end --
        window found, captures return real pixels, simulated input actually
        moves the cursor, elevation/display-scale mismatches, critical
        reference images present, Tesseract available. Each result is
        logged AND returned so the UI can render a summary. Checks that
        need Roblox report "skipped" (ok=True) when it isn't attached --
        a half-run check that names what it couldn't test beats refusing
        to run at all."""
        checks = []

        def add(name, ok, detail=""):
            checks.append({"name": name, "ok": bool(ok), "detail": detail})
            mark = "OK  " if ok else "FAIL"
            self.push_log(f"[Health] {mark} {name}" + (f" -- {detail}" if detail else ""))

        hwnd = self.game_hwnd
        roblox_ok = bool(hwnd and wm.is_window(hwnd))
        add("Roblox window attached", roblox_ok,
            "" if roblox_ok else "Open Roblox and let the macro dock it (the Waiting screen does this).")

        if roblox_ok:
            try:
                from core import vision
                gray = vision.capture_game_gray(hwnd)
                cap_ok = gray is not None and bool(gray.any())
            except Exception as exc:
                cap_ok, gray = False, None
                self.push_log(f"[Health] capture raised: {exc}")
            add("Screen capture returns pixels", cap_ok,
                "" if cap_ok else "Captures come back black. Make sure the game is visible on the "
                                  "Dashboard; BitBlt-dead GPU setups switch to window capture after "
                                  "one black frame -- run this again.")
        else:
            add("Screen capture returns pixels", True, "skipped -- no Roblox window to capture")

        # Does simulated input reach the OS at all? A relative nudge that
        # doesn't move the real cursor means SendInput/CGEvent is being
        # dropped wholesale (permissions on mac, injection blocked on
        # Windows) -- the exact "finds the button, clicks, nothing happens"
        # class of report.
        try:
            x0, y0 = self.mouse.position()
            self.mouse.nudge(5, 5)
            time.sleep(0.05)
            x1, y1 = self.mouse.position()
            self.mouse.nudge(-5, -5)  # put the cursor back where it was
            input_ok = (x1, y1) != (x0, y0)
        except Exception as exc:
            input_ok = False
            self.push_log(f"[Health] input probe raised: {exc}")
        add("Simulated input moves the cursor", input_ok,
            "" if input_ok else ("Grant Accessibility permission (System Settings > Privacy & Security) "
                                 "and restart the app." if sys.platform == "darwin" else
                                 "Something is blocking SendInput -- security software, or an "
                                 "elevation mismatch (see the next check)."))

        if roblox_ok and sys.platform != "darwin":
            mismatch = wm.is_process_elevated(hwnd) and not wm.is_self_elevated()
            add("Elevation matches Roblox", not mismatch,
                "" if not mismatch else "Roblox runs as Administrator but this macro doesn't -- Windows "
                                        "silently drops clicks/keys upward. Relaunch the macro as "
                                        "Administrator too.")
        else:
            add("Elevation matches Roblox", True, "skipped" if not roblox_ok else "not applicable on macOS")

        scale = wm.get_display_scale_percent()
        add("Display scale", scale == 100,
            "" if scale == 100 else f"Windows display scale is {scale}% -- fixed coordinates assume 100%. "
                                    f"Settings > Display > Scale, set 100% (or expect placement drift).")

        from core import vision
        # The whole Assets folder being absent is its own diagnosis, not
        # just "N images missing": it's the signature of downloading the
        # bare exe from the release page instead of the zip (the exe is a
        # compatibility asset for old auto-updaters, not a download for
        # people). Flagged with an action the UI turns into an "open the
        # release page" button so the fix is one click away.
        assets_missing = not os.path.isdir(constants.ASSETS_DIR) or not os.listdir(constants.ASSETS_DIR)
        if assets_missing:
            checks.append({"name": "Assets folder present", "ok": False,
                            "detail": "No Assets folder next to the app -- you likely downloaded just the "
                                      ".exe from GitHub. Download the full -Windows.zip from the latest "
                                      "release instead and extract it whole (app + Assets together).",
                            "action": "open_releases"})
            self.push_log("[Health] FAIL Assets folder present -- download the release ZIP (not the bare "
                           "exe) from GitHub and extract it whole.")
        else:
            missing = []
            for name in self.HEALTH_CRITICAL_IMAGES:
                try:
                    if not vision.template_variant_paths(name):
                        missing.append(name)
                except Exception:
                    missing.append(name)
            add("Critical reference images present", not missing,
                "" if not missing else f"Missing: {', '.join(missing)} -- re-extract the release zip "
                                       f"keeping its folder structure, or re-add crops via the Image Manager.")

        # OCR (wave/stats reading): Windows' built-in engine is preferred and
        # needs nothing installed; Tesseract is only the fallback. This is a
        # real recognition smoke test, not just an import/package check.
        from core import ocr
        ocr_ok, ocr_detail = ocr.smoke_test_text_reader()
        add("Text reading (OCR)", ocr_ok,
            ocr_detail if ocr_ok else
            f"{ocr_detail} -- only Auto Bounty, stats/reward reading, and Wait-for-Wave need it. "
            "Install the Windows OCR dependencies from requirements.txt, or install Tesseract via "
            "Settings > General.")

        overall = all(c["ok"] for c in checks)
        self.push_log(f"[Health] {'All checks passed.' if overall else 'Some checks need attention -- see above.'}")
        return {"ok": overall, "checks": checks}

    def open_releases_page(self) -> dict:
        # Health check's "Assets folder missing" fix-it button: the GitHub
        # latest-release page, where the full zip lives.
        import webbrowser
        webbrowser.open(updater.RELEASES_PAGE_URL)
        return {"ok": True}

    YOUTUBE_CHANNEL_URL = "https://www.youtube.com/@Cweamya/videos"
    # Where people share routines with each other. The Examples picker points
    # at it, since only a handful can reasonably ship with the app.
    COMMUNITY_URL = "https://discord.gg/creams"

    def open_youtube_channel(self) -> dict:
        # The one-time subscribe prompt's button -- opens the creator's
        # channel in the default browser.
        import webbrowser
        webbrowser.open(self.YOUTUBE_CHANNEL_URL)
        return {"ok": True}

    def open_community(self) -> dict:
        # Examples picker -> "More examples". Opened in the default browser
        # rather than navigated to: the UI runs inside the app's own webview,
        # so an <a href> would replace the macro with a web page.
        import webbrowser
        webbrowser.open(self.COMMUNITY_URL)
        return {"ok": True}

    def export_failure_report(self) -> dict:
        """Settings > Debug > "Export Failure Report": bundles everything a
        bug report needs into one shareable zip -- the newest debug
        screenshots, the log tail, settings (webhook URL redacted -- it's a
        credential), version/platform, and a fresh health-check run -- so
        "it's broken" reports can arrive as one file instead of a
        photo-of-a-screen and twenty questions."""
        import json
        import zipfile as zf_mod
        import platform
        import webview
        if not self._window:
            return {"ok": False, "reason": "no_window"}
        fname = f"AnimeExpeditions-report-{time.strftime('%Y%m%d-%H%M%S')}.zip"
        dialog_type = getattr(getattr(webview, "FileDialog", None), "SAVE", getattr(webview, "SAVE_DIALOG", 2))
        try:
            result = self._window.create_file_dialog(
                dialog_type, directory=os.path.expanduser("~"), save_filename=fname,
                file_types=("Zip files (*.zip)",))
        except Exception as exc:
            return {"ok": False, "reason": f"dialog_error: {exc}"}
        if not result:
            return {"ok": False, "reason": "cancelled"}
        path = result[0] if isinstance(result, (list, tuple)) else result

        health = self.run_health_check()  # logs as it goes; captured into the bundle below
        try:
            with zf_mod.ZipFile(path, "w", zf_mod.ZIP_DEFLATED) as bundle:
                # Newest debug captures -- the frames are what make reports
                # diagnosable (bands/positions get measured off them).
                debug_dir = os.path.join(constants.APP_DIR, "debug")
                if os.path.isdir(debug_dir):
                    shots = sorted((os.path.join(debug_dir, n) for n in os.listdir(debug_dir)
                                     if n.lower().endswith(".png")),
                                    key=os.path.getmtime, reverse=True)[:12]
                    for shot in shots:
                        bundle.write(shot, f"debug/{os.path.basename(shot)}")

                log_path = os.path.join(constants.APP_DIR, "debug.log")
                if os.path.isfile(log_path):
                    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                        tail = f.readlines()[-600:]
                    from core.diagnostics import redact_sensitive_info
                    sanitized_tail = redact_sensitive_info("".join(tail))
                    bundle.writestr("log_tail.txt", sanitized_tail)

                data = cfg.load()
                if data.get("webhook_url"):
                    data["webhook_url"] = "<redacted>"

                from core.diagnostics import redact_sensitive_info
                settings_json = json.dumps(data, indent=2)
                sanitized_settings = redact_sensitive_info(settings_json)
                bundle.writestr("settings.json", sanitized_settings)

                bundle.writestr("info.json", json.dumps({
                    "version": updater.get_current_version(),
                    "platform": platform.platform(),
                    "python": sys.version,
                    "frozen": constants.IS_FROZEN,
                    "health_check": health,
                }, indent=2))
        except OSError as exc:
            return {"ok": False, "reason": str(exc)}
        self.push_log(f"[Debug] Failure report saved: {path}")
        return {"ok": True, "path": path}

    def debug_test_path(self, name: str) -> dict:
        # Settings > Debug > "Test Walking Path": replays a path recorded via
        # Macro Manager > Custom Path > Record (see core.paths.replay_events)
        # against the live game, so a recorded route can be sanity-checked on
        # its own instead of only finding out it's wrong mid-run.
        from core import paths
        if paths.is_recording():
            return {"ok": False, "reason": "recording_in_progress"}

        hwnd = self.game_hwnd
        if not hwnd or not wm.is_window(hwnd):
            return {"ok": False, "reason": "no_roblox"}

        data = paths.load_path(name)
        events = data.get("events", [])
        if not events:
            return {"ok": False, "reason": "empty_path"}

        # Same focus dance as reward-scroll/camera-setup/path-recording:
        # Roblox only processes WASD while it's actually the focused window.
        wm.show_window(hwnd)
        wm.activate_window(hwnd)

        self._path_test_stop = threading.Event()
        stop_event = self._path_test_stop

        def run():
            try:
                time.sleep(0.3)
                paths.replay_events(events, self.keyboard, stop_event)
                if stop_event.is_set():
                    self.push_log(f"[Debug] Stopped test-walking path \"{name}\".")
                else:
                    self.push_log(f"[Debug] Finished test-walking path \"{name}\".")
            except Exception as exc:
                self.push_log(f"[Debug] Path test failed: {exc}")
            finally:
                # The UI swapped Run for Stop when this started, and only
                # stop_test_path() ever swapped it back -- so a replay that
                # ended on its own left a Stop button offering to stop a walk
                # that had already finished, for the rest of the session.
                # A log line is not enough; the button state needs telling.
                # finally: so a crash restores it too. Same push_ui signal the
                # Tesseract install already uses for its own unknown-duration
                # button (see tesseractInstallDone in ui/app.js).
                self.push_ui("testPathFinished")

        threading.Thread(target=run, daemon=True).start()
        return {"ok": True}

    def stop_test_path(self) -> dict:
        if self._path_test_stop is not None:
            self._path_test_stop.set()
        return {"ok": True}

    def list_map_categories(self) -> list:
        from core import maps
        return maps.list_categories()

    def list_stage_data_maps(self) -> list:
        # Settings > Debug > "Read Rewards" map picker: whatever's actually
        # in Assets/stage_data.json (see tools/fetch_stage_data.py), not a
        # hardcoded list -- stays correct if the wiki adds a map before this
        # dropdown's own code does.
        from core import stage_data
        return stage_data.list_maps()

    def list_maps(self, category: str) -> list:
        from core import maps
        return maps.list_maps(category)

    def get_map_image(self, category: str, name: str) -> dict:
        from core import maps
        uri = maps.map_image_data_uri(category, name)
        if not uri:
            return {"ok": False, "reason": "not_found"}
        return {"ok": True, "data_uri": uri}

    def get_roblox_snapshot(self) -> dict:
        # Macro Manager > Place Unit > Set > "Use Roblox Screen": a one-shot,
        # inert screenshot of the docked Roblox window to click positions
        # against instead of a static map reference image. No input is ever
        # sent and focus never changes, so it can never reach the actual game.
        # The picker only ever clicks on this frozen image afterward; it's a
        # clone for planning, not a live view.
        #
        # The UI (usePlaceUnitRobloxScreen) switches to the Dashboard before
        # calling this and switches back after, so by the time we run here the
        # game is visible and actually presenting frames -- the exact same
        # dance the debug screenshot does, because it's the one capture path
        # that demonstrably works. All this has to do is raise the game above
        # the WebView2 child (both are children of the GUI window, and the
        # grab photographs whichever is topmost in that region) and grab its
        # rect. The was_hidden branch guards the corner case of being called
        # while the game is still hidden mid-transition.
        hwnd = self.game_hwnd
        if not hwnd or not wm.is_window(hwnd):
            return {"ok": False, "reason": "no_roblox"}

        try:
            was_hidden = not wm.is_window_visible(hwnd)
            if was_hidden:
                wm.show_window(hwnd)
                time.sleep(0.4)  # let it present a frame before photographing its rect
            wm.bring_to_top(hwnd)  # z-order only: no focus, no activation, no input
            try:
                if sys.platform == "darwin":
                    # A rectangular MSS grab photographs the desktop surface.
                    # Roblox is rendered on a separate macOS compositor surface,
                    # so that grab can contain only the wallpaper behind the game.
                    # Quartz's per-window capture reads Roblox's own surface and is
                    # already the capture path used by the live vision system.
                    captured = wm.capture_window_rgb(hwnd)
                    if not captured:
                        return {"ok": False, "reason": "capture_failed"}
                    rgb, width, height = captured
                    import numpy as np
                    image = np.frombuffer(rgb, np.uint8).reshape(height, width, 3)[:, :, ::-1]
                else:
                    left, top, right, bottom = wm.get_window_rect_screen(hwnd)
                    width, height = right - left, bottom - top
                    if width <= 0 or height <= 0:
                        return {"ok": False, "reason": "bad_region"}
                    import mss
                    with mss.MSS() as sct:
                        shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
                        bgra = shot.bgra
                    import numpy as np
                    image = np.frombuffer(bytearray(bgra), np.uint8).reshape(
                        shot.height, shot.width, 4)[:, :, :3]
            finally:
                if was_hidden:
                    wm.hide_window(hwnd)
            # Normalize to the reference 1152x756 before anything downstream
            # sees it: the Place Unit picker reads click positions off this
            # image's own pixels and the Image Manager cuts reference crops
            # from it, so a Retina Mac's 2x-density grab (or an off-size
            # window) MUST be brought back to reference space here or every
            # position/crop derived from it lands double-scaled. Identity
            # (and skipped) at the Windows norm.
            import cv2
            if image.shape[:2] != (config.FIXED_WIN_H, config.FIXED_WIN_W):
                image = cv2.resize(image, (config.FIXED_WIN_W, config.FIXED_WIN_H), interpolation=cv2.INTER_AREA)
                width, height = config.FIXED_WIN_W, config.FIXED_WIN_H
            ok, encoded = cv2.imencode(".png", image)
            if not ok:
                return {"ok": False, "reason": "encode_failed"}
            png_bytes = encoded.tobytes()
        except Exception as exc:
            return {"ok": False, "reason": str(exc)}

        import base64
        return {
            "ok": True,
            "data_uri": "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii"),
            "width": width, "height": height,
        }

    def get_reward_region(self) -> dict:
        data = cfg.load()
        return {
            "x": data.get("reward_region_x", REWARD_REGION_DEFAULTS["x"]),
            "y": data.get("reward_region_y", REWARD_REGION_DEFAULTS["y"]),
            "width": data.get("reward_region_w", REWARD_REGION_DEFAULTS["width"]),
            "height": data.get("reward_region_h", REWARD_REGION_DEFAULTS["height"]),
        }

    def save_reward_region(self, x: int, y: int, width: int, height: int) -> dict:
        cfg.update({
            "reward_region_x": int(x), "reward_region_y": int(y),
            "reward_region_w": int(width), "reward_region_h": int(height),
        })
        return {"ok": True}

    def reset_reward_region(self) -> dict:
        cfg.update({
            "reward_region_x": REWARD_REGION_DEFAULTS["x"],
            "reward_region_y": REWARD_REGION_DEFAULTS["y"],
            "reward_region_w": REWARD_REGION_DEFAULTS["width"],
            "reward_region_h": REWARD_REGION_DEFAULTS["height"],
        })
        return self.get_reward_region()

    def preview_reward_region(self) -> dict:
        # Settings > Debug > "Preview": saves exactly what Read Rewards would
        # capture, with the auto-detected icon-cell boundaries drawn on top
        # in green, to a PNG next to main.py -- garbled OCR is ambiguous
        # (wrong region vs. text that's genuinely hard to read), a picture of
        # the actual capture isn't.
        hwnd = self.game_hwnd
        if not hwnd or not wm.is_window(hwnd):
            return {"ok": False, "reason": "no_roblox"}

        region = self.get_reward_region()
        path = os.path.join(_debug_dir(), "debug_reward_region.png")

        try:
            from core import rewards
            image = _capture_game_region(hwnd, region)
            rewards.save_region_preview(image, path)
        except Exception as exc:
            self.push_log(f"[Rewards] Preview failed: {exc}")
            return {"ok": False, "reason": str(exc)}

        self.push_log(f"[Rewards] Saved region preview to {path} -- open it to check alignment.")
        return {"ok": True, "path": path}

    def read_rewards(self, map_name: str = "", stage: str = "", difficulty: str = "Normal") -> dict:
        # Settings > Debug > "Read Rewards": crops the Victory screen's reward
        # grid (region calibrated against the *docked* Roblox client -- offsets
        # are relative to the game window's own top-left, not the screen, so
        # this keeps working regardless of where the macro window sits), scrolls
        # to pick up a big drop that overflows the visible box (see below), then
        # hands the captured image(s) off to a background thread for OCR so this
        # call -- and the mouse/scroll sequence in particular -- doesn't sit
        # blocked on the slow part. Capture-and-scroll only takes ~1s and needs
        # to happen in order right now (the mouse is mid-sequence); OCR takes
        # several seconds and has nothing left to coordinate once the pixels are
        # in hand, so it runs after this call has already returned, logging each
        # item as its own [Rewards] line as it finishes.
        #
        # map_name/stage/difficulty are optional and only used to narrow icon
        # identification (see core.stage_data.expected_item_names) -- lets
        # this button be used to test/tune the reward reader against
        # whatever's already on screen (an old Victory screen, a manually
        # navigated one, ...) without needing to actually win a fresh match
        # through the real macro run just to check a reading.
        hwnd = self.game_hwnd
        if not hwnd or not wm.is_window(hwnd):
            return {"ok": False, "reason": "no_roblox"}

        allowed_names = None
        amounts = None
        if map_name and stage:
            try:
                from core import stage_data
                allowed_names = stage_data.expected_item_names(map_name, stage, difficulty) or None
                amounts = stage_data.expected_item_amounts(map_name, stage, difficulty) or None
            except Exception:
                allowed_names = None
                amounts = None

        region = self.get_reward_region()
        game_left, game_top, _, _ = wm.get_window_rect_screen(hwnd)

        try:
            from core import rewards

            image_top = _capture_game_region(hwnd, region)

            probe_x, probe_y, probe_w, probe_h = REWARD_SCROLLBAR_PROBE
            has_more = _game_region_color_matches(
                hwnd, probe_x, probe_y, probe_w, probe_h, REWARD_SCROLLBAR_COLOR,
                tolerance=rewards.SCROLLBAR_TOLERANCE,
            )
            image_bottom = None
            if not has_more:
                self.push_log("[Rewards] Reward list fits in view -- no scroll needed.")
            if has_more:
                self.push_log("[Rewards] Reward list overflows -- scrolling for the rest.")
                # The click that triggered this call left the macro's own
                # webview panel with OS focus, not the docked Roblox window --
                # mouse wheel messages go to whichever window actually has
                # focus, not just whatever the cursor sits over, so scrolling
                # was silently going nowhere regardless of cursor position or
                # timing. Same activate_window() the undock path already uses
                # to hand Roblox real input focus.
                wm.activate_window(hwnd)
                time.sleep(0.1)

                box_cx = game_left + region["x"] + region["width"] // 2
                box_cy = game_top + region["y"] + region["height"] // 2
                self.mouse.move_to(box_cx, box_cy)
                time.sleep(0.05)
                # A jump straight to the box center is an absolute-position
                # message -- the scrollable panel doesn't count that as real
                # hover and silently ignores wheel input right after it. A
                # tiny relative wiggle (same trick Mouse.click() uses before
                # clicking) forces an actual mouse-move event first.
                self.mouse.nudge()
                time.sleep(0.2)
                # Enough wheel notches to bottom out any reasonably long
                # list -- scrolling past the bottom is a no-op, so there's
                # no need to know the real row count up front.
                for _ in range(20):
                    self.mouse.scroll(-120)
                    time.sleep(0.02)
                time.sleep(0.2)  # let the scroll-snap animation settle

                image_bottom = _capture_game_region(hwnd, region)
                # Move off the reward box once scrolling is done, same
                # reasoning as core.runner's automatic post-match read.
                self.mouse.move_to(game_left + 3, game_top + 3)
        except Exception as exc:
            self.push_log(f"[Rewards] Capture failed: {exc}")
            return {"ok": False, "reason": str(exc)}

        self.push_log("[Rewards] Reading...")
        threading.Thread(
            target=self._read_rewards_background, args=(image_top, image_bottom, allowed_names, amounts),
            daemon=True
        ).start()
        return {"ok": True, "started": True}

    def _read_rewards_background(self, image_top, image_bottom, allowed_names: list = None,
                                   amounts: dict = None) -> None:
        try:
            from core import rewards
            pages = [rewards.read_reward_grid(image_top, allowed_names=allowed_names, amounts=amounts)]
            if image_bottom is not None:
                pages.append(rewards.read_reward_grid(image_bottom, allowed_names=allowed_names, amounts=amounts))
            items = rewards.merge_reward_pages(*pages)
        except Exception as exc:
            self.push_log(f"[Rewards] Read failed: {exc}")
            return

        if not items:
            self.push_log("[Rewards] No reward icons read -- check the region in Settings > Debug.")
        for item in items:
            qty = item["quantity"] or "?"
            name = item["name"] or "(unreadable)"
            self.push_log(f"[Rewards] {qty} {name}")
        self.push_log(f"[Rewards] Done -- {len(items)} item(s).")

    def get_stats_region(self) -> dict:
        data = cfg.load()
        return {
            "x": data.get("stats_region_x", STATS_REGION_DEFAULTS["x"]),
            "y": data.get("stats_region_y", STATS_REGION_DEFAULTS["y"]),
            "width": data.get("stats_region_w", STATS_REGION_DEFAULTS["width"]),
            "height": data.get("stats_region_h", STATS_REGION_DEFAULTS["height"]),
        }

    def save_stats_region(self, x: int, y: int, width: int, height: int) -> dict:
        cfg.update({
            "stats_region_x": int(x), "stats_region_y": int(y),
            "stats_region_w": int(width), "stats_region_h": int(height),
        })
        return {"ok": True}

    def reset_stats_region(self) -> dict:
        cfg.update({
            "stats_region_x": STATS_REGION_DEFAULTS["x"],
            "stats_region_y": STATS_REGION_DEFAULTS["y"],
            "stats_region_w": STATS_REGION_DEFAULTS["width"],
            "stats_region_h": STATS_REGION_DEFAULTS["height"],
        })
        return self.get_stats_region()

    def preview_stats_region(self) -> dict:
        # Settings > Debug > "Preview" (Game Stats): saves exactly what Read
        # Game Stats would capture, same reasoning as preview_reward_region --
        # a picture of the actual capture makes a bad calibration obvious.
        hwnd = self.game_hwnd
        if not hwnd or not wm.is_window(hwnd):
            return {"ok": False, "reason": "no_roblox"}

        region = self.get_stats_region()
        path = os.path.join(_debug_dir(), "debug_game_stats.png")

        try:
            from core import game_stats
            image = _capture_game_region(hwnd, region)
            game_stats.save_region_preview(image, path)
        except Exception as exc:
            self.push_log(f"[Stats] Preview failed: {exc}")
            return {"ok": False, "reason": str(exc)}

        self.push_log(f"[Stats] Saved region preview to {path} -- open it to check alignment.")
        return {"ok": True, "path": path}

    def read_game_stats(self) -> dict:
        # Settings > Debug > "Read Game Stats": crops the Victory screen's
        # stats panel (Clear Time / Total Yen / Total Kills / Total Damage,
        # a fixed 2x2 grid -- see core.game_stats) and OCRs each value,
        # logging them as one [Stats]-tagged line in the Process Log.
        hwnd = self.game_hwnd
        if not hwnd or not wm.is_window(hwnd):
            return {"ok": False, "reason": "no_roblox"}

        region = self.get_stats_region()

        try:
            from core import game_stats
            image = _capture_game_region(hwnd, region)
            stats = game_stats.read_game_stats(image)
        except Exception as exc:
            self.push_log(f"[Stats] Read failed: {exc}")
            return {"ok": False, "reason": str(exc)}

        clear_time = stats.get("clear_time") or "?"
        yen = stats.get("total_yen") or "?"
        kills = stats.get("total_kills") or "?"
        damage = stats.get("total_damage") or "?"
        self.push_log(f"[Stats] Clear Time {clear_time} | Yen {yen} | Kills {kills} | Damage {damage}")
        return {"ok": True, "stats": stats}

    def detach_game_safely(self) -> None:
        """Un-parent Roblox from our window so destroying the GUI can't
        cascade WM_DESTROY into it (Windows kills child windows with their
        parent -- a still-parented Roblox goes down too, and a half-torn-
        down game leaks). Idempotent and swallow-everything: run from every
        exit path (close button, Alt+F4, AND an atexit backstop for crashes/
        force-exits), so calling it twice or on an already-detached game is
        harmless. Sets stopping FIRST so the dock watchdog stops trying to
        re-parent while this runs."""
        self.stopping.set()
        hwnd = self.game_hwnd
        if not hwnd:
            return
        try:
            if not self.docker.undock(hwnd):
                self.logger.log("Warning: could not confirm Roblox was detached before closing.")
            else:
                # undock's SetParent(0) is verified inside, but give the
                # reparent a beat to fully commit before the window dies.
                time.sleep(0.1)
        except Exception as exc:
            self.logger.log(f"Detach-on-close failed: {exc}")

    def close_window(self):
        # Quitting the macro must only ever detach Roblox, never take it down
        # with it: Windows destroys child windows when their parent closes,
        # so Roblox has to be un-parented *before* this window is destroyed.
        self.detach_game_safely()
        self.persist_all_time()
        from core import vision
        vision.close_all_mss()
        self.logger.close()
        if self._window:
            self._window.destroy()


def _launch_ui():
    import webview  # imported lazily so --test works without pywebview/keyboard installed
    import keyboard

    # pywebview's frameless drag region defaults to starting a window-drag on
    # ANY mousedown inside .pywebview-drag-region, including on buttons/icons
    # nested in it (there's no CSS opt-out on Windows, unlike Electron's
    # -webkit-app-region) -- this restricts a drag to only start when the
    # click's literal target is the drag-region element itself, so clicking a
    # nav/titlebar button no longer drags the whole window.
    webview.settings['DRAG_REGION_DIRECT_TARGET_ONLY'] = True

    api = Api()
    # First line of every session's debug.log on purpose -- exactly which
    # tagged version AND which exact source revision (for anyone running
    # from source between releases, which is most of this app's own
    # testing) produced a given log is otherwise unrecoverable once
    # several untagged fixes have landed since the last real release, and
    # a pasted debug.log with no version context at all wastes a round
    # trip just asking "which build is this from?" every time.
    api.push_log(f"[Macro] Cream's Macro v{updater.get_current_version()} ({_get_build_info()}) starting...")
    # Diagnostic: confirms whether set_dpi_aware() (called at import time,
    # above the wm.set_dpi_aware() call at module scope) actually took --
    # a non-100% value here with docking/clicks still landing wrong would
    # point elsewhere; still 100 despite real display scaling means it
    # didn't take and every fixed coordinate in core.runner is off. Every
    # fixed coordinate in core.runner was captured/tuned at 100% Windows
    # display scale -- set_dpi_aware() makes the PROCESS report real
    # physical pixels regardless of scale, but Windows still stretches
    # what's actually drawn on screen at non-100%, which is a real (if
    # smaller) source of drift set_dpi_aware() can't fix on its own. Below
    # 100% shows a one-time warning telling the user to fix it at the
    # source, same troubleshooting-log spirit as the DPI/focus fixes
    # already in core.window.
    if sys.platform == "darwin":
        # The two macOS permissions everything depends on -- surfaced
        # loudly at startup instead of letting "clicks do nothing" or
        # "windows won't move" be diagnosed from symptoms. See
        # core/window_mac.py's module docstring.
        try:
            from core import window_mac
            if not window_mac.ax_trusted():
                api.push_log("[Macro] macOS Accessibility permission NOT granted -- window arranging and "
                              "input will not work. Enable this app under System Settings > Privacy & "
                              "Security > Accessibility (and Input Monitoring), then restart it.")
        except Exception as exc:
            api.push_log(f"[Macro] Couldn't check macOS permissions: {exc}")
    scale = wm.get_display_scale_percent()
    api.push_log(f"[Macro] Display scale: {scale}%.")
    if scale != 100:
        api.push_log(f"[Macro] Windows display scale is {scale}%, not 100% -- this is a common cause of "
                       f"clicks/detection landing slightly wrong. Set it to 100% in Settings > System > Display, "
                       f"then restart your computer (not just the macro) so it fully takes effect.")
        api.push_ui("showScaleWarning")
    gui_wm = WindowManager(GUI_TITLE)
    roblox_wm = WindowManager(config.ROBLOX_WINDOW_TITLE)  # only used for its resize/client-rect helpers below

    screen_w, screen_h = wm.get_screen_size()
    if sys.platform == "darwin":
        # Side-by-side arrangement (see core/dock.py's darwin GameDocker) needs the panel width
        # plus the full fixed game size in logical points. Smaller/lower-scaled MacBook displays
        # (e.g. a 13" panel left at its default 1280x800 scaled resolution) don't have that much
        # logical width even though the physical panel is plenty big -- Roblox ends up parked
        # partly or fully off-screen with no error, which just looks like "the game is too big".
        needed_w = GUI_WIDTH_COMPACT + MAC_GAP + config.FIXED_WIN_W
        if screen_w < needed_w or screen_h < config.FIXED_WIN_H:
            api.push_log(
                f"[Macro] Your display's logical resolution ({screen_w}x{screen_h}pt) is smaller than "
                f"what side-by-side docking needs ({needed_w}x{config.FIXED_WIN_H}pt) -- Roblox will be "
                f"placed partly or fully off-screen. Fix: System Settings > Displays > select a scaled "
                f"resolution with \"More Space\" (a higher point resolution, not necessarily higher "
                f"physical res) so it's at least that wide.")
    start_w, start_h = GUI_WIDTH_COMPACT, GUI_HEIGHT_COMPACT
    start_x = (screen_w - start_w) // 2
    start_y = (screen_h - start_h) // 2

    window = webview.create_window(
        GUI_TITLE,
        url=UI_INDEX,
        js_api=api,
        width=start_w,
        height=start_h,
        x=start_x,
        y=start_y,
        resizable=False,
        frameless=True,
        easy_drag=False,  # dragging is handled by the .pywebview-drag-region element in ui/index.html instead
    )
    api.set_window(window)

    def _set_window_icon_background():
        # pywebview's own icon= start() param only works on GTK/QT, not the
        # Windows EdgeChromium backend this app actually uses (see
        # core.window.set_window_icon) -- and the native window doesn't
        # exist to set an icon ON until webview.start()'s GUI loop actually
        # creates it, hence polling here rather than doing this right after
        # create_window() above.
        deadline = time.time() + 10
        while time.time() < deadline:
            hwnd = gui_wm.find()
            if hwnd:
                wm.set_window_icon(hwnd, LOGO_ICO)
                return
            time.sleep(0.2)

    threading.Thread(target=_set_window_icon_background, daemon=True).start()

    def _check_for_update_background():
        # A few seconds after launch, not immediately -- so a slow/offline
        # GitHub request can never compete with the app's own startup for
        # attention. push_ui (no args, same pattern as showDocked/
        # showWaiting) just tells the UI to go ask get_update_info() for the
        # details once it actually has something to show.
        time.sleep(4)
        try:
            api._update_info = updater.check_for_update(log=api.push_log)
        except Exception as exc:
            api.push_log(f"[Update] Check failed: {exc}")
            return
        if api._update_info.get("available"):
            api.push_log(f'[Update] Version {api._update_info["version"]} is available.')
            api.push_ui("showUpdateAvailable")

    threading.Thread(target=_check_for_update_background, daemon=True).start()

    def _ensure_assets_background():
        # Assets/ ships as a loose folder beside the exe (see core.constants.
        # ASSETS_DIR), so a bare exe with no Assets next to it (shared solo,
        # or an old bootstrapper install from before the zip layout) would
        # have every image search dead on arrival. This restores it from the
        # release zip's Assets when missing -- a no-op costing one isdir/
        # listdir in the normal case, and on a background thread so a slow
        # download can never hold up startup.
        try:
            updater.ensure_assets_present(api.push_log)
        except Exception as exc:
            api.push_log(f"[Update] Assets check failed: {exc}")

    threading.Thread(target=_ensure_assets_background, daemon=True).start()

    def _dock_watchdog():
        """Runs for the app's whole lifetime, not just once at startup, so it
        also catches Roblox being launched late, or relaunched after a crash
        (a new hwnd that needs re-docking), not just the first window found.

        Wrapped in try/except per iteration on purpose: an unhandled exception
        in a daemon thread just kills the thread silently, and the UI would be
        stuck showing "waiting" forever with no error and no further retries,
        which looked exactly like the app being frozen/broken.
        """
        while not api.stopping.is_set():
            try:
                if api.game_hwnd and not wm.is_window(api.game_hwnd):
                    # tracked window died (closed/crashed): allow re-attaching to a new one
                    api.docker.docked = False
                    api.game_hwnd = None
                    api.push_ui("showWaiting")
                    api.push_log("Roblox window closed, waiting for it again.")
                    # Arm the auto-reopen ONLY if a run was actually live when
                    # Roblox vanished -- snapshotting is_running() right here,
                    # while the runner's own recovery is still ticking on the
                    # now-dead hwnd, is what tells "the game crashed mid-run"
                    # apart from "the run had already finished and the user
                    # closed Roblox". The reopen + resume happen further down.
                    if api.runner.is_running():
                        api._resume_after_relaunch = True

                # Explicit Un-Attach (Settings > Debug): skip auto-detect
                # entirely until the user picks a window and clicks Attach
                # again -- otherwise find_roblox_window() below would just
                # find the same still-open window and instantly redock it,
                # making Un-Attach a no-op.
                if api.dock_suspended:
                    time.sleep(2)
                    continue

                # A manual Attach pins the NEXT dock to a specific window
                # (see attach_roblox_window) instead of whatever
                # find_roblox_window() would grab on its own -- with
                # multiple Roblox windows open, that's always just the
                # first one EnumWindows happens to return, not necessarily
                # the one actually picked.
                if api.pinned_hwnd and wm.is_window(api.pinned_hwnd):
                    hwnd = api.pinned_hwnd
                else:
                    api.pinned_hwnd = None
                    hwnd = wm.find_roblox_window()  # title AND process name: a Chrome tab titled "Roblox" won't match

                # Roblox is gone and a run wants it back: reopen the game via
                # the same deep link the runner's own rejoin uses, so an
                # unattended run heals a crash/close instead of sitting on
                # "waiting" forever. Throttled (ROBLOX_RELAUNCH_COOLDOWN) so a
                # slow boot isn't hit with a second launch, and skipped when
                # other Roblox windows are open -- the deep link's single-
                # instance handling would force-close them (alt accounts).
                if (not hwnd and api._resume_after_relaunch
                        and not api.runner.is_rejoin_pending()):
                    try:
                        want_reopen = cfg.load().get("auto_relaunch_roblox", True)
                    except Exception:
                        want_reopen = True
                    now = time.time()
                    if (want_reopen and hasattr(os, "startfile")
                            and now - api._roblox_relaunch_at >= ROBLOX_RELAUNCH_COOLDOWN):
                        try:
                            others = wm.list_roblox_windows()
                        except Exception:
                            others = []
                        if others:
                            api._roblox_relaunch_at = now  # also rate-limits this log
                            api.push_log("Roblox closed mid-run, but other Roblox windows are open -- "
                                         "not auto-reopening (it would close them).")
                        elif not api.runner.claim_rejoin_launch():
                            api.push_log("Roblox rejoin started elsewhere -- not auto-reopening a second instance.")
                        else:
                            from core.runner_constants import REJOIN_DEEPLINK
                            api._roblox_relaunch_at = now
                            try:
                                os.startfile(REJOIN_DEEPLINK)
                                api.push_log("Roblox closed mid-run -- reopening the game automatically...")
                            except OSError as exc:
                                api.runner.cancel_rejoin_launch()
                                api.push_log(f"Couldn't auto-reopen Roblox: {exc}")

                if hwnd and (not api.docker.docked or hwnd != api.game_hwnd):
                    api.push_log("Roblox found, settling before docking...")
                    api.game_hwnd = hwnd

                    # Give a freshly-launched Roblox window a moment to finish its own
                    # startup/resize before we touch its borders and reparent it:
                    # docking it mid-launch is what left the game looking broken.
                    time.sleep(1.0)
                    if api.stopping.is_set():
                        return
                    if not wm.is_window(hwnd):
                        api.push_log("Roblox window disappeared before docking, will retry.")
                        api.game_hwnd = None
                        time.sleep(2)
                        continue

                    # Un-Attach (or a different Attach pick) can land WHILE
                    # this settle sleep was running -- without this check,
                    # the dock below would commit anyway, ignoring it: the
                    # window would end up reparented and hidden with
                    # api.game_hwnd already cleared back to None (Detach
                    # already ran), so nothing would be left tracking it to
                    # ever show it again. That's exactly what "Roblox just
                    # disappears and stays gone until I close the macro"
                    # was -- a still-hidden, still-parented child window
                    # that only went away when closing the app destroyed it.
                    if api.dock_suspended or (api.pinned_hwnd and api.pinned_hwnd != hwnd):
                        api.push_log("Dock aborted -- the Roblox Window selection changed while settling.")
                        api.game_hwnd = None
                        time.sleep(1)
                        continue

                    roblox_wm.hwnd = hwnd
                    roblox_wm.resize_client_to()

                    if sys.platform == "darwin":
                        # macOS can't embed another app's window (no
                        # SetParent -- see core/dock.py's darwin
                        # GameDocker), so instead of growing the GUI to
                        # make room for a docked child, the panel stays
                        # compact at the screen's left edge and Roblox is
                        # arranged immediately to its right at the exact
                        # reference size. (One-shot per dock, same as the
                        # Windows path -- if the game gets dragged away
                        # mid-session, image search still lands correctly
                        # via vision's reference-space scaling; it's just
                        # no longer beside the panel.)
                        # Both windows are placed from one layout (see
                        # _mac_panel_layout): the panel is created centered and
                        # compact, so it has to be moved AND grown to the left
                        # strip here -- leaving it centered is what put it
                        # floating over the middle of the game, and leaving it
                        # compact is what made the real UI unreachable.
                        layout = _mac_panel_layout()
                        gui_hwnd = gui_wm.find()
                        if gui_hwnd and not api.stopping.is_set():
                            api.gui_hwnd = gui_hwnd
                            with api._mac_geometry_lock:
                                api._apply_panel_geometry(
                                    layout["x"], layout["y"], layout["panel_w"], layout["panel_h"])
                                api._mac_panel_ready = True
                            api.docker.dock(hwnd, gui_hwnd, x=layout["game_x"], y=layout["game_y"])
                            api.pinned_hwnd = None
                            api.push_ui("showDocked")
                            api.push_log(
                                f'Roblox arranged beside the panel (macOS side-by-side mode): panel '
                                f'{layout["panel_w"]}x{layout["panel_h"]}, game at x={layout["game_x"]}.')
                        else:
                            api.push_log("Could not find the macro's own window, will retry.")
                        time.sleep(2)
                        continue

                    # The window may be minimized right now (Start Minimized, or
                    # the user minimized it while waiting). A resize issued on a
                    # minimized window is silently dropped and it restores at the
                    # old compact size (verified against pywebview 6.2.1), which
                    # docked Roblox into a 400px-wide window. Restore first.
                    window.restore()
                    time.sleep(0.2)
                    window.resize(GUI_WIDTH_FULL, GUI_HEIGHT_FULL)
                    window.move(0, 0)
                    time.sleep(0.3)
                    gui_hwnd = gui_wm.find()
                    if gui_hwnd and not api.stopping.is_set():
                        # Belt and braces: confirm the resize actually took before
                        # parenting Roblox into the window, falling back to a
                        # native MoveWindow if pywebview's resize was lost. Never
                        # dock into a still-compact window.
                        gui_wm.hwnd = gui_hwnd
                        l, t, r, b = wm.get_window_rect_screen(gui_hwnd)
                        if (r - l, b - t) != (GUI_WIDTH_FULL, GUI_HEIGHT_FULL):
                            wm.move_window(gui_hwnd, 0, 0, GUI_WIDTH_FULL, GUI_HEIGHT_FULL)
                            time.sleep(0.2)
                            l, t, r, b = wm.get_window_rect_screen(gui_hwnd)
                            if (r - l) <= GUI_WIDTH_COMPACT + 50:
                                api.push_log("Macro window still compact, retrying dock...")
                                time.sleep(2)
                                continue
                            elif (r - l, b - t) != (GUI_WIDTH_FULL, GUI_HEIGHT_FULL):
                                api.push_log(f"Warning: Macro window size ({r - l}x{b - t}) is smaller than requested "
                                             f"({GUI_WIDTH_FULL}x{GUI_HEIGHT_FULL}) due to display resolution or DPI scaling. Docking anyway.")
                        api.gui_hwnd = gui_hwnd
                        # Final stopping re-check: several sleeps have passed
                        # since the one guarding this branch, and a dock()
                        # committed AFTER close set stopping would re-parent
                        # Roblox right before the window dies -- recreating
                        # the cascade the whole close path exists to prevent.
                        if api.stopping.is_set():
                            return
                        api.docker.dock(hwnd, gui_hwnd, x=0, y=TITLEBAR_H)
                        # Stay hidden until the JS side explicitly shows it for the Task
                        # screen (showDocked() does that) — Info/Settings/Macro Manager are the
                        # default/other screens now, and Roblox is a native window that
                        # would otherwise render on top of them regardless of DOM state.
                        # Cutout mode never hides the window (captures read its
                        # contents) -- "hidden" there is parked at the bottom of
                        # the z-order until show_game promotes it.
                        if api.docker.cutout:
                            wm.send_to_bottom(hwnd)
                        else:
                            wm.hide_window(hwnd)
                        api.pinned_hwnd = None  # dock succeeded -- back to normal auto-tracking of this hwnd
                        api.push_ui("showDocked")
                        api.push_log("Roblox docked.")
                    else:
                        api.push_log("Could not find the macro's own window to dock into, will retry.")
            except Exception as exc:
                api.push_log(f"Dock watchdog error: {exc}")

            # Cutout mode's glue: while the game is meant to be visible,
            # re-assert its over-the-slot position every tick (tracks a
            # dragged GUI, heals lost topmost). Skipped while hidden --
            # re-promoting the game over the Settings screen every 2s would
            # BE the bug.
            try:
                if (sys.platform != "darwin" and api.docker.cutout and api.docker.docked
                        and api._cutout_game_visible
                        and api.game_hwnd and wm.is_window(api.game_hwnd)
                        and api.gui_hwnd and wm.is_window(api.gui_hwnd)):
                    api.docker.dock(api.game_hwnd, api.gui_hwnd, x=0, y=TITLEBAR_H)
            except Exception:
                pass

            # Run interrupted by Roblox closing, and the game is now back and
            # docked: pick the run up again so it doesn't need a human to hit
            # Start. Fires once per outage -- cleared whether the still-live
            # runner recovered onto the new window by itself (nothing to do)
            # or it had given up and we start a fresh run here.
            try:
                if (api._resume_after_relaunch and api.docker.docked
                        and api.game_hwnd and wm.is_window(api.game_hwnd)):
                    # Cleared either way -- the still-live runner already
                    # caught the re-dock by itself (nothing to do here), or it
                    # had given up and a fresh run starts below.
                    still_running = api.runner.is_running()
                    api._resume_after_relaunch = False
                    if not still_running:
                        # Not a resume: start_macro() re-enters the queue at
                        # the first task, so an interrupted task loses the
                        # repeats it had already done. Say that plainly rather
                        # than implying it picks up mid-task.
                        api.push_log("Roblox is back -- starting the macro again from the first task.")
                        api.start_macro()
            except Exception as exc:
                api.push_log(f"Auto-restart after reopening Roblox failed: {exc}")

            time.sleep(2)

    def _register_hotkeys(hotkeys: dict):
        # The `keyboard` lib's global hooks need root on macOS -- a plain
        # user launch raises OSError somewhere in here. Hotkeys just being
        # unavailable (use the on-screen buttons) beats the app dying, so
        # the whole registration is best-effort on that platform.
        try:
            keyboard.unhook_all()
        except (OSError, ImportError):
            api.push_log("[Macro] Global hotkeys unavailable (macOS needs the app run with elevated "
                          "permissions for keyboard hooks) -- use the on-screen buttons instead.")
            return
        actions = {
            # Routed through JS so each reuses its existing JS-side logic
            # (switchScreen's hide/show coordination, startMacro's button-
            # state + error-log handling) instead of a second, competing
            # implementation living here in Python.
            "toggle_game": lambda: api.push_ui("toggleGameScreenHotkey"),
            "skip_waiting": lambda: api.push_ui("skipWaiting"),
            "macro_start": lambda: api.push_ui("startMacro"),
            "macro_pause": lambda: api.push_ui("togglePauseMacro"),
            "debug_screenshot": lambda: api.push_ui("saveDebugScreenshot"),
            "image_manager": lambda: api.push_ui("toggleImageManagerHotkey"),
            "toggle_compact": lambda: api.push_ui("toggleCompactStrip"),
            # NOT routed through push_ui/JS: stopping has to win over
            # everything else regardless of what the UI thread is doing
            # (mid screen-switch animation, waiting on an evaluate_js round
            # trip, etc.), so this calls straight into the runner's
            # threading.Event from the hotkey's own thread. The button-state
            # sync (disabling Start, etc.) still happens -- refreshStatus's
            # poll picks up is_macro_running() within its own next tick --
            # it just isn't gating the actual stop signal anymore.
            "macro_stop": lambda: api.stop_macro(),
        }
        for action, fn in actions.items():
            key = hotkeys.get(action) or HOTKEY_DEFAULTS.get(action, "")
            if not key:
                continue
            try:
                keyboard.add_hotkey(key, fn, suppress=False)
            except (ValueError, ImportError, OSError):
                pass

    def on_shown():
        threading.Thread(target=_dock_watchdog, daemon=True).start()
        _register_hotkeys(api.get_hotkeys())
        api._on_hotkeys_changed = _register_hotkeys
        if cfg.load().get("start_minimized", False):
            window.minimize()

    def on_closing():
        # Fallback for close paths other than our custom titlebar button
        # (e.g. Alt+F4): close_window() already handles the normal case.
        api.detach_game_safely()
        api.persist_all_time()
        from core import vision
        vision.close_all_mss()
        api.logger.close()
        return True

    # Last-resort backstop: if the app exits any OTHER way -- an unhandled
    # exception during teardown, or webview.start() returning without the
    # graceful path having run -- atexit still detaches Roblox before the
    # process (and its child windows) go away. Cheap and idempotent.
    import atexit

    def _on_app_exit():
        api.detach_game_safely()
        from core import vision
        vision.close_all_mss()
        api.logger.close()

    atexit.register(_on_app_exit)

    window.events.shown += on_shown
    window.events.closing += on_closing
    webview.start()
    # webview.start() returns once the window is gone -- detach here too, in
    # case the window died without firing our handlers.
    _on_app_exit()
    try:
        keyboard.unhook_all()
    except OSError:
        pass  # macOS without hook permissions -- nothing was ever hooked


def test_mouse():
    mouse = Mouse()
    print("Current cursor position:", mouse.position())
    print("Moving mouse in a small square in 2s...")
    time.sleep(2)
    x, y = mouse.position()
    for dx, dy in [(100, 0), (0, 100), (-100, 0), (0, -100)]:
        mouse.move_to(x + dx, y + dy)
        time.sleep(0.3)


def test_keyboard():
    print("Typing 'hello' in 3s -- click into a text field now...")
    time.sleep(3)
    kb = Keyboard()
    kb.type_text("hello")
    kb.tap(keys.VK_RETURN)


def test_window():
    hwnd = wm.find_roblox_window()
    if not hwnd:
        print("Roblox window not found -- open Roblox and try again.")
        return
    wm_ = WindowManager("Roblox")
    wm_.hwnd = hwnd
    print("Found Roblox window:", hwnd)
    print("Client size before:", wm_.get_client_size())
    wm_.resize_client_to()
    print("Client size after resize:", wm_.get_client_size())
    print("Client (0,0) -> screen:", wm_.client_to_screen(0, 0))


TEST_MENU = {
    "1": ("Test mouse", test_mouse),
    "2": ("Test keyboard", test_keyboard),
    "3": ("Test window (find + resize Roblox)", test_window),
}


def run_diagnostics():
    print("Anime Expeditions -- core input/window diagnostics")
    for key, (label, _) in TEST_MENU.items():
        print(f"  {key}) {label}")
    print("  4) Run all")
    choice = input("Select: ").strip()

    if choice == "4":
        for _, fn in TEST_MENU.values():
            fn()
        return

    entry = TEST_MENU.get(choice)
    if not entry:
        print("Unknown option.")
        return
    entry[1]()


if __name__ == "__main__":
    if "--test" in sys.argv:
        run_diagnostics()
    else:
        _launch_ui()
