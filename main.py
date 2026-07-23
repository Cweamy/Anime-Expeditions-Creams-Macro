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
import subprocess
import threading
from datetime import date

from core import window as wm
from core import config
from core import constants
from core import keys
from core import settings as cfg
from core import templates as tpl
from core import webhook
from core.window import WindowManager
from core.dock import GameDocker
from core.mouse import Mouse
from core.keyboard import Keyboard
from core.logger import Logger
from core.runner import MacroRunner
from core import updater

# Imported at module scope (not inside the darwin branches that use it) so the
# macOS-only geometry helpers below can be plain module functions. window_mac
# pulls in Quartz/AppKit, which don't exist on Windows -- hence the guard.
if sys.platform == "darwin":
    from core import window_mac
else:  # pragma: no cover -- Windows/Linux never reach the mac layout paths
    window_mac = None

wm.set_dpi_aware()


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

# ── macOS side-by-side geometry ─────────────────────────────────────────────
# On Windows the game is a child window INSIDE ours, so one window size covers
# both (GUI_*_FULL above). macOS can't embed another app's window at all (see
# core/dock.py), so panel and game are two top-level windows sharing the
# screen, and the panel's size is whatever the game doesn't need -- which is a
# different number on every Mac, hence computed at runtime from the visible
# frame rather than baked in as a constant.
MAC_GAP = 12  # visual breathing room between the panel and the game window
MAC_PANEL_MIN_W = PANEL_WIDTH  # narrower than the Windows panel column and it stops being usable
MAC_PANEL_MAX_W = 560  # past this the single-column dashboard just looks stretched
UI_INDEX = os.path.join(constants.UI_DIR, "index.html")
LOGS_WINDOW_HTML = os.path.join(constants.UI_DIR, "logs_window.html")
LOGO_ICO = os.path.join(constants.BUNDLE_DIR, "logo.ico")
LOG_HISTORY_LIMIT = 500  # caps what a freshly popped-out window gets replayed with

