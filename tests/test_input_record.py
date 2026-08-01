import threading
import time

import mouse as mouse_lib

from core import input_record


class FakeMouse:
    def __init__(self):
        self.events = []

    def move_to(self, x, y):
        self.events.append(("move", x, y))

    def down(self, button="left"):
        self.events.append(("down", button))

    def up(self, button="left"):
        self.events.append(("up", button))

    def scroll(self, amount):
        self.events.append(("scroll", amount))


class FakeKeyboard:
    def __init__(self):
        self.events = []

    def key_down(self, vk):
        self.events.append(("key_down", vk))

    def key_up(self, vk):
        self.events.append(("key_up", vk))


def _isolate(monkeypatch, tmp_path):
    d = tmp_path / "Recordings"
    d.mkdir()
    monkeypatch.setattr(input_record, "RECORDINGS_DIR", str(d))
    return d


def test_replay_events_moves_clicks_and_types(monkeypatch):
    # replay_events reads the window's geometry once up front (not per move
    # event, see its docstring), then applies the offset/scale itself --
    # patch the geometry lookup rather than ref_to_screen to match.
    monkeypatch.setattr(input_record.vision, "_window_geometry", lambda hwnd: (10, 20, 1.0, 1.0))
    mouse = FakeMouse()
    kb = FakeKeyboard()
    events = [
        {"t": 0.0, "type": "move", "x": 5, "y": 6},
        {"t": 0.01, "type": "down", "button": "left"},
        {"t": 0.02, "type": "up", "button": "left"},
        {"t": 0.03, "type": "scroll", "delta": -120},
        {"t": 0.04, "type": "keydown", "key": "w"},
        {"t": 0.05, "type": "keyup", "key": "w"},
    ]

    input_record.replay_events(events, mouse, kb, hwnd=1234)

    assert ("move", 15, 26) in mouse.events
    assert ("down", "left") in mouse.events
    assert ("up", "left") in mouse.events
    assert ("scroll", -120) in mouse.events
    assert ("key_down", ord("W")) in kb.events
    assert ("key_up", ord("W")) in kb.events


def test_replay_events_releases_held_button_and_key_on_interrupt(monkeypatch):
    monkeypatch.setattr(input_record.vision, "_window_geometry", lambda hwnd: (0, 0, 1.0, 1.0))
    mouse = FakeMouse()
    kb = FakeKeyboard()
    stop_event = threading.Event()
    events = [
        {"t": 0.0, "type": "down", "button": "left"},
        {"t": 0.0, "type": "keydown", "key": "w"},
        {"t": 30.0, "type": "up", "button": "left"},
        {"t": 30.0, "type": "keyup", "key": "w"},
    ]

    threading.Timer(0.1, stop_event.set).start()
    started = time.perf_counter()
    input_record.replay_events(events, mouse, kb, hwnd=1, stop_event=stop_event)
    elapsed = time.perf_counter() - started

    assert elapsed < 5.0, f"stop took {elapsed:.1f}s to interrupt a 30s gap"
    assert ("up", "left") in mouse.events
    assert ("key_up", ord("W")) in kb.events


def test_replay_events_skips_unmapped_key_names(monkeypatch):
    monkeypatch.setattr(input_record.vision, "_window_geometry", lambda hwnd: (0, 0, 1.0, 1.0))
    mouse = FakeMouse()
    kb = FakeKeyboard()
    events = [{"t": 0.0, "type": "keydown", "key": "some totally unknown key"}]

    input_record.replay_events(events, mouse, kb, hwnd=1)  # must not raise

    assert kb.events == []


# ── Persistence ──────────────────────────────────────────────────────────

EVENTS = [
    {"t": 0.0, "type": "move", "x": 10, "y": 20},
    {"t": 0.1, "type": "down", "button": "left"},
    {"t": 0.15, "type": "up", "button": "left"},
]


