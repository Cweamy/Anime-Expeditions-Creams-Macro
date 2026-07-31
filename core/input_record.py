"""Records a player's own full mouse + keyboard input -- movement, clicks,
scroll, and every key, not just WASD -- into a named JSON file, so a Record
block (see Macro Manager) can replay it later. Companion to core.paths, which
only records WASD/I/O movement for Walk Path/Walk blocks; this is the
general-purpose recorder for arbitrary action sequences those don't cover
(a multi-step UI interaction, a precisely-timed combo, ...) -- the same idea
as a TinyTask-style recorder, built into a Macro Operation block.

Recording uses global hooks (the `keyboard` package, already a dependency for
Settings > Hotkeys, and the `mouse` package) instead of polling every possible
key/button -- there are far too many keys to poll cheaply the way core.paths
polls just w/a/s/d/i/o, and a hook reports exact press/release/move/scroll
events with real timestamps regardless of which window has focus, which is
what a player actively controlling Roblox while this records needs.

Mouse positions are converted to REFERENCE space (core.vision's 1152x756
reference client area) at capture time using the game window's rect at that
instant, and a move/click/scroll outside the window's bounds is dropped --
both for correctness (a recording should only replay actions that landed on
the game) and, incidentally, to keep clicking this feature's own "Stop
Recording" button (which the UI deliberately keeps outside the docked game's
rect, see ui/index.html's #rec-popout) out of the saved recording. That means
this module needs no special-case exclusion for its own UI at all -- it
falls out of the same bounds check every other captured point already needs.

A click's "up" is the one exception: it is bounds-checked only as part of
deciding whether to record the "down" (see _Recorder._buttons_down). An "up"
is never independently re-checked against the cursor's position a moment
later -- doing that used to let a down get recorded while its up was
filtered out (the cursor read a pixel or two differently by the time the up
fired), leaving an orphaned down with no matching up in the saved recording,
which then replays as that mouse button held down for the rest of the
replay.

The hook callbacks themselves (_on_key_event/_on_mouse_event) do the
absolute minimum: capture a timestamp (and, for a click/scroll, the cursor
position -- read HERE, not later, since it can only be trusted at the exact
instant the event fired) and hand the raw tuple off to a queue.Queue,
nothing else. All the actual work -- coordinate conversion (a GetWindowRect
call), bounds checking, building the event dict -- happens on a separate
background worker thread pulling off that queue. A hook callback that's slow
to return risks the OS (or the `keyboard`/`mouse` library's own internal
relay) dropping events during a burst of rapid clicks or key presses; moving
every bit of non-trivial work off that thread is what keeps the callback
fast enough to never be the bottleneck, however busy the recording gets.
"""
import base64
import ctypes
import json
import os
import queue
import re
import sys
import threading
import zlib
import time

from . import constants
from . import keys
from . import vision
from . import window as wm
from .config import FIXED_WIN_H, FIXED_WIN_W
from .jsonstore import write_json_atomic

# Windows only: process-wide multimedia timer resolution. time.sleep() and
# threading.Event.wait() are only as precise as the OS scheduler's timer
# tick, which defaults to ~15.6ms -- a delay recorded as 3-8ms (typical
# between two mouse-move samples) actually sleeps for one whole 15.6ms tick,
# so a dense replay comes out visibly slower and jerkier than what was
# recorded no matter how tight the sampling is. timeBeginPeriod(1) asks
# Windows for ~1ms scheduler granularity for as long as it's held (see
# replay_events' try/finally, which always calls timeEndPeriod to release it
# again -- holding it longer than necessary needlessly costs system power).
if sys.platform == "win32":
    _winmm = ctypes.windll.winmm
else:
    _winmm = None

# Writable -- your own recordings, has to live beside the real exe (see
# core.constants), not wherever a frozen build's temp extraction lands. No
# shipped-defaults folder like Paths/defaults/: unlike a walk route there is
# no game-wide "known good" recording to ship, every one of these is bespoke
# to whatever the block is meant to do.
RECORDINGS_DIR = os.path.join(constants.APP_DIR, "Recordings")