HOTKEY_DEFAULTS = {
    "toggle_game": "f4", "skip_waiting": "", "macro_start": "f1", "macro_stop": "f2", "macro_pause": "f5",
    "debug_screenshot": "f3",
    # Toggles the Image Manager from anywhere -- capturing a missing crop
    # right when a search fails shouldn't need clicking back through
    # Settings > General first.
    "image_manager": "f6",
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
    "expedition_difficulty_x": 1094, "expedition_difficulty_y": 456,
    "team_loadout_x": 800, "team_loadout_y": 324, "team_loadout_row_height": 126,
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
# through one of the 5 Story maps over time, so config/count-tracking is
# keyed by MAP (which macro to run for it, how many times it's been played
# today) while the 3 slots are just simple on/off toggles for "attempt
# whatever's in this slot". CHALLENGE_STORY_MAPS matches TASK_DATA.story's
# maps in ui/app.js. The exact in-game reset schedule isn't confirmed yet
# (see get_challenge_settings' daily-rollover comment) -- CHALLENGE_
# DAILY_CAP and the reset mechanism are a first-pass approximation, not
# verified against the real game yet.
CHALLENGE_STORY_MAPS = ["School Grounds", "Rose Kingdom", "Fairy King Forest", "King's Tomb", "Flower Forest"]
CHALLENGE_STAGE_SLOTS = ["1", "2", "3"]
CHALLENGE_DAILY_CAP = 10  # fixed, not user-editable -- see get_challenge_settings


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
    """"Ready" if any enabled, not-yet-capped stage slot hasn't been
    played in the CURRENT :00/:30 window yet; otherwise MM:SS until the
    next :00/:30 mark (same clock for every slot). "All capped" if every
    enabled slot has hit today's daily cap, "No stages enabled" if none
    are toggled on at all. Computed fresh on every get_status() poll, same
    "don't store text, compute it live" approach as _format_ago."""
    cap = challenge.get("cap", 0)
    any_enabled = False
    any_uncapped = False
    for info in challenge.get("stages", {}).values():
        if not info.get("enabled"):
            continue
        any_enabled = True
        if cap and info.get("count", 0) >= cap:
            continue
        any_uncapped = True
        if info.get("ready"):
            return "Ready"
    if not any_enabled:
        return "No stages enabled"
    if not any_uncapped:
        return "All capped"
    now = time.time()
    local = time.localtime(now)
    secs_into_hour = local.tm_min * 60 + local.tm_sec
    remaining = (1800 - secs_into_hour) if secs_into_hour < 1800 else (3600 - secs_into_hour)
    mins, secs = divmod(int(remaining), 60)
    return f"{mins:02d}:{secs:02d}"


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
    panel_w = max(MAC_PANEL_MIN_W, min(MAC_PANEL_MAX_W, width - config.FIXED_WIN_W - MAC_GAP))
    return {
        "x": x, "y": y,
        "panel_w": panel_w, "panel_h": height,
        "expanded_w": width,
        "game_x": x + panel_w + MAC_GAP, "game_y": y,
    }


class Api:
    """Exposed to the frontend as `pywebview.api.*`: the JS <-> Python bridge.
    Grows as the task/placement/upgrade systems get built; for now it just
    reports docking status so the UI has something real to show."""

    def __init__(self):
        self._window = None
        self._log_window = None
        self._log_history = []
        self.docker = GameDocker()
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
        # macOS side-by-side layout state (see set_panel_expanded): the panel
        # only starts trading width against the game once it has actually been
        # arranged, and the lock keeps the screen-switch resizes from
        # interleaving with the dock watchdog's own re-arrange.
        self._mac_panel_ready = False
        self._mac_panel_width = None  # last applied width, so repeat calls are free
        self._mac_geometry_lock = threading.Lock()
        self.stopping = threading.Event()
        self.logger = Logger()
        self.session_start = time.time()
        self._all_time_base = cfg.load().get("all_time_seconds", 0)
        self._on_hotkeys_changed = None
        self.mouse = Mouse()
        self.keyboard = Keyboard()
        self._path_test_stop = None
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
            self.get_challenge_settings, self.mark_challenge_stage_played)

    def _set_run_status(self, **kwargs) -> None:
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
                if sys.platform == "darwin":
                    # The mac build is a .app bundle -- swapping a whole
                    # bundle in place is a different (unbuilt, untested)
                    # dance from the single-file exe swap below. Until a
                    # tester-verified implementation exists, point at the
                    # zip instead of half-doing it.
                    msg = ("Self-update isn't supported on the macOS build yet -- download the new "
                           f"macOS zip from {self._update_info.get('url')} and replace the app.")
                    self.push_log(f"[Update] {msg}")
                    self._update_progress = {"phase": "error", "percent": None, "message": msg}
                    return
                if not self._update_info.get("release_zip_url"):
                    msg = ("No release zip attached to this release -- can't self-update the build. "
                           f'Grab it manually: {self._update_info.get("url")}')
                    self.push_log(f"[Update] {msg}")
                    self._update_progress = {"phase": "error", "percent": None, "message": msg}
                    return
                # One download covers the whole update: the new exe is
                # extracted out of the release zip and staged for the swap,
                # and any reference images NEW in this release are add-only
                # merged from that same file (never overwriting the user's
                # own edited/added images -- see core.updater's Assets
                # section) before the restart.
                new_exe = updater.download_release_update(
                    self._update_info["release_zip_url"], self.push_log, on_progress)
                self._update_progress = {"phase": "staging", "percent": 100, "message": "Preparing update..."}
                helper_path = updater.stage_exe_update(new_exe)
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
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / (wins + losses) * 100) if (wins + losses) else None,
            "time_until_challenge": _time_until_challenge_ready(challenge) if challenge.get("enabled") else "Disabled",
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
        data = cfg.load()
        data["all_time_seconds"] = self._all_time_base + elapsed
        cfg.save(data)

    def _record_match_result(self, result: str, map_name: str, duration: str,
                              stats: dict = None, items: list = None) -> None:
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
        data[key] = data.get(key, 0) + 1
        history = data.get("run_history", [])
        history.insert(0, {"result": result, "map": map_name or "-", "duration": duration or "-", "at": time.time()})
        data["run_history"] = history[:RUN_HISTORY_LIMIT]
        cfg.save(data)

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
        }

    def get_tasks(self) -> list:
        return cfg.load().get("tasks", [])

    def get_macro_coords(self) -> dict:
        data = cfg.load()
        return {k: data.get(k, v) for k, v in MACRO_COORD_DEFAULTS.items()}

    def set_macro_coord(self, key: str, value: int) -> dict:
        if key not in MACRO_COORD_DEFAULTS:
            return {"ok": False}
        data = cfg.load()
        data[key] = int(value)
        cfg.save(data)
        return {"ok": True}

    def reset_macro_coords(self) -> dict:
        data = cfg.load()
        for key, default in MACRO_COORD_DEFAULTS.items():
            data[key] = default
        cfg.save(data)
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
        data = cfg.load()
        defaults = dict(data.get("default_walk_paths", {}))
        if path_name:
            defaults[map_name] = path_name
        else:
            defaults.pop(map_name, None)
        data["default_walk_paths"] = defaults
        cfg.save(data)
        return {"ok": True, "default_walk_paths": defaults}

    # ---- Challenge tab ----
    def _default_challenge_settings(self) -> dict:
        return {
            "enabled": False,
            "play_mode": "solo",
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
            "last_reset_date": date.today().isoformat(),
        }

    def get_challenge_settings(self) -> dict:
        # Regular Challenge's daily play count resets once a calendar day --
        # checked/applied here (not on a timer) so it's caught the moment
        # anything asks for the settings, whether that's the Challenge
        # screen loading or the runner checking availability before a run.
        # Uses the LOCAL calendar day as the reset boundary, which is a
        # first-pass approximation -- swap for the real in-game reset time
        # once it's confirmed (see CHALLENGE_STORY_MAPS' comment). This does
        # NOT touch last_played_at -- that's checked against the current
        # :00/:30 window instead, independent of the daily count reset.
        data = cfg.load()
        saved = data.get("challenge") or {}
        defaults = self._default_challenge_settings()
        merged = {**defaults, **saved}
        merged["cap"] = CHALLENGE_DAILY_CAP  # fixed -- ignore any stale saved value from before this was hardcoded
        if merged.get("play_mode") not in ("solo", "matchmaking"):
            merged["play_mode"] = "solo"
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

        today = date.today().isoformat()
        if merged.get("last_reset_date") != today:
            for s in merged["stages"].values():
                s["count"] = 0
            merged["last_reset_date"] = today
            data["challenge"] = merged
            cfg.save(data)
            self.push_log("[Challenge] Daily play counts reset.")
        return merged

    def mark_challenge_stage_played(self, stage: str, count_play: bool = True) -> dict:
        # Called by the runner right after actually running a Challenge
        # stage -- starts that slot's cooldown (not ready again until the
        # next :00/:30 window) and bumps its daily count in one write.
        # count_play=False is the LOSS case: the cooldown still applies
        # (retrying the same rotated-in stage right away just loses again
        # -- wait for the next window), but a loss shouldn't eat one of the
        # day's capped plays the way a real completion does.
        if stage not in CHALLENGE_STAGE_SLOTS:
            return {"ok": False, "reason": "bad_stage"}
        data = cfg.load()
        challenge = self.get_challenge_settings()
        challenge["stages"][stage]["last_played_at"] = time.time()
        if count_play:
            challenge["stages"][stage]["count"] += 1
        data["challenge"] = challenge
        cfg.save(data)
        return {"ok": True}

    def set_challenge_enabled(self, enabled: bool) -> dict:
        data = cfg.load()
        challenge = self.get_challenge_settings()
        challenge["enabled"] = bool(enabled)
        data["challenge"] = challenge
        cfg.save(data)
        return {"ok": True}

    def set_challenge_play_mode(self, play_mode: str) -> dict:
        if play_mode not in ("solo", "matchmaking"):
            return {"ok": False, "reason": "bad_play_mode"}
        data = cfg.load()
        challenge = self.get_challenge_settings()
        challenge["play_mode"] = play_mode
        data["challenge"] = challenge
        cfg.save(data)
        return {"ok": True}

    def set_challenge_stage_enabled(self, stage: str, enabled: bool) -> dict:
        if stage not in CHALLENGE_STAGE_SLOTS:
            return {"ok": False, "reason": "bad_stage"}
        data = cfg.load()
        challenge = self.get_challenge_settings()
        challenge["stages"][stage]["enabled"] = bool(enabled)
        data["challenge"] = challenge
        cfg.save(data)
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
        data = cfg.load()
        challenge = self.get_challenge_settings()
        challenge["stages"][stage]["count"] = count
        data["challenge"] = challenge
        cfg.save(data)
        return {"ok": True}

    def set_challenge_map_macro(self, map_name: str, macro: str) -> dict:
        if map_name not in CHALLENGE_STORY_MAPS:
            return {"ok": False, "reason": "bad_map"}
        data = cfg.load()
        challenge = self.get_challenge_settings()
        challenge["maps"][map_name]["macro"] = macro or ""
        data["challenge"] = challenge
        cfg.save(data)
        return {"ok": True}

    def reset_challenge_counts(self) -> dict:
        data = cfg.load()
        challenge = self.get_challenge_settings()
        for s in challenge["stages"].values():
            s["count"] = 0
            s["last_played_at"] = 0  # also clears cooldown -- every slot becomes available immediately
        challenge["last_reset_date"] = date.today().isoformat()
        data["challenge"] = challenge
        cfg.save(data)
        self.push_log("[Challenge] Play counts and cooldowns reset manually.")
        return {"ok": True}

    def start_macro(self) -> dict:
        data = cfg.load()
        scroll_power = data.get("story_scroll_power", 3)
        scroll_nudges = data.get("story_scroll_nudges", 8)
        coords = {k: data.get(k, v) for k, v in MACRO_COORD_DEFAULTS.items()}
        debug_screenshots = data.get("debug_screenshots", False)
        default_walk_paths = self.get_default_walk_paths()
        reward_region = self.get_reward_region()
        stats_region = self.get_stats_region()
        webhook_settings = self.get_webhook_settings()
        return self.runner.start(
            lambda: self.game_hwnd, self.get_tasks, scroll_power, coords, scroll_nudges, debug_screenshots,
            default_walk_paths, reward_region, stats_region, webhook_settings,
            expedition_color_buttons=data.get("expedition_color_buttons", True))

    def stop_macro(self) -> dict:
        return self.runner.stop()

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
        data = cfg.load()
        data["tasks"] = tasks
        cfg.save(data)
        return {"ok": True}

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

    def set_setting(self, key: str, value) -> dict:
        data = cfg.load()
        data[key] = value
        cfg.save(data)
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
        data = cfg.load()
        keys_ = dict(HOTKEY_DEFAULTS)
        keys_.update(data.get("hotkeys", {}))
        keys_[action] = (key or "").lower()
        data["hotkeys"] = keys_
        cfg.save(data)
        if self._on_hotkeys_changed:
            self._on_hotkeys_changed(keys_)
        return {"ok": True}

    def reset_hotkeys(self) -> dict:
        data = cfg.load()
        data["hotkeys"] = dict(HOTKEY_DEFAULTS)
        cfg.save(data)
        if self._on_hotkeys_changed:
            self._on_hotkeys_changed(dict(HOTKEY_DEFAULTS))
        return {"ok": True, "hotkeys": dict(HOTKEY_DEFAULTS)}

    # Task screen > Export/Import: shares a task queue (plus the Macro Manager
    # templates those tasks reference, bundled in by the JS side) as a single
    # JSON file through native save/open dialogs. Also reused by Macro Manager's
    # template Export/Import (see ui/app.js's exportTemplates) -- the file
    # shape is just whatever payload/dict the caller hands in, so nothing
    # here is actually task-specific except the default filename.
    def export_tasks_file(self, payload: dict, filename_prefix: str = "tasks") -> dict:
        import json
        import time as _time
        import webview
        if not self._window:
            return {"ok": False, "reason": "no_window"}
        fname = f"AnimeExpeditions-{filename_prefix}-{_time.strftime('%Y%m%d-%H%M%S')}.json"
        # Defaults to the Templates folder -- Export/Import already deals in
        # Macro Operation templates half the time (a task export bundles
        # every template its tasks reference; Macro Manager's own Export/Import
        # reuses this same dialog for templates alone), so that's the more
        # useful starting point than whatever generic default the OS picks.
        os.makedirs(tpl.TEMPLATES_DIR, exist_ok=True)
        result = self._window.create_file_dialog(
            webview.SAVE_DIALOG, directory=tpl.TEMPLATES_DIR, save_filename=fname,
            file_types=("JSON files (*.json)",))
        if not result:
            return {"ok": False, "reason": "cancelled"}
        path = result[0] if isinstance(result, (list, tuple)) else result
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except OSError as exc:
            return {"ok": False, "reason": str(exc)}
        return {"ok": True, "path": path}

    def import_tasks_file(self) -> dict:
        import json
        import webview
        if not self._window:
            return {"ok": False, "reason": "no_window"}
        os.makedirs(tpl.TEMPLATES_DIR, exist_ok=True)
        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG, directory=tpl.TEMPLATES_DIR, file_types=("JSON files (*.json)",))
        if not result:
            return {"ok": False, "reason": "cancelled"}
        path = result[0] if isinstance(result, (list, tuple)) else result
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            return {"ok": False, "reason": str(exc)}
        return {"ok": True, "data": data}

    def list_templates(self) -> list:
        return tpl.list_templates()

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

    def get_webhook_settings(self) -> dict:
        data = cfg.load()
        return {
            "url": data.get("webhook_url", ""),
            "enabled": data.get("webhook_enabled", False),
            "silent": data.get("webhook_silent", False),
            "mention_id": data.get("webhook_mention_id", ""),
        }

    def save_webhook_settings(self, url: str, enabled: bool, silent: bool, mention_id: str = "") -> dict:
        data = cfg.load()
        data["webhook_url"] = url or ""
        data["webhook_enabled"] = bool(enabled)
        data["webhook_silent"] = bool(silent)
        data["webhook_mention_id"] = (mention_id or "").strip()
        cfg.save(data)
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

    def push_log(self, message: str) -> None:
        self.logger.log(message)
        self._log_history.append(message)
        if len(self._log_history) > LOG_HISTORY_LIMIT:
            self._log_history = self._log_history[-LOG_HISTORY_LIMIT:]
        for win in (self._window, self._log_window):
            if not win:
                continue
            try:
                win.evaluate_js(f"window.addLog && window.addLog({json.dumps(message)})")
            except Exception:
                pass

    def clear_logs(self) -> None:
        # Drops the replay buffer too, so a log window popped out *after* this
        # won't come back seeded with lines the user just cleared.
        self._log_history = []
        for win in (self._window, self._log_window):
            if not win:
                continue
            try:
                win.evaluate_js("window.clearLogs && window.clearLogs()")
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
            for line in self._log_history:
                try:
                    win.evaluate_js(f"window.addLog && window.addLog({json.dumps(line)})")
                except Exception:
                    pass

        def _on_closed():
            self._log_window = None

        win.events.shown += _seed
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
        if self.game_hwnd and wm.is_window(self.game_hwnd):
            wm.show_window(self.game_hwnd)

    def hide_game(self):
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

        Never expands WHILE THE MACRO IS RUNNING. Expanding covers Roblox, and
        not everything that reads the game can see through that: core/vision.py
        is immune (it reads the window's own backing store on mac -- see
        _use_window_capture there), but core/ocr.py's capture_region is a plain
        screen grab of a screen-space rect, so wave/reward/stats OCR would read
        the panel's own pixels instead of the game. Staying collapsed mid-run
        costs a cramped Settings screen; expanding costs silently wrong OCR."""
        if sys.platform != "darwin" or not self._window or not self._mac_panel_ready:
            return
        if expanded and self.runner.is_running():
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
            os.startfile(constants.ASSETS_OVERRIDE_DIR)
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
        return {"ok": True, "categories": categories}

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
                left, top, right, bottom = wm.get_window_rect_screen(hwnd)
                width, height = right - left, bottom - top
                if width <= 0 or height <= 0:
                    return {"ok": False, "reason": "bad_region"}
                import mss
                with mss.MSS() as sct:
                    shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
                    bgra = shot.bgra
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
            import numpy as np
            image = np.frombuffer(bytearray(bgra), np.uint8).reshape(shot.height, shot.width, 4)[:, :, :3]
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
        data = cfg.load()
        data["reward_region_x"] = int(x)
        data["reward_region_y"] = int(y)
        data["reward_region_w"] = int(width)
        data["reward_region_h"] = int(height)
        cfg.save(data)
        return {"ok": True}

    def reset_reward_region(self) -> dict:
        data = cfg.load()
        data["reward_region_x"] = REWARD_REGION_DEFAULTS["x"]
        data["reward_region_y"] = REWARD_REGION_DEFAULTS["y"]
        data["reward_region_w"] = REWARD_REGION_DEFAULTS["width"]
        data["reward_region_h"] = REWARD_REGION_DEFAULTS["height"]
        cfg.save(data)
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
        game_left, game_top, _, _ = wm.get_window_rect_screen(hwnd)
        path = os.path.join(_debug_dir(), "debug_reward_region.png")

        try:
            from core import rewards
            image = rewards.capture_region(
                game_left + region["x"], game_top + region["y"], region["width"], region["height"]
            )
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
            from core.ocr import capture_region, sample_color_matches

            image_top = capture_region(
                game_left + region["x"], game_top + region["y"], region["width"], region["height"]
            )

            probe_x, probe_y, probe_w, probe_h = REWARD_SCROLLBAR_PROBE
            has_more = sample_color_matches(
                game_left + probe_x, game_top + probe_y, probe_w, probe_h, REWARD_SCROLLBAR_COLOR,
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

                image_bottom = capture_region(
                    game_left + region["x"], game_top + region["y"], region["width"], region["height"]
                )
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
        data = cfg.load()
        data["stats_region_x"] = int(x)
        data["stats_region_y"] = int(y)
        data["stats_region_w"] = int(width)
        data["stats_region_h"] = int(height)
        cfg.save(data)
        return {"ok": True}

    def reset_stats_region(self) -> dict:
        data = cfg.load()
        data["stats_region_x"] = STATS_REGION_DEFAULTS["x"]
        data["stats_region_y"] = STATS_REGION_DEFAULTS["y"]
        data["stats_region_w"] = STATS_REGION_DEFAULTS["width"]
        data["stats_region_h"] = STATS_REGION_DEFAULTS["height"]
        cfg.save(data)
        return self.get_stats_region()

    def preview_stats_region(self) -> dict:
        # Settings > Debug > "Preview" (Game Stats): saves exactly what Read
        # Game Stats would capture, same reasoning as preview_reward_region --
        # a picture of the actual capture makes a bad calibration obvious.
        hwnd = self.game_hwnd
        if not hwnd or not wm.is_window(hwnd):
            return {"ok": False, "reason": "no_roblox"}

        region = self.get_stats_region()
        game_left, game_top, _, _ = wm.get_window_rect_screen(hwnd)
        path = os.path.join(_debug_dir(), "debug_game_stats.png")

        try:
            from core import game_stats
            from core.ocr import capture_region
            image = capture_region(
                game_left + region["x"], game_top + region["y"], region["width"], region["height"]
            )
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
        game_left, game_top, _, _ = wm.get_window_rect_screen(hwnd)

        try:
            from core import game_stats
            from core.ocr import capture_region
            image = capture_region(
                game_left + region["x"], game_top + region["y"], region["width"], region["height"]
            )
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

    def close_window(self):
        # Quitting the macro must only ever detach Roblox, never take it down
        # with it: Windows destroys child windows when their parent closes,
        # so Roblox has to be un-parented *before* this window is destroyed.
        self.stopping.set()
        self.persist_all_time()
        if self.game_hwnd:
            if not self.docker.undock(self.game_hwnd):
                self.logger.log("Warning: could not confirm Roblox was detached before closing.")
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
        needed_w = GUI_WIDTH_COMPACT + 12 + config.FIXED_WIN_W
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
                            if (r - l, b - t) != (GUI_WIDTH_FULL, GUI_HEIGHT_FULL):
                                api.push_log("Macro window would not reach full size, retrying dock...")
                                time.sleep(2)
                                continue
                        api.gui_hwnd = gui_hwnd
                        api.docker.dock(hwnd, gui_hwnd, x=0, y=TITLEBAR_H)
                        # Stay hidden until the JS side explicitly shows it for the Task
                        # screen (showDocked() does that) — Info/Settings/Macro Manager are the
                        # default/other screens now, and Roblox is a native window that
                        # would otherwise render on top of them regardless of DOM state.
                        wm.hide_window(hwnd)
                        api.pinned_hwnd = None  # dock succeeded -- back to normal auto-tracking of this hwnd
                        api.push_ui("showDocked")
                        api.push_log("Roblox docked.")
                    else:
                        api.push_log("Could not find the macro's own window to dock into, will retry.")
            except Exception as exc:
                api.push_log(f"Dock watchdog error: {exc}")

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
        api.stopping.set()
        api.persist_all_time()
        if api.game_hwnd:
            if not api.docker.undock(api.game_hwnd):
                api.push_log("Warning: could not confirm Roblox was detached before closing.")
        return True

    window.events.shown += on_shown
    window.events.closing += on_closing
    webview.start()
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
