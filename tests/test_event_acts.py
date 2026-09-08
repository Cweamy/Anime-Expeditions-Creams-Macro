import threading

import core.runner as runner_module
from core.runner import MacroRunner
from core import runner_constants as rc


def test_event_kind_images_and_order_stay_in_sync():
    """_reach_event_kind_selected looks a kind up in EVENT_KIND_IMAGES, then
    validates it against EVENT_KIND_ORDER. A kind in one but not the other
    would fail mid-navigation, so the two have to be edited together -- this
    fails the moment they drift, which is exactly what adding Portal Mode to
    one and forgetting the other looks like."""
    assert set(rc.EVENT_KIND_IMAGES) == set(rc.EVENT_KIND_ORDER)


def test_every_event_kind_has_at_least_one_candidate_crop():
    for kind, images in rc.EVENT_KIND_IMAGES.items():
        candidates = (images,) if isinstance(images, str) else images
        assert candidates, f"Event kind {kind} has no reference crop names"
        assert all(isinstance(n, str) and n for n in candidates), f"Event kind {kind} has a bad crop name"


def test_reach_event_act_selected_clicks_summer_flow_for_infinite(monkeypatch):
    """The Summer event flow is nav_event -> summer_nav -> the event
    gamemode card -> the chosen kind's card (Infinite & Fishing). The kind
    decision is delegated to _reach_event_kind_selected, so this just
    asserts the images leading up to it and the final kind card."""
    runner = object.__new__(MacroRunner)
    events = []

    runner._mouse = type("Mouse", (), {"click": lambda self, x, y: events.append(("coord", x, y))})()
    runner._ensure_lobby = lambda hwnd, stop_event: True
    runner._checkpoint = lambda stop_event: False
    runner._set_status = lambda **kwargs: None
    runner._log = lambda message: None
    runner._spam_back_until_gone = lambda hwnd, stop_event: events.append(("back",))

    def click_found_image(hwnd, image_name, timeout, stop_event):
        events.append(("image", image_name))
        return {"score": 0.99}

    runner._click_found_image = click_found_image
    monkeypatch.setattr(runner_module.time, "sleep", lambda seconds: None)

    assert runner._reach_event_act_selected(hwnd=123, stop_event=threading.Event(), act="infinite") is True
    assert [event[1] for event in events if event[0] == "image"] == [
        "nav_event", "summer_nav", "summer_event_gamemode", "summer_event_infinite"
    ]


def test_reach_event_act_selected_backs_out_when_summer_nav_missing(monkeypatch):
    """If the Summer event's lobby entry never shows up, the whole unit backs
    out to the lobby so the retry loop starts clean -- same recovery the other
    nav paths use."""
    runner = object.__new__(MacroRunner)
    clicked = []
    backs = []

    runner._ensure_lobby = lambda hwnd, stop_event: True
    runner._checkpoint = lambda stop_event: False
    runner._set_status = lambda **kwargs: None
    runner._log = lambda message: None
    runner._spam_back_until_gone = lambda hwnd, stop_event: backs.append(hwnd)

    def click_found_image(hwnd, image_name, timeout, stop_event):
        clicked.append(image_name)
        return {"score": 0.99} if image_name == "nav_event" else None

    runner._click_found_image = click_found_image
    monkeypatch.setattr(runner_module.time, "sleep", lambda seconds: None)

    assert runner._reach_event_act_selected(hwnd=456, stop_event=threading.Event(), act="infinite") is False
    assert clicked == ["nav_event", "summer_nav"]
    assert backs == [456]


def test_reach_event_act_selected_rejects_unknown_kind():
    """A bad kind (e.g. a leftover villain-Act task field like "1") fails
    cleanly with a log line instead of navigating partway."""
    runner = object.__new__(MacroRunner)
    logged = []
    runner._log = lambda message: logged.append(message)
    runner._set_status = lambda **kwargs: None

    assert runner._reach_event_act_selected(hwnd=999, stop_event=threading.Event(), act="1") is False
    assert any("Unknown Event kind" in message for message in logged)


def test_reach_event_kind_selected_unknown_kind_backs_out():
    runner = object.__new__(MacroRunner)
    backs = []
    runner._set_status = lambda **kwargs: None
    runner._log = lambda message: None
    runner._spam_back_until_gone = lambda hwnd, stop_event: backs.append(hwnd)
    runner._click_found_image = lambda hwnd, image_name, timeout, stop_event: None

    assert runner._reach_event_kind_selected(hwnd=789, stop_event=threading.Event(), kind="bogus") is False
    assert backs == [789]


def test_reach_event_kind_selected_portal_not_implemented_fails_cleanly():
    """Portal Mode points at a template that doesn't exist yet, so picking it
    must fail cleanly (back out to lobby) rather than raise -- the runnable
    kind is Infinite & Fishing until summer_event_portal is added."""
    runner = object.__new__(MacroRunner)
    clicked = []
    backs = []
    runner._set_status = lambda **kwargs: None
    runner._log = lambda message: None
    runner._checkpoint = lambda stop_event: False
    runner._spam_back_until_gone = lambda hwnd, stop_event: backs.append(hwnd)
    runner._click_found_image = (
        lambda hwnd, image_name, timeout, stop_event: clicked.append(image_name) or None)

    assert runner._reach_event_kind_selected(hwnd=1234, stop_event=threading.Event(), kind="portal") is False
    assert clicked == ["summer_event_portal"]
    assert backs == [1234]
