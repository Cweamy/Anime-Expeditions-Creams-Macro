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
from .jsonstore import write_json_atomic

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
# Per-map ENCOUNTER walks -- same add-or-override role the file above plays
# for stage-entry walks (see _BUILTIN_ENCOUNTER_WALK_PATHS).
SHIPPED_ENCOUNTER_WALK_PATHS_FILE = os.path.join(constants.ASSETS_DIR, "default_encounter_walk_paths.json")

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
            for key in _WATCHED_KEYS:
                # By PHYSICAL position, not VK -- so an AZERTY/QWERTZ player's
                # movement presses are captured (on those layouts the physical
                # W position isn't VK_W). See _input_backend.is_move_key_down.
                is_down = _input_backend.is_move_key_down(key)
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


# Built-in default map/act -> walk-path-name mappings, baked into the CODE so
# they ship with the exe itself (delivered by the exe swap) rather than
# relying on Assets/default_walk_paths.json being refreshed by the updater.
# That refresh does NOT happen for an existing install: the add-only Assets
# merge only ever ADDS brand-new files -- a change to a file that shipped with
# the original install is treated as untracked and left untouched (see
# core.updater._extract_assets_zip_addonly). That gap is exactly why the Event
# Act walk paths didn't reach updated exe users even though the path files
# themselves (bundled in the exe under Paths/defaults) did. The JSON file
# still loads ON TOP of these, so it can add or override any entry without a
# code change -- these are just the guaranteed-delivered floor. Mirrors
# Assets/default_walk_paths.json.
_BUILTIN_DEFAULT_WALK_PATHS = {
    "Fairy King Forest": "Fairy King Forest",
    "King's Tomb": "Kings Tomb",
    "Spirit City Act3": "Spirit Act3",
    "Event Act1": "Villian1",
    "Event Act2": "Villian2",
}


def load_shipped_default_walk_paths() -> dict:
    merged = dict(_BUILTIN_DEFAULT_WALK_PATHS)
    try:
        with open(SHIPPED_DEFAULT_WALK_PATHS_FILE, "r", encoding="utf-8") as f:
            merged.update(json.load(f))
    except (OSError, json.JSONDecodeError):
        pass
    return merged


# A SECOND walk per map, walked MID-RUN rather than at stage entry. The
# mapping above answers "where do I stand when the stage opens"; this one
# answers "where is this map's encounter NPC".
#
# Expedition parks the client in the AFK Chamber when an encounter node is
# reached and nothing handles it. Handling it means walking to an NPC whose
# location differs per map, so the route cannot be one shared recording.
#
# Same two-tier delivery as the stage-entry mapping: baked into the code so an
# exe swap carries it, with Assets/default_encounter_walk_paths.json loading on
# top so a map can be added or a route replaced without a code change.
_BUILTIN_ENCOUNTER_WALK_PATHS = {
    "School Grounds": "Expedition Encounter - School Grounds",
    "Rose Kingdom": "Expedition Encounter - Rose Kingdom",
    "Flower Forest": "Expedition Encounter - Flower Forest",
    "East Town": "Expedition Encounter - East Town",
}


def load_shipped_encounter_walk_paths() -> dict:
    """Map name -> the recording that walks to that map's encounter NPC.

    A map with no entry has no encounter walk yet; callers log and skip rather
    than failing, so adding maps stays additive.
    """
    merged = dict(_BUILTIN_ENCOUNTER_WALK_PATHS)
    try:
        with open(SHIPPED_ENCOUNTER_WALK_PATHS_FILE, "r", encoding="utf-8") as f:
            merged.update(json.load(f))
    except (OSError, json.JSONDecodeError):
        pass
    return merged