def test_save_and_load_recording_round_trips(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    saved = input_record.save_recording("My Combo", EVENTS)
    assert saved == "My Combo"
    assert input_record.load_recording("My Combo")["events"] == EVENTS
    assert input_record.list_recordings() == ["My Combo"]


def test_save_recording_same_name_overwrites(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    input_record.save_recording("Combo", EVENTS)
    input_record.save_recording("Combo", [])
    assert input_record.load_recording("Combo")["events"] == []


def test_save_recording_different_names_that_slug_the_same_dont_clobber(monkeypatch, tmp_path):
    """"Combo!" and "Combo?" both sanitize to the same "Combo" filename (see
    _safe_name) -- the second save must land in a different file (returning
    the display name is still correct either way; load_recording resolves by
    stored name, with a directory scan as fallback -- see _resolve), so both
    remain independently loadable by their own name afterward."""
    _isolate(monkeypatch, tmp_path)
    input_record.save_recording("Combo!", EVENTS)
    second_events = [{"t": 0.0, "type": "keydown", "key": "e"}]
    second = input_record.save_recording("Combo?", second_events)
    assert second == "Combo?"
    assert input_record.load_recording("Combo!")["events"] == EVENTS
    assert input_record.load_recording("Combo?")["events"] == second_events


def test_collect_recordings_bundles_existing_skips_missing_and_empty(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    input_record.save_recording("Combo A", EVENTS)
    input_record.save_recording("Empty", [])

    bundle = input_record.collect_recordings(["Combo A", "Empty", "Ghost"])
    assert set(bundle) == {"Combo A"}
    assert bundle["Combo A"]["events"] == EVENTS


def test_import_recording_reuses_identical_existing(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    input_record.save_recording("Combo A", EVENTS)

    saved = input_record.import_recording("Combo A", EVENTS)
    assert saved == "Combo A"
    assert input_record.list_recordings() == ["Combo A"]


def test_import_recording_new_name_when_content_differs(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    input_record.save_recording("Combo A", EVENTS)
    different = [{"t": 0.0, "type": "keydown", "key": "e"}]

    saved = input_record.import_recording("Combo A", different)
    assert saved != "Combo A"
    assert input_record.load_recording("Combo A")["events"] == EVENTS
    assert input_record.load_recording(saved)["events"] == different


def test_import_recording_creates_when_absent(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    saved = input_record.import_recording("Fresh Combo", EVENTS)
    assert saved == "Fresh Combo"
    assert input_record.load_recording("Fresh Combo")["events"] == EVENTS


# ── Compressed export bundle (Task/Template file export) ───────────────────

def test_compress_decompress_events_round_trips():
    blob = input_record._compress_events(EVENTS)
    assert isinstance(blob, str)
    assert input_record._decompress_events(blob) == EVENTS


def test_collect_recordings_compressed_bundles_existing_skips_missing_and_empty(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    input_record.save_recording("Combo A", EVENTS)
    input_record.save_recording("Empty", [])

    bundle = input_record.collect_recordings_compressed(["Combo A", "Empty", "Ghost"])
    assert set(bundle) == {"Combo A"}
    assert "events_gz" in bundle["Combo A"]
    assert "events" not in bundle["Combo A"]
    assert input_record._decompress_events(bundle["Combo A"]["events_gz"]) == EVENTS


def test_import_recordings_compressed_round_trips_and_skips_existing(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    input_record.save_recording("Already Here", EVENTS)
    bundle = {
        "Already Here": {"name": "Already Here", "events_gz": input_record._compress_events([{"t": 0.0, "type": "keydown", "key": "z"}])},
        "New One": {"name": "New One", "events_gz": input_record._compress_events(EVENTS)},
    }

    added = input_record.import_recordings_compressed(bundle)

    assert added == 1
    # The existing recording under this name was left alone, not overwritten
    # by the bundle's (different) events.
    assert input_record.load_recording("Already Here")["events"] == EVENTS
    assert input_record.load_recording("New One")["events"] == EVENTS


def test_import_recordings_compressed_skips_corrupt_entries(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    bundle = {"Broken": {"name": "Broken", "events_gz": "not valid base64/zlib"}}

    added = input_record.import_recordings_compressed(bundle)

    assert added == 0
    assert input_record.list_recordings() == []


# ── Recording: reference-space conversion + the drop-outside-the-window ────
# filter (see _Recorder._in_bounds' docstring for why this matters -- it's
# also what keeps a click on the Record block's own "Stop Recording" button
# out of the saved recording, since that button deliberately sits outside
# the docked game window).

def _rec_with_fake_window(monkeypatch):
    rec = input_record._Recorder()
    rec._hwnd = 999
    rec.active = True
    # The hook callbacks only enqueue (see _Recorder's producer/consumer
    # split) -- tests drain the queue synchronously afterward via _drain()
    # instead of spinning up the real worker thread, so a queue must exist
    # here the way start() would normally create one.
    rec._queue = input_record.queue.Queue()
    monkeypatch.setattr(input_record.wm, "is_window", lambda hwnd: True)
    # A trivial 1:1 mapping with a (100, 50) screen origin, so ref-space
    # coordinates are easy to reason about in assertions below.
    monkeypatch.setattr(input_record.vision, "screen_to_ref", lambda hwnd, x, y: (x - 100, y - 50))
    return rec


def _drain(rec):
    """Synchronously processes every item currently queued -- the test-side
    stand-in for the real background worker thread (_process_queue)."""
    while not rec._queue.empty():
        rec._process_item(rec._queue.get())


def test_mouse_move_inside_window_is_recorded_in_ref_space(monkeypatch):
    rec = _rec_with_fake_window(monkeypatch)
    rec._on_mouse_event(mouse_lib.MoveEvent(x=150, y=90, time=0))
    _drain(rec)

    assert len(rec._events) == 1
    ev = rec._events[0]
    assert ev["type"] == "move"
    assert ev["x"] == 50
    assert ev["y"] == 40


def test_mouse_move_outside_window_bounds_is_dropped(monkeypatch):
    """This is what keeps a click on the Record block's own Stop Recording
    popout (deliberately positioned outside the game's rect) from leaking
    into the saved recording."""
    rec = _rec_with_fake_window(monkeypatch)
    # 2000, 2000 screen -> way outside the 1152x756 reference window.
    rec._on_mouse_event(mouse_lib.MoveEvent(x=2000, y=2000, time=0))
    _drain(rec)

    assert rec._events == []


def test_mouse_button_event_outside_window_bounds_is_dropped(monkeypatch):
    rec = _rec_with_fake_window(monkeypatch)
    monkeypatch.setattr(mouse_lib, "get_position", lambda: (2000, 2000))
    rec._on_mouse_event(mouse_lib.ButtonEvent(event_type="down", button="left", time=0))
    _drain(rec)

    assert rec._events == []


def test_mouse_click_inside_window_is_recorded(monkeypatch):
    rec = _rec_with_fake_window(monkeypatch)
    monkeypatch.setattr(mouse_lib, "get_position", lambda: (150, 90))
    rec._on_mouse_event(mouse_lib.ButtonEvent(event_type="down", button="left", time=0))
    _drain(rec)

    assert len(rec._events) == 1
    assert rec._events[0]["type"] == "down"
    assert rec._events[0]["button"] == "left"


def test_button_up_is_recorded_even_if_cursor_reads_out_of_bounds_by_then(monkeypatch):
    """Regression test: reported live as a right-click that stayed held down
    while the recording replayed. The cursor position at "up" time used to
    be bounds-checked independently of "down" -- a click that ends right at
    the docked window's edge (or just unlucky query timing) could pass the
    down's check but fail the up's, orphaning a down with no matching up.
    That replays as the button held for the rest of the replay. The up must
    always be recorded once its down was, regardless of where the cursor
    reads a moment later."""
    rec = _rec_with_fake_window(monkeypatch)
    monkeypatch.setattr(mouse_lib, "get_position", lambda: (150, 90))  # inside the window
    rec._on_mouse_event(mouse_lib.ButtonEvent(event_type="down", button="right", time=0))

    monkeypatch.setattr(mouse_lib, "get_position", lambda: (2000, 2000))  # now reads outside
    rec._on_mouse_event(mouse_lib.ButtonEvent(event_type="up", button="right", time=0))
    _drain(rec)

    assert [e["type"] for e in rec._events] == ["down", "up"]
    assert rec._buttons_down == set()


def test_button_up_without_a_recorded_down_is_dropped(monkeypatch):
    """The down that started this click was itself filtered out (e.g. it
    landed on the Record block's own Stop Recording popout) -- its up must
    not appear on its own either, or replay would send a bare "up" for a
    button that was never pressed."""
    rec = _rec_with_fake_window(monkeypatch)
    monkeypatch.setattr(mouse_lib, "get_position", lambda: (2000, 2000))
    rec._on_mouse_event(mouse_lib.ButtonEvent(event_type="down", button="left", time=0))
    rec._on_mouse_event(mouse_lib.ButtonEvent(event_type="up", button="left", time=0))
    _drain(rec)

    assert rec._events == []


def test_key_event_is_recorded_regardless_of_mouse_position(monkeypatch):
    """Keyboard events have no on-screen position, so unlike mouse events
    they are never bounds-filtered."""
    rec = _rec_with_fake_window(monkeypatch)

    class FakeKeyEvent:
        name = "w"
        event_type = "down"

    rec._on_key_event(FakeKeyEvent())
    _drain(rec)

    assert len(rec._events) == 1
    assert rec._events[0] == {"t": rec._events[0]["t"], "type": "keydown", "key": "w"}


class _FakeKeyEvent:
    def __init__(self, name, event_type="down"):
        self.name = name
        self.event_type = event_type


def test_rapid_burst_of_clicks_and_keys_are_all_captured(monkeypatch):
    """The whole point of the producer/consumer split: hook callbacks only
    enqueue, so a burst of many clicks/keys arriving faster than they can be
    fully processed (coordinate lookups, bounds checks) still all land in
    the queue instead of some being dropped by a slow synchronous callback.
    Each iteration is a full press (down + up) of a distinct key so this
    isn't confused with the auto-repeat suppression covered separately."""
    rec = _rec_with_fake_window(monkeypatch)
    monkeypatch.setattr(mouse_lib, "get_position", lambda: (150, 90))

    for i in range(50):
        rec._on_mouse_event(mouse_lib.ButtonEvent(event_type="down", button="left", time=0))
        rec._on_mouse_event(mouse_lib.ButtonEvent(event_type="up", button="left", time=0))
        rec._on_key_event(_FakeKeyEvent(str(i), "down"))
        rec._on_key_event(_FakeKeyEvent(str(i), "up"))
    _drain(rec)

    downs = [e for e in rec._events if e["type"] == "down"]
    ups = [e for e in rec._events if e["type"] == "up"]
    keydowns = [e for e in rec._events if e["type"] == "keydown"]
    keyups = [e for e in rec._events if e["type"] == "keyup"]
    assert len(downs) == 50
    assert len(ups) == 50
    assert len(keydowns) == 50
    assert len(keyups) == 50


def test_held_key_autorepeat_records_one_downup_not_a_flood(monkeypatch):
    """A held key auto-repeats -- Windows fires a fresh KEY_DOWN dozens of
    times a second. Only the first is recorded; every repeat while it's
    still down is dropped, so one physical hold is one clean down/up pair."""
    rec = _rec_with_fake_window(monkeypatch)

    for _ in range(30):  # 30 auto-repeat downs for a single held key
        rec._on_key_event(_FakeKeyEvent("w", "down"))
    rec._on_key_event(_FakeKeyEvent("w", "up"))
    _drain(rec)

    assert [e["type"] for e in rec._events] == ["keydown", "keyup"]
    assert rec._keys_down == set()


def test_key_up_without_a_recorded_down_is_dropped(monkeypatch):
    """A key held BEFORE recording started has no recorded down, so its
    stray up must be dropped rather than replayed as a lone key_up."""
    rec = _rec_with_fake_window(monkeypatch)
    rec._on_key_event(_FakeKeyEvent("shift", "up"))
    _drain(rec)
    assert rec._events == []


def test_rapid_clicks_reclassified_as_double_by_the_mouse_library_still_all_record(monkeypatch):
    """Regression test: reported live as holding Shift and right-clicking 3
    times fast only recording once. Root cause is in the `mouse` package
    itself (see _winmouse.py's listen()): a low-level system-wide hook never
    gets Windows' own WM_*BUTTONDBLCLK messages (those only reach a window
    that opted into CS_DBLCLKS), so the library does its own from-scratch
    double-click detection -- any "down" arriving within GetDoubleClickTime()
    (500ms default) of ANY previous button event gets its event_type
    rewritten to DOUBLE ('double') instead of DOWN. That isn't "down" or
    "up", so it used to be silently dropped -- clicks #2 and #3 of a fast
    triple-click vanished, along with their now-orphaned "up"s. A recorder
    needs every physical press regardless of speed, so DOUBLE must be
    treated as the down it actually was."""
    rec = _rec_with_fake_window(monkeypatch)
    monkeypatch.setattr(mouse_lib, "get_position", lambda: (150, 90))

    # Click 1: a normal down/up. Clicks 2 and 3: the library's own reported
    # shape for two fast repeats of the same button -- "double" downs.
    rec._on_mouse_event(mouse_lib.ButtonEvent(event_type="down", button="right", time=0))
    rec._on_mouse_event(mouse_lib.ButtonEvent(event_type="up", button="right", time=0.05))
    rec._on_mouse_event(mouse_lib.ButtonEvent(event_type="double", button="right", time=0.10))
    rec._on_mouse_event(mouse_lib.ButtonEvent(event_type="up", button="right", time=0.15))
    rec._on_mouse_event(mouse_lib.ButtonEvent(event_type="double", button="right", time=0.20))
    rec._on_mouse_event(mouse_lib.ButtonEvent(event_type="up", button="right", time=0.25))
    _drain(rec)

    downs = [e for e in rec._events if e["type"] == "down"]
    ups = [e for e in rec._events if e["type"] == "up"]
    assert len(downs) == 3, "one or more of the 3 clicks was dropped"
    assert len(ups) == 3
    assert rec._buttons_down == set()


def test_lost_button_up_is_healed_before_the_next_down(monkeypatch):
    """The core "held right-click too long" fix. Roblox hides/locks the
    cursor during a right-drag and the OS can drop an "up" under load. When
    a fresh "down" arrives for a button that's still marked held, the
    missing up is synthesized right before the new down -- otherwise replay
    would hold the button across the whole gap and rotate the camera on its
    own."""
    rec = _rec_with_fake_window(monkeypatch)
    monkeypatch.setattr(mouse_lib, "get_position", lambda: (150, 90))

    # down at t=0, its up is LOST, then a new down at t=3 (a big gap), its up.
    # Driving _process_item directly gives precise control over each t.
    rec._process_item(("button", "down", "right", (150, 90), 0.0))
    rec._process_item(("button", "down", "right", (150, 90), 3.0))
    rec._process_item(("button", "up", "right", (150, 90), 3.1))

    types = [(e["type"], e["t"]) for e in rec._events]
    # The healed up lands at the NEW down's time (3.0), not stretched from 0.
    assert types == [("down", 0.0), ("up", 3.0), ("down", 3.0), ("up", 3.1)]
    assert rec._buttons_down == set()


def test_stop_finalizes_still_held_button_and_key(monkeypatch):
    """Anything physically held when Stop is clicked (a button mid-drag,
    Shift held for a sprint) is closed out so the saved recording is
    self-balanced -- no dangling down that replays as a stuck input."""
    rec = input_record._Recorder()
    rec._hwnd = 999  # truthy so _ref_point does the (patched) conversion
    rec.active = True
    rec._queue = input_record.queue.Queue()
    rec._worker = threading.Thread(target=rec._process_queue, daemon=True)
    rec._worker.start()

    monkeypatch.setattr(input_record.wm, "is_window", lambda hwnd: True)
    monkeypatch.setattr(input_record.vision, "screen_to_ref", lambda hwnd, x, y: (x, y))

    rec._queue.put(("key", "down", "shift", 0.010))
    rec._queue.put(("button", "down", "right", (150, 90), 0.020))
    # No ups enqueued -- both are still held at Stop.

    events = rec.stop()

    types = [e["type"] for e in events]
    assert types.count("keydown") == 1 and types.count("keyup") == 1
    assert types.count("down") == 1 and types.count("up") == 1
    assert rec._keys_down == set() and rec._buttons_down == set()


def test_stop_joins_the_worker_and_returns_sorted_events(monkeypatch):
    """stop() must wait for the real background worker to finish draining
    the queue (a burst that arrived right before Stop was clicked must not
    be left stranded mid-queue), and sort by "t" -- two producer threads
    (keyboard's hook thread, mouse's) can enqueue their own accurately-
    timestamped events in a slightly different order than true time."""
    rec = input_record._Recorder()
    rec._hwnd = None  # no window lookups needed -- only "key" events below
    rec.active = True
    rec._queue = input_record.queue.Queue()
    rec._worker = threading.Thread(target=rec._process_queue, daemon=True)
    rec._worker.start()

    # Deliberately out of chronological order, as if two different producer
    # threads' events interleaved in the queue slightly differently than
    # their real capture times. Each is a full down/up so nothing is left
    # held for stop() to finalize.
    rec._queue.put(("key", "down", "b", 0.020))
    rec._queue.put(("key", "up", "b", 0.025))
    rec._queue.put(("key", "down", "a", 0.010))
    rec._queue.put(("key", "up", "a", 0.015))
    rec._queue.put(("key", "down", "c", 0.030))
    rec._queue.put(("key", "up", "c", 0.035))

    events = rec.stop()

    assert [e["key"] for e in events] == ["a", "a", "b", "b", "c", "c"]
    assert [e["type"] for e in events] == [
        "keydown", "keyup", "keydown", "keyup", "keydown", "keyup"]
    assert rec.active is False