# Below this interval a new mouse-move sample is dropped -- a hook callback
# can fire well over 100 times/sec on a fast mouse, and replaying every one
# of those is a much bigger file for no visible gain. 125Hz (~every 8ms)
# comfortably beats a 60Hz display's own repaint rate, so the recorded path
# is at least as smooth as what the player actually saw while recording it.
_MOVE_MIN_INTERVAL = 0.008

_MOUSE_BUTTONS = {"left", "right", "middle"}


class RecordingAlreadyActive(Exception):
    pass


class _Recorder:
    """One recording session's state -- module-level singleton since only one
    Record block can realistically be recorded at a time (there's only one
    physical player controlling one game window), same pattern as
    core.paths._recorder.

    Producer/consumer split: _on_key_event/_on_mouse_event (the actual hook
    callbacks, called on the keyboard/mouse libraries' own relay threads) are
    pure producers -- capture a raw timestamp/position, enqueue, return.
    _process_queue is the sole consumer, running on its own dedicated worker
    thread, and is the only thing that ever touches self._events/
    self._buttons_down/self._keys_down/self._last_move_t/self._start_time --
    being the only writer/reader of that state means none of it needs a lock.
    """

    def __init__(self):
        self._events = []
        self._start_time = None
        self._hwnd = None
        # A sentinel below any real elapsed time (which starts at ~0.0, see
        # _elapsed) -- otherwise the very first move sample's `t - 0.0` would
        # itself read as under _MOVE_MIN_INTERVAL and get throttled away,
        # silently dropping the first frame of every recording.
        self._last_move_t = -1.0
        # Buttons whose "down" was recorded and hasn't seen its "up" yet.
        # Two jobs (see _process_item's "button" branch): the matching "up"
        # is always recorded once the down was (never bounds-checked again,
        # so a down/up pair can't come apart), AND a fresh "down" for a
        # button ALREADY here means the previous press's up was lost (Roblox
        # hides/locks the cursor during a right-drag; the OS can drop an up
        # under load) -- so we synthesize that missing up before the new
        # down, or the button would replay as held all the way across the
        # gap. That gap is exactly the "held right-click too long, camera
        # kept rotating on its own" report.
        self._buttons_down = set()
        # Keys currently held. A held key auto-repeats -- Windows fires a
        # fresh KEY_DOWN dozens of times a second for as long as it's down --
        # so a "down" for a key already here is that auto-repeat and is
        # dropped, keeping one clean down/up per physical press instead of a
        # flood of duplicate downs (which bloated recordings and, on replay,
        # re-pressed the key over and over).
        self._keys_down = set()
        self._queue = None
        self._worker = None
        self._keyboard_hook = None
        self._mouse_hook = None
        self.active = False

    def start(self, hwnd) -> None:
        if self.active:
            raise RecordingAlreadyActive("An input recording is already in progress.")
        import keyboard as keyboard_lib
        import mouse as mouse_lib
        self._events = []
        self._start_time = None
        self._hwnd = hwnd
        self._last_move_t = -1.0
        self._buttons_down = set()
        self._keys_down = set()
        self._queue = queue.Queue()
        self.active = True
        self._worker = threading.Thread(target=self._process_queue, daemon=True)
        self._worker.start()
        try:
            self._keyboard_hook = keyboard_lib.hook(self._on_key_event)
            self._mouse_hook = mouse_lib.hook(self._on_mouse_event)
        except Exception:
            self.active = False
            self._keyboard_hook = None
            self._mouse_hook = None
            self._queue.put(None)
            self._worker.join(timeout=1.0)
            raise

    # ── Producers: hook callbacks. Minimal work, never blocks. ──────────────

    def _on_key_event(self, event) -> None:
        if not self.active:
            return
        name = getattr(event, "name", None)
        event_type = getattr(event, "event_type", None)
        if not name or event_type not in ("down", "up"):
            return
        self._queue.put(("key", event_type, name, time.perf_counter()))

    def _on_mouse_event(self, event) -> None:
        if not self.active:
            return
        import mouse as mouse_lib
        now = time.perf_counter()
        if isinstance(event, mouse_lib.MoveEvent):
            self._queue.put(("move", event.x, event.y, now))
        elif isinstance(event, mouse_lib.ButtonEvent):
            event_type = event.event_type
            # The `mouse` package's own low-level hook (see its _winmouse.py
            # listen()) reclassifies a "down" as event_type DOUBLE ('double')
            # whenever it lands within the SYSTEM's double-click time
            # (GetDoubleClickTime(), 500ms by default on Windows) of ANY
            # previous button event -- its own from-scratch double-click
            # detection, since a raw system-wide low-level hook never gets
            # the OS's own WM_*BUTTONDBLCLK messages (those only reach a
            # window that opted into the CS_DBLCLKS class style). Reported
            # live: clicking the same button 3 times fast only recorded
            # once, because clicks #2 and #3 arrived as DOUBLE, which wasn't
            # "down" or "up" and got silently dropped below. A recorder has
            # to capture every physical press regardless of how fast they
            # come in -- we don't care about "double-click" as a concept, so
            # DOUBLE is just treated as the "down" it actually was.
            if event_type == mouse_lib.DOUBLE:
                event_type = mouse_lib.DOWN
            if event.button not in _MOUSE_BUTTONS or event_type not in (mouse_lib.DOWN, mouse_lib.UP):
                return
            # Position isn't on a ButtonEvent -- query it NOW, in the hook
            # callback, at the instant the click actually happened. Querying
            # it later on the worker thread would read wherever the cursor
            # has moved TO by the time the queue gets around to this item,
            # not where the click actually landed.
            pos = mouse_lib.get_position()
            self._queue.put(("button", event_type, event.button, pos, now))
        elif isinstance(event, mouse_lib.WheelEvent):
            pos = mouse_lib.get_position()
            self._queue.put(("wheel", event.delta, pos, now))

    # ── Consumer: the one worker thread, off the hook callbacks. ───────────

    def _process_queue(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            self._process_item(item)
            self._queue.task_done()

    def _process_item(self, item) -> None:
        """Handles exactly one queued raw event -- split out of
        _process_queue so every branch's several early-outs (`return` here,
        each one skipping just this one item) can't accidentally skip the
        task_done() call that _flush()/tests' queue.join() depend on."""
        kind = item[0]
        if kind == "key":
            _, event_type, name, now = item
            key = str(name).lower()
            if event_type == "down":
                # Auto-repeat: Windows fires a fresh KEY_DOWN over and over
                # while a key is held. Record only the FIRST -- every repeat
                # while it's still down is dropped, so one held key is one
                # clean down/up pair, not a flood of duplicate downs.
                if key in self._keys_down:
                    return
                self._keys_down.add(key)
                self._events.append({"t": self._elapsed(now), "type": "keydown", "key": key})
            else:  # "up"
                # Only record the up if we recorded its down (mirrors the
                # button branch) -- a key already held when recording STARTED
                # has no recorded down, so its stray up is dropped rather
                # than replayed as a lone key_up.
                if key not in self._keys_down:
                    return
                self._keys_down.discard(key)
                self._events.append({"t": self._elapsed(now), "type": "keyup", "key": key})
        elif kind == "move":
            _, x, y, now = item
            t = self._elapsed(now)
            if t - self._last_move_t < _MOVE_MIN_INTERVAL:
                return
            ref = self._ref_point(x, y)
            if ref is None or not self._in_bounds(*ref):
                return
            self._last_move_t = t
            self._events.append({"t": t, "type": "move", "x": round(ref[0], 1), "y": round(ref[1], 1)})
        elif kind == "button":
            _, event_type, button, pos, now = item
            if event_type == "up":
                # Never bounds-check an "up" independently of its "down":
                # the cursor can read a pixel or two differently a moment
                # later (window mid-animation, a drag that ends right at
                # the edge, plain query timing), and re-checking here is
                # what let a down get recorded while its up was filtered
                # out -- an orphaned down that then replays as that button
                # held for the rest of the replay. If we recorded this
                # button's down, its up is always recorded too,
                # unconditionally.
                if button not in self._buttons_down:
                    return
                self._buttons_down.discard(button)
                self._events.append({"t": self._elapsed(now), "type": "up", "button": button})
                return
            # "down". If this button is ALREADY held, the previous press's
            # up never arrived (dropped by the OS, or swallowed by Roblox's
            # cursor lock during a right-drag). Close it out right here, at
            # this same instant, before starting the new press -- otherwise
            # replay would hold the button from that stale down all the way
            # to now, rotating the camera for the whole gap. This is the
            # core fix for "it just holds right click too long."
            if button in self._buttons_down:
                self._events.append({"t": self._elapsed(now), "type": "up", "button": button})
            ref = self._ref_point(*pos)
            if ref is None or not self._in_bounds(*ref):
                # New press is off-screen (e.g. the Stop Recording button).
                # We may have just healed a stale up above; either way this
                # button is no longer held from replay's point of view.
                self._buttons_down.discard(button)
                return
            self._buttons_down.add(button)
            self._events.append({"t": self._elapsed(now), "type": "down", "button": button})
        elif kind == "wheel":
            _, delta, pos, now = item
            ref = self._ref_point(*pos)
            if ref is None or not self._in_bounds(*ref):
                return
            self._events.append({"t": self._elapsed(now), "type": "scroll", "delta": int(round(delta * 120))})

    def _elapsed(self, now: float) -> float:
        if self._start_time is None:
            self._start_time = now
        return round(now - self._start_time, 3)

    def _in_bounds(self, x: float, y: float) -> bool:
        return -1 <= x <= FIXED_WIN_W + 1 and -1 <= y <= FIXED_WIN_H + 1

    def _ref_point(self, screen_x, screen_y):
        if not self._hwnd or not wm.is_window(self._hwnd):
            return None
        return vision.screen_to_ref(self._hwnd, screen_x, screen_y)

    def stop(self) -> list:
        if not self.active:
            return []
        self.active = False
        import keyboard as keyboard_lib
        import mouse as mouse_lib
        try:
            if self._keyboard_hook is not None:
                keyboard_lib.unhook(self._keyboard_hook)
        except (KeyError, ValueError):
            pass
        try:
            if self._mouse_hook is not None:
                mouse_lib.unhook(self._mouse_hook)
        except (KeyError, ValueError):
            pass
        self._keyboard_hook = None
        self._mouse_hook = None
        # Hooks are gone, so nothing new can be enqueued -- the sentinel is
        # guaranteed to be the last item, and joining the worker guarantees
        # every event already queued (a whole burst that arrived right
        # before Stop was clicked) gets fully processed before we read
        # self._events back out, not left stranded mid-queue.
        self._queue.put(None)
        self._worker.join(timeout=2.0)
        # Close out anything still physically held at the moment Stop was
        # clicked (a mouse button mid-drag, Shift held for a sprint) so the
        # SAVED recording is self-balanced end to end: every down has a
        # matching up in the file itself, not relying on replay_events'
        # finally-cleanup as the only backstop. A dangling down with no up
        # is precisely what replays as a stuck button.
        end_t = self._elapsed(time.perf_counter())
        for button in sorted(self._buttons_down):
            self._events.append({"t": end_t, "type": "up", "button": button})
        self._buttons_down.clear()
        for key in sorted(self._keys_down):
            self._events.append({"t": end_t, "type": "keyup", "key": key})
        self._keys_down.clear()
        events = self._events
        self._events = []
        # A negligible chance of two events from the keyboard and mouse
        # threads landing in the queue slightly out of true chronological
        # order (each carries its own accurate capture-time timestamp, so
        # correctness doesn't depend on queue order -- only the final
        # ordering of the saved list does, which replay_events assumes is
        # sorted). Sorting here is cheap and removes any doubt.
        events.sort(key=lambda ev: ev["t"])
        return events

    def cancel(self) -> None:
        self.stop()


_recorder = _Recorder()


def start_recording(hwnd) -> None:
    _recorder.start(hwnd)


def stop_recording() -> list:
    """Stops the active recording and returns its raw event list without
    saving it -- save_recording() persists it separately so the caller can
    name it first (see main.Api.stop_input_capture/save_pending_recording,
    same split core.paths uses for the same reason: naming happens in a
    dialog AFTER hooks are torn down, so typing the name can't leak into the
    recording as phantom input)."""
    return _recorder.stop()


def cancel_recording() -> None:
    _recorder.cancel()


def is_recording() -> bool:
    return _recorder.active


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 _-]", "", name or "").strip()
    return cleaned or "recording"