def _stored_name(path: str, fallback: str) -> str:
    """The display name recorded inside a path file, or the filename if it has
    none (the shipped defaults, hand-dropped files) or won't parse."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("name") or fallback
    except (OSError, json.JSONDecodeError):
        return fallback


def _resolve(name: str, directory: str) -> str:
    """Absolute path of the recording called `name` inside `directory`.

    "<safe name>.json" is tried FIRST -- that is where every recording made
    before this existed lives, and the shipped defaults are named that way too,
    so an existing install resolves on the first check exactly as it always
    did. The directory scan only runs when that file is absent or holds a
    different display name (the collision case _free_slug creates)."""
    base = os.path.join(directory, f"{_safe_name(name)}.json")
    if os.path.isfile(base) and _stored_name(base, _safe_name(name)) == name:
        return base
    if os.path.isdir(directory):
        for fname in sorted(os.listdir(directory)):
            if not fname.endswith(".json"):
                continue
            full = os.path.join(directory, fname)
            if _stored_name(full, fname[:-5]) == name:
                return full
    return base


def _free_slug(name: str) -> str:
    """Filename to record `name` under -- its own slug if free or already
    hers, otherwise the next "<slug> (n)".

    _safe_name maps several distinct names onto one file ("King's Tomb" and
    "Kings Tomb" both become "Kings Tomb.json"), so the second recording used
    to silently destroy the first. Worse here than for templates: load_path
    treats a missing file as a miss and falls through to the shipped default,
    so a lost recording doesn't just do nothing -- it walks a different route
    through the map."""
    slug = _safe_name(name)
    candidate, n = slug, 2
    while True:
        path = os.path.join(PATHS_DIR, f"{candidate}.json")
        # A shipped default owns its slug too -- never shadow one with a
        # different recording under the same filename.
        shipped = os.path.join(DEFAULT_PATHS_DIR, f"{candidate}.json")
        taken = ((os.path.isfile(path) and _stored_name(path, candidate) != name)
                 or (os.path.isfile(shipped) and _stored_name(shipped, candidate) != name))
        if not taken:
            return candidate
        candidate = f"{slug} ({n})"
        n += 1


def list_paths() -> list:
    names = set()
    for directory in (DEFAULT_PATHS_DIR, PATHS_DIR):
        if not os.path.isdir(directory):
            continue
        for fname in os.listdir(directory):
            if fname.endswith(".json"):
                names.add(_stored_name(os.path.join(directory, fname), fname[:-5]))
    return sorted(names)


def list_custom_paths() -> list:
    """Names stored in the user-owned Paths folder, excluding shipped defaults."""
    if not os.path.isdir(PATHS_DIR):
        return []
    names = []
    for fname in os.listdir(PATHS_DIR):
        if fname.endswith(".json"):
            names.append(_stored_name(os.path.join(PATHS_DIR, fname), fname[:-5]))
    return sorted(set(names))


def save_path(name: str, events: list) -> str:
    name = (name or "").strip() or "path"
    os.makedirs(PATHS_DIR, exist_ok=True)
    path = os.path.join(PATHS_DIR, f"{_free_slug(name)}.json")
    # Atomic: an interrupted save must not truncate the recording that was
    # already there. load_path() treats a corrupt file as a miss and falls
    # through to the shipped default, so for a name that ships one the
    # replacement is silent AND walks a different route (see
    # core/jsonstore.py).
    write_json_atomic(path, {"name": name, "events": events})
    return name


def load_path(name: str) -> dict:
    # Your own recording (Paths/<name>.json) wins if one exists under this
    # name -- only falls back to the shipped default when you haven't
    # recorded your own version of it.
    for directory in (PATHS_DIR, DEFAULT_PATHS_DIR):
        try:
            with open(_resolve(name, directory), "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
    return {"name": name, "events": []}


def collect_paths(names) -> dict:
    """Bundle the given recorded walks as {name: {"name", "events"}} for
    embedding in a shared macro code. Names with no recording (or an empty
    one) are skipped -- there's nothing to carry, and the block would skip the
    walk on the far side anyway."""
    bundle = {}
    for name in names:
        data = load_path(name)
        events = data.get("events") or []
        if events:
            bundle[name] = {"name": data.get("name") or name, "events": events}
    return bundle


def import_path(name: str, events: list) -> str:
    """Persist a walk bundled with an imported macro and return the name the
    block should reference. If an identical recording already exists under
    this name (a shipped default, or a prior import of the same macro) it's
    reused as-is. If a DIFFERENT recording holds the name, the bundle is saved
    under the next free "<name> (n)" instead -- save_path keys its own
    collision check on the display name, not the content, so saving straight
    over the same name would silently destroy the existing recording. The
    caller remaps the block's pathName to whatever comes back."""
    name = (name or "").strip() or "path"
    events = list(events or [])
    candidate, n = name, 2
    while True:
        existing_events = load_path(candidate).get("events") or []
        if not existing_events:
            # Free (nothing recorded under this name) -- save here.
            return save_path(candidate, events)
        if existing_events == events:
            # Same walk already present -- reuse it, no duplicate.
            return candidate
        candidate = f"{name} ({n})"
        n += 1


def replay_events(events: list, keyboard, stop_event: threading.Event = None, sprint: bool = False) -> None:
    """Replays a recorded WASD event list through a Keyboard controller,
    sleeping between events to reproduce the original press/release timing
    (events are stored in recording order, each timestamped relative to
    recording start -- see _Recorder._poll_loop). Used by the Debug tab's
    "Test Walking Path" to sanity-check a recorded path plays back the way
    it was walked, without needing a Custom Path block wired into a real run.

    sprint=True holds Left Shift down for the WHOLE replay -- for maps whose
    default walk was recorded/timed while sprinting (the path only reaches
    its spot at sprint speed). Shift is released in the same finally as the
    direction keys, so an interrupted replay never leaves it stuck.

    Always releases every watched key on the way out (including when
    stop_event cuts the replay short), so an interrupted test can't leave a
    direction stuck held down in the live game.
    """
    from . import keys
    try:
        if sprint:
            keyboard.key_down(keys.VK_SHIFT)
        last_t = 0.0
        for ev in events:
            if stop_event is not None and stop_event.is_set():
                break
            delay = ev["t"] - last_t
            if delay > 0:
                # Event.wait() rather than time.sleep() so Stop cuts in during
                # the gap BETWEEN two key events, not just at the next one. A
                # recorded path can sit still for seconds at a time (walking
                # to a spot, waiting for an animation), and a plain sleep
                # blocks the stop for the whole of it. wait() returns True the
                # moment the event is set, so there's no polling either.
                if stop_event is not None:
                    if stop_event.wait(delay):
                        break
                else:
                    time.sleep(delay)
            last_t = ev["t"]
            name = ev["key"]
            if name not in _WATCHED_KEYS:
                continue
            # By physical position (move_key_*), so the same recorded path
            # walks correctly on any keyboard layout, not just the one it was
            # recorded on.
            if ev["state"] == "down":
                keyboard.move_key_down(name)
            else:
                keyboard.move_key_up(name)
    finally:
        for name in _WATCHED_KEYS:
            keyboard.move_key_up(name)
        if sprint:
            keyboard.key_up(keys.VK_SHIFT)
