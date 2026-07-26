import threading
import time

from core import paths


class FakeKeyboard:
    """Records what replay_events would send to the game."""

    def __init__(self):
        self.events = []

    def key_down(self, vk):
        self.events.append(("key_down", vk))

    def key_up(self, vk):
        self.events.append(("key_up", vk))

    def move_key_down(self, name):
        self.events.append(("down", name))

    def move_key_up(self, name):
        self.events.append(("up", name))


def test_replay_events_replays_in_order():
    kb = FakeKeyboard()
    events = [
        {"t": 0.0, "key": "w", "state": "down"},
        {"t": 0.05, "key": "w", "state": "up"},
    ]
    paths.replay_events(events, kb)

    assert ("down", "w") in kb.events
    assert ("up", "w") in kb.events
    # Every watched key is released on the way out, whatever was pressed.
    released = [name for kind, name in kb.events if kind == "up"]
    for name in paths._WATCHED_KEYS:
        assert name in released


def test_replay_events_stop_interrupts_a_long_gap():
    """Stop must cut in DURING the wait between two key events, not only at
    the next one. A recorded path can sit still for seconds (walking to a
    spot), and a plain time.sleep() blocks the stop for that whole gap."""
    kb = FakeKeyboard()
    stop_event = threading.Event()
    # A 30s gap: if the stop is honoured only between events, this test would
    # sit here for 30 seconds instead of returning promptly.
    events = [
        {"t": 0.0, "key": "w", "state": "down"},
        {"t": 30.0, "key": "w", "state": "up"},
    ]

    threading.Timer(0.1, stop_event.set).start()
    started = time.perf_counter()
    paths.replay_events(events, kb, stop_event=stop_event)
    elapsed = time.perf_counter() - started

    assert elapsed < 5.0, f"stop took {elapsed:.1f}s to interrupt a 30s gap"
    # The held direction is still released even though the replay was cut short.
    assert ("up", "w") in kb.events


def test_replay_events_releases_sprint_on_interrupt():
    from core import keys

    kb = FakeKeyboard()
    stop_event = threading.Event()
    events = [
        {"t": 0.0, "key": "w", "state": "down"},
        {"t": 30.0, "key": "w", "state": "up"},
    ]

    threading.Timer(0.1, stop_event.set).start()
    paths.replay_events(events, kb, stop_event=stop_event, sprint=True)

    assert ("key_down", keys.VK_SHIFT) in kb.events
    assert ("key_up", keys.VK_SHIFT) in kb.events
