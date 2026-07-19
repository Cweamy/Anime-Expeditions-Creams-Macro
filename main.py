"""
Cream's Macro | Anime Expeditions
Run:  python main.py            (launches the docked macro UI)
      python main.py --test     (CLI diagnostics for mouse/keyboard/window)
"""
import os
import sys
import time
import json
import threading

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

GUI_TITLE = "Cream's Macro | Anime Expeditions"
PANEL_WIDTH = 400
TITLEBAR_H = 44  # custom HTML titlebar, since the window is frameless (no native OS titlebar)
LOGS_H = 160  # log strip under the docked Roblox window, same width as the game
GUI_WIDTH_FULL = config.FIXED_WIN_W + PANEL_WIDTH
GUI_WIDTH_COMPACT = PANEL_WIDTH
GUI_HEIGHT_FULL = TITLEBAR_H + config.FIXED_WIN_H + LOGS_H
GUI_HEIGHT_COMPACT = TITLEBAR_H + 280
UI_INDEX = os.path.join(constants.UI_DIR, "index.html")
LOGS_WINDOW_HTML = os.path.join(constants.UI_DIR, "logs_window.html")
LOGO_ICO = os.path.join(constants.BUNDLE_DIR, "logo.ico")
LOG_HISTORY_LIMIT = 500  # caps what a freshly popped-out window gets replayed with

HOTKEY_DEFAULTS = {
    "toggle_game": "f4", "skip_waiting": "", "macro_start": "f1", "macro_stop": "f2", "debug_screenshot": "f3",
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
}

# Settings > Debug > "Reward Reader"/"Game Stats": OCR capture regions for
# the Victory screen. Same "expose + reset" treatment as MACRO_COORD_DEFAULTS
# above, for the same reason -- a UI change in the game shifts these too.
REWARD_REGION_DEFAULTS = {"x": 212, "y": 429, "width": 504, "height": 106}
STATS_REGION_DEFAULTS = {"x": 210, "y": 337, "width": 509, "height": 57}