def _stored_name(path: str, fallback: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("name") or fallback
    except (OSError, json.JSONDecodeError):
        return fallback


def _resolve(name: str) -> str:
    base = os.path.join(RECORDINGS_DIR, f"{_safe_name(name)}.json")
    if os.path.isfile(base) and _stored_name(base, _safe_name(name)) == name:
        return base
    if os.path.isdir(RECORDINGS_DIR):
        for fname in sorted(os.listdir(RECORDINGS_DIR)):
            if not fname.endswith(".json"):
                continue
            full = os.path.join(RECORDINGS_DIR, fname)
            if _stored_name(full, fname[:-5]) == name:
                return full
    return base


def _free_slug(name: str) -> str:
    slug = _safe_name(name)
    candidate, n = slug, 2
    while True:
        path = os.path.join(RECORDINGS_DIR, f"{candidate}.json")
        if not (os.path.isfile(path) and _stored_name(path, candidate) != name):
            return candidate
        candidate = f"{slug} ({n})"
        n += 1


def list_recordings() -> list:
    if not os.path.isdir(RECORDINGS_DIR):
        return []
    names = set()
    for fname in os.listdir(RECORDINGS_DIR):
        if fname.endswith(".json"):
            names.add(_stored_name(os.path.join(RECORDINGS_DIR, fname), fname[:-5]))
    return sorted(names)


def save_recording(name: str, events: list) -> str:
    name = (name or "").strip() or "recording"
    os.makedirs(RECORDINGS_DIR, exist_ok=True)
    path = os.path.join(RECORDINGS_DIR, f"{_free_slug(name)}.json")
    # compact=True: a dense recording is thousands of small similar objects,
    # not something meant to be hand-edited -- indent=2 (jsonstore's default)
    # would mean pure whitespace is most of the file.
    write_json_atomic(path, {"name": name, "events": events}, compact=True)
    return name


def load_recording(name: str) -> dict:
    try:
        with open(_resolve(name), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"name": name, "events": []}


def delete_recording(name: str) -> bool:
    path = _resolve(name)
    if not os.path.isfile(path):
        return False
    os.remove(path)
    return True


def collect_recordings(names) -> dict:
    """Bundle the given recordings as {name: {"name", "events"}} for
    embedding in a shared macro code -- mirrors core.paths.collect_paths.
    Used for the Share Code path (see main.Api.export_template_code), which
    already zlib-compresses the WHOLE payload with a shared dictionary;
    events stay raw JSON here rather than pre-compressed per-recording (see
    collect_recordings_compressed) since compressing already-compressed,
    base64-inflated data a second time under that outer pass would only
    hurt the ratio, not help it."""
    bundle = {}
    for name in names:
        data = load_recording(name)
        events = data.get("events") or []
        if events:
            bundle[name] = {"name": data.get("name") or name, "events": events}
    return bundle


def _compress_events(events: list) -> str:
    raw = json.dumps(events, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(zlib.compress(raw, 9)).decode("ascii")


def _decompress_events(blob: str) -> list:
    raw = zlib.decompress(base64.b64decode(blob))
    return json.loads(raw.decode("utf-8"))


def collect_recordings_compressed(names) -> dict:
    """Same bundle as collect_recordings, but each recording's events are
    zlib-compressed + base64-encoded ("events_gz") instead of embedded as
    raw JSON. Used for the plain-JSON Task/Template FILE export (see
    main.Api.export_recordings_bundle) -- unlike the Share Code path above,
    that file isn't compressed at all otherwise, so a recording with
    thousands of move events would otherwise dominate the exported file's
    size on its own."""
    bundle = {}
    for name in names:
        data = load_recording(name)
        events = data.get("events") or []
        if events:
            bundle[name] = {"name": data.get("name") or name, "events_gz": _compress_events(events)}
    return bundle


def import_recordings_compressed(bundle: dict) -> int:
    """Reverse of collect_recordings_compressed: decompresses and saves any
    entry whose name isn't already a recording on this machine (same
    skip-if-exists behavior ui/app.js's importCustomPaths already uses for
    walk paths in the same file-export path), returning how many were
    added. Also accepts a plain "events" list for robustness, though nothing
    on this code path writes that shape anymore."""
    existing = set(list_recordings())
    added = 0
    for name, entry in (bundle or {}).items():
        if name in existing or not isinstance(entry, dict):
            continue
        try:
            events = _decompress_events(entry["events_gz"]) if "events_gz" in entry else (entry.get("events") or [])
        except Exception:
            continue
        if not events:
            continue
        saved = save_recording(name, events)
        existing.add(saved)
        added += 1
    return added


def import_recording(name: str, events: list) -> str:
    """Persist a recording bundled with an imported macro/export and return
    the name the block should reference -- mirrors core.paths.import_path,
    including the same identical-content dedup and "(n)" collision handling."""
    name = (name or "").strip() or "recording"
    events = list(events or [])
    candidate, n = name, 2
    while True:
        existing_events = load_recording(candidate).get("events") or []
        if not existing_events:
            return save_recording(candidate, events)
        if existing_events == events:
            return candidate
        candidate = f"{name} ({n})"
        n += 1


def replay_events(events: list, mouse, keyboard, hwnd: int, stop_event: threading.Event = None) -> None:
    """Replays a recorded mouse+keyboard event list through Mouse/Keyboard
    controllers, sleeping between events to reproduce the original timing
    (see core.paths.replay_events for the WASD-only equivalent this mirrors).
    Reference-space mouse coordinates are converted back to real screen
    coordinates against the window rect captured once at the START of this
    replay (not re-fetched per event -- the window doesn't move mid-replay in
    practice, and re-querying it hundreds of times a second for a dense
    recording is pure overhead), so a recording plays back correctly
    regardless of where the docked window sits right now.

    Wrapped in Windows' 1ms multimedia timer resolution (see _winmm above):
    without it, the OS's default ~15.6ms scheduler tick rounds up every short
    sleep between closely-spaced events, making a dense recording replay
    visibly slower and jerkier than what was actually recorded.

    Always releases every mouse button/key this replay pressed on the way
    out (including when stop_event cuts it short), so an interrupted replay
    can't leave a click or key stuck held down in the live game.
    """
    if _winmm is not None:
        _winmm.timeBeginPeriod(1)
    held_buttons = set()
    held_keys = set()
    left, top, sx, sy = vision._window_geometry(hwnd)
    try:
        last_t = 0.0
        for ev in events:
            if stop_event is not None and stop_event.is_set():
                break
            delay = ev.get("t", last_t) - last_t
            if delay > 0:
                if stop_event is not None:
                    if stop_event.wait(delay):
                        break
                else:
                    time.sleep(delay)
            last_t = ev.get("t", last_t)
            etype = ev.get("type")
            if etype == "move":
                mouse.move_to(int(left + ev.get("x", 0) * sx), int(top + ev.get("y", 0) * sy))
            elif etype == "down":
                button = ev.get("button") or "left"
                # Defensive against an unbalanced recording (one made before
                # the recorder healed lost ups, or a hand-edited/imported
                # file): if this button is somehow already held, release it
                # first so two downs in a row can't leave it stuck.
                if button in held_buttons:
                    mouse.up(button)
                mouse.down(button)
                held_buttons.add(button)
            elif etype == "up":
                button = ev.get("button") or "left"
                mouse.up(button)
                held_buttons.discard(button)
            elif etype == "scroll":
                try:
                    mouse.scroll(int(ev.get("delta") or 0))
                except (TypeError, ValueError):
                    pass
            elif etype == "keydown":
                vk = keys.key_name_to_vk(ev.get("key"))
                if vk is not None:
                    keyboard.key_down(vk)
                    held_keys.add(vk)
            elif etype == "keyup":
                vk = keys.key_name_to_vk(ev.get("key"))
                if vk is not None:
                    keyboard.key_up(vk)
                    held_keys.discard(vk)
    finally:
        for button in held_buttons:
            mouse.up(button)
        for vk in held_keys:
            keyboard.key_up(vk)
        if _winmm is not None:
            _winmm.timeEndPeriod(1)
