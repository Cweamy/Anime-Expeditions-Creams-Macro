"""Records a player's own WASD (+ I/O) movement/action keys on the map into a
named JSON file, so a Custom Path block (see Creation tab) can replay it
later instead of relying on Auto Select's live pathing.

Recording works by *polling* the OS's live key state (GetAsyncKeyState on
Windows, CGEventSourceKeyState on macOS -- see the per-OS input backends)
for each watched key at a fixed interval on a background thread -- this
reads real physical key state regardless of which window has focus, unlike
a message-based keyboard hook, which matters here since the player is
actively controlling Roblox while this records. Only state *transitions*
(press/release) get logged, each timestamped relative to recording start,
rather than one entry per poll -- that's enough to reconstruct exactly
when each key was held and for how long, at a small fraction of the size
logging every poll tick would take.
"""
import json
import os
import re
import sys
import threading
import time

from . import constants

if sys.platform == "darwin":
    from . import _input_mac as _input_backend
else:
    from . import _input_win as _input_backend

# Writable -- your own recordings, has to live beside the real exe (see
# core.constants), not wherever a frozen build's temp extraction lands.
PATHS_DIR = os.path.join(constants.APP_DIR, "Paths")
# Known-good walk paths for specific maps/acts, shipped/bundled with the app
# (see Assets/default_walk_paths.json and .gitignore's Paths/defaults/
# exception) -- shared game data, not personal recordings, so unlike
# everything else in Paths/ these are git-tracked AND resolved via
# BUNDLE_DIR, not APP_DIR (a frozen build ships them inside the bundle, not
# beside the exe). load_path/list_paths fall back to this folder so a fresh
# clone/install gets working default walks with nothing to record first;
# saving a path under the same name in the regular (APP_DIR) Paths/ folder
# overrides it (see load_path).
DEFAULT_PATHS_DIR = os.path.join(constants.BUNDLE_DIR, "Paths", "defaults")
# The map-name -> path-name mapping to go with DEFAULT_PATHS_DIR above --
# read by main.Api.get_default_walk_paths/start_macro and merged with the
# user's own settings.json overrides (a user's own mapping for the same map
# wins). Lives in Assets/, which since the exe+Assets zip layout is the
# loose folder beside the exe (see core.constants.ASSETS_DIR), not inside
# the bundle.
SHIPPED_DEFAULT_WALK_PATHS_FILE = os.path.join(constants.ASSETS_DIR, "default_walk_paths.json")

_POLL_INTERVAL = 0.03  # 30ms -- well under human key-tap duration, cheap enough to poll forever
# W/A/S/D for movement, I/O for whatever in-game action a recorded route
# needs alongside walking (e.g. an interact/use key at a specific point) --
# recorded, replayed, and released-on-exit identically to the movement keys,
# since every place below just iterates this same dict.
_WATCHED_KEYS = {
    "w": ord("W"), "a": ord("A"), "s": ord("S"), "d": ord("D"),
    "i": ord("I"), "o": ord("O"),
}


class RecordingAlreadyActive(Exception):
    pass


class _Recorder:
    """One recording session's state -- module-level singleton since only
    one Custom Path block can realistically be recorded at a time (there's
    only one physical player controlling one game window)."""

    def __init__(self):
        self._thread = None
        self._stop_event = None
        self._events = []
        self._start_time = None
        self.active = False

    def start(self):
        if self.active:
            raise RecordingAlreadyActive("A path recording is already in progress.")
        self._events = []
        self._start_time = None
        self._stop_event = threading.Event()
        self.active = True
        self._thread = threading.Thread(target=self._poll_loop, args=(self._stop_event,), daemon=True)
        self._thread.start()

    def _poll_loop(self, stop_event: threading.Event) -> None:
        held = {key: False for key in _WATCHED_KEYS}
        while not stop_event.is_set():
            for key, vk in _WATCHED_KEYS.items():
                is_down = _input_backend.is_key_down(vk)
                if is_down != held[key]:
                    # The clock starts at the FIRST key transition, not at
                    # start(): however long the player fumbles between clicking
                    # Record and actually walking, the saved path begins at
                    # t=0 with the first press instead of replaying that whole
                    # dead wait at the start.
                    if self._start_time is None:
                        self._start_time = time.perf_counter()
                    held[key] = is_down
                    self._events.append({
                        "t": round(time.perf_counter() - self._start_time, 3),
                        "key": key,
                        "state": "down" if is_down else "up",
                    })
            time.sleep(_POLL_INTERVAL)

    def stop(self) -> list:
        if not self.active:
            return []
        self._stop_event.set()
        self._thread.join(timeout=1.0)
        self.active = False
        return self._events

    def cancel(self) -> None:
        if self.active:
            self._stop_event.set()
            self._thread.join(timeout=1.0)
            self.active = False
        self._events = []


_recorder = _Recorder()


def start_recording() -> None:
    _recorder.start()


def stop_recording() -> list:
    """Stops the active recording and returns its raw (key, state, t)
    event list without saving it -- save_path() persists it separately so
    the caller can name it first."""
    return _recorder.stop()


def cancel_recording() -> None:
    """Stops and discards the active recording -- used when the player
    starts a recording but then declines to name/save it."""
    _recorder.cancel()


def is_recording() -> bool:
    return _recorder.active


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 _-]", "", name or "").strip()
    return cleaned or "path"


def load_shipped_default_walk_paths() -> dict:
    try:
        with open(SHIPPED_DEFAULT_WALK_PATHS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def list_paths() -> list:
    names = set()
    if os.path.isdir(DEFAULT_PATHS_DIR):
        names.update(f[:-5] for f in os.listdir(DEFAULT_PATHS_DIR) if f.endswith(".json"))
    if os.path.isdir(PATHS_DIR):
        names.update(f[:-5] for f in os.listdir(PATHS_DIR) if f.endswith(".json"))
    return sorted(names)


def save_path(name: str, events: list) -> str:
    name = _safe_name(name)
    os.makedirs(PATHS_DIR, exist_ok=True)
    path = os.path.join(PATHS_DIR, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"name": name, "events": events}, f, indent=2)
    return name


def load_path(name: str) -> dict:
    safe = _safe_name(name)
    # Your own recording (Paths/<name>.json) wins if one exists under this
    # name -- only falls back to the shipped default when you haven't
    # recorded your own version of it.
    for directory in (PATHS_DIR, DEFAULT_PATHS_DIR):
        path = os.path.join(directory, f"{safe}.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
    return {"name": name, "events": []}


def replay_events(events: list, keyboard, stop_event: threading.Event = None) -> None:
    """Replays a recorded WASD event list through a Keyboard controller,
    sleeping between events to reproduce the original press/release timing
    (events are stored in recording order, each timestamped relative to
    recording start -- see _Recorder._poll_loop). Used by the Debug tab's
    "Test Walking Path" to sanity-check a recorded path plays back the way
    it was walked, without needing a Custom Path block wired into a real run.

    Always releases every watched key on the way out (including when
    stop_event cuts the replay short), so an interrupted test can't leave a
    direction stuck held down in the live game.
    """
    try:
        last_t = 0.0
        for ev in events:
            if stop_event is not None and stop_event.is_set():
                break
            delay = ev["t"] - last_t
            if delay > 0:
                time.sleep(delay)
            last_t = ev["t"]
            vk = _WATCHED_KEYS.get(ev["key"])
            if vk is None:
                continue
            if ev["state"] == "down":
                keyboard.key_down(vk)
            else:
                keyboard.key_up(vk)
    finally:
        for vk in _WATCHED_KEYS.values():
            keyboard.key_up(vk)