RUN_HISTORY_LIMIT = 50  # oldest entries drop off past this -- a running log, not a permanent archive


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
        self.stopping = threading.Event()
        self.logger = Logger()
        self.session_start = time.time()
        self._all_time_base = cfg.load().get("all_time_seconds", 0)
        self._on_hotkeys_changed = None
        self.mouse = Mouse()
        self.keyboard = Keyboard()
        self._path_test_stop = None
        # Live readout for the Dashboard's status panel -- get_status() merges
        # this over its placeholder defaults; the runner is the only thing
        # that ever writes to it (via the set_status callback below), one
        # dict instead of a pile of separate instance attributes since it's
        # just read back out as a dict anyway.
        self._run_status = {"current_task": "-", "current_repeat": "-", "map": "-", "action": "Idle"}
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
        self.runner = MacroRunner(
            self.mouse, self.keyboard, self.push_log, self._set_run_status, self._record_match_result)

    def _set_run_status(self, **kwargs) -> None:
        self._run_status.update(kwargs)
        self._pending_path_events = None  # stopped-but-not-yet-named recording (see stop_path_capture)

    def set_window(self, window):
        self._window = window

    def get_version(self) -> str:
        return updater.get_current_version()

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
            self._update_info = updater.check_for_update()
        threading.Thread(target=run, daemon=True).start()
        return {"ok": True}

    def apply_update(self) -> dict:
        if not self._update_info.get("available"):
            return {"ok": False, "reason": "no_update"}
        try:
            # Running as a built exe: swap the exe itself -- robocopying
            # loose .py source over a compiled exe's directory wouldn't do
            # anything, it doesn't read source files at runtime. Running
            # from source: the usual source-zip-over-the-install swap.
            if constants.IS_FROZEN:
                if not self._update_info.get("exe_url"):
                    self.push_log("[Update] No exe attached to this release -- can't self-update the build. "
                                  f'Grab it manually: {self._update_info.get("url")}')
                    return {"ok": False, "reason": "no_exe_asset"}
                new_exe = updater.download_exe_update(self._update_info["exe_url"], self.push_log)
                helper_path = updater.stage_exe_update(new_exe)
            else:
                helper_path = updater.stage_source_update(
                    self._update_info["zip_url"], constants.APP_DIR, self.push_log)
        except Exception as exc:
            self.push_log(f"[Update] Failed to prepare update: {exc}")
            return {"ok": False, "reason": str(exc)}
        self.push_log(f'[Update] Update to {self._update_info["version"]} staged -- restarting to apply it...')
        # Launch the detached helper BEFORE closing -- it waits for this
        # process to exit before touching any files, but it has to already
        # be running (and thus survive this process going away) first.
        updater.launch_helper(helper_path)
        threading.Timer(0.4, self.close_window).start()
        return {"ok": True}

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
        return {
            "docked": self.docker.docked,
            **self._run_status,
            "last_run": _format_ago(history[0]["at"]) if history else "-",
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / (wins + losses) * 100) if (wins + losses) else None,
            "time_until_challenge": "Disabled",
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
            "theme": data.get("theme", "default"),
            "story_scroll_power": data.get("story_scroll_power", 3),
            "story_scroll_nudges": data.get("story_scroll_nudges", 8),
            "debug_screenshots": data.get("debug_screenshots", False),
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
            default_walk_paths, reward_region, stats_region, webhook_settings)

    def debug_story_map_region(self) -> dict:
        # Settings > Debug > "Story Map Region": saves exactly the band
        # core.stage_select searches for map name labels, so it can be
        # visually checked/tuned (and cross-referenced against a reference
        # crop in Assets/maps/) without needing a match to trigger a
        # debug screenshot first.
        from core import stage_select, vision
        hwnd = self.game_hwnd
        if not hwnd or not wm.is_window(hwnd):
            return {"ok": False, "reason": "no_roblox"}
        path = vision.save_region_debug(hwnd, "story_map_band", stage_select.NAME_BAND_REGION)
        return {"ok": True, "path": path}

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
        # Creation > Custom Path > "Record": begins polling the player's own
        # WASD state (see core.paths) -- the player then walks the route
        # in-game themselves and clicks Stop when they've reached the end.
        # GetAsyncKeyState reads real key state regardless of focus, but the
        # recording is only useful if Roblox is actually the window
        # *processing* those WASD presses as movement -- otherwise the
        # player's character never walks and there's nothing meaningful to
        # capture. The Creation screen hides the docked game window entirely
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
        self.push_log(f"[Creation] Recorded path \"{saved_name}\" ({len(events)} key events).")
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
        self.push_log(f"[Creation] Recorded path \"{saved_name}\" ({len(events)} key events).")
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

    # Task screen > Export/Import: shares a task queue (plus the Creation
    # templates those tasks reference, bundled in by the JS side) as a single
    # JSON file through native save/open dialogs. Also reused by Creation's
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
        result = self._window.create_file_dialog(
            webview.SAVE_DIALOG, save_filename=fname, file_types=("JSON files (*.json)",))
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
        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG, file_types=("JSON files (*.json)",))
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

    def skip_waiting(self):
        # Lets the panel be used (config, etc.) before Roblox is even open.
        # The window has to actually resize to full size here, not just in
        # JS/CSS, since the two-column layout is wider than the compact window.
        if self._window:
            self._window.resize(GUI_WIDTH_FULL, GUI_HEIGHT_FULL)
            self._window.move(0, 0)
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

    def debug_test_path(self, name: str) -> dict:
        # Settings > Debug > "Test Walking Path": replays a path recorded via
        # Creation > Custom Path > Record (see core.paths.replay_events)
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
        # Creation > Place Unit > Set > "Use Roblox Screen": a one-shot,
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
            from mss.tools import to_png
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
                    rgb = shot.rgb
            finally:
                if was_hidden:
                    wm.hide_window(hwnd)
            png_bytes = to_png(rgb, (width, height))
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
        if map_name and stage:
            try:
                from core import stage_data
                allowed_names = stage_data.expected_item_names(map_name, stage, difficulty) or None
            except Exception:
                allowed_names = None

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
            target=self._read_rewards_background, args=(image_top, image_bottom, allowed_names), daemon=True
        ).start()
        return {"ok": True, "started": True}

    def _read_rewards_background(self, image_top, image_bottom, allowed_names: list = None) -> None:
        try:
            from core import rewards
            pages = [rewards.read_reward_grid(image_top, allowed_names=allowed_names)]
            if image_bottom is not None:
                pages.append(rewards.read_reward_grid(image_bottom, allowed_names=allowed_names))
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
    # Diagnostic: confirms whether set_dpi_aware() (called at import time,
    # above the wm.set_dpi_aware() call at module scope) actually took --
    # a non-100% value here with docking/clicks still landing wrong would
    # point elsewhere; still 100 despite real display scaling means it
    # didn't take and every fixed coordinate in core.runner is off.
    api.push_log(f"[Macro] Display scale: {wm.get_display_scale_percent()}%.")
    gui_wm = WindowManager(GUI_TITLE)
    roblox_wm = WindowManager(config.ROBLOX_WINDOW_TITLE)  # only used for its resize/client-rect helpers below

    screen_w, screen_h = wm.get_screen_size()
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
            api._update_info = updater.check_for_update()
        except Exception as exc:
            api.push_log(f"[Update] Check failed: {exc}")
            return
        if api._update_info.get("available"):
            api.push_log(f'[Update] Version {api._update_info["version"]} is available.')
            api.push_ui("showUpdateAvailable")

    threading.Thread(target=_check_for_update_background, daemon=True).start()

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

                    roblox_wm.hwnd = hwnd
                    roblox_wm.resize_client_to()
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
                        # screen (showDocked() does that) — Info/Settings/Creation are the
                        # default/other screens now, and Roblox is a native window that
                        # would otherwise render on top of them regardless of DOM state.
                        wm.hide_window(hwnd)
                        api.push_ui("showDocked")
                        api.push_log("Roblox docked.")
                    else:
                        api.push_log("Could not find the macro's own window to dock into, will retry.")
            except Exception as exc:
                api.push_log(f"Dock watchdog error: {exc}")

            time.sleep(2)

    def _register_hotkeys(hotkeys: dict):
        keyboard.unhook_all()
        actions = {
            # Routed through JS so each reuses its existing JS-side logic
            # (switchScreen's hide/show coordination, startMacro's button-
            # state + error-log handling) instead of a second, competing
            # implementation living here in Python.
            "toggle_game": lambda: api.push_ui("toggleGameScreenHotkey"),
            "skip_waiting": lambda: api.push_ui("skipWaiting"),
            "macro_start": lambda: api.push_ui("startMacro"),
            "debug_screenshot": lambda: api.push_ui("saveDebugScreenshot"),
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
    keyboard.unhook_all()


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
