import threading

import core.runner as runner_module
from core.runner import MacroRunner
from core import runner_constants as rc


def test_event_act_images_and_order_stay_in_sync():
    """_reach_event_act_selected looks an act up in EVENT_ACT_IMAGES, then
    indexes EVENT_ACT_ORDER to decide whether the card needs scrolling to. An
    act in one but not the other raises ValueError mid-navigation, so the two
    have to be edited together -- this fails the moment they drift, which is
    exactly what adding Act 4 to one and forgetting the other looks like."""
    assert set(rc.EVENT_ACT_IMAGES) == set(rc.EVENT_ACT_ORDER)


def test_event_act_scroll_index_is_within_the_order():
    assert 0 <= rc.EVENT_ACT_SCROLL_FROM_INDEX <= len(rc.EVENT_ACT_ORDER)


def test_every_event_act_has_at_least_one_candidate_crop():
    for act, images in rc.EVENT_ACT_IMAGES.items():
        candidates = (images,) if isinstance(images, str) else images
        assert candidates, f"Act {act} has no reference crop names"
        assert all(isinstance(n, str) and n for n in candidates), f"Act {act} has a bad crop name"

def test_reach_event_act_selected_clicks_villian_invasion_between_event_and_gamemode(monkeypatch):
    runner = object.__new__(MacroRunner)
    events = []

    runner._mouse = type("Mouse", (), {"click": lambda self, x, y: events.append(("coord", x, y))})()
    runner._ensure_lobby = lambda hwnd, stop_event: True
    runner._checkpoint = lambda stop_event: False
    runner._set_status = lambda **kwargs: None
    runner._log = lambda message: None
    runner._cxy = lambda name: (10, 20)
    runner._spam_back_until_gone = lambda hwnd, stop_event: events.append(("back",))

    def click_found_image(hwnd, image_name, timeout, stop_event):
        events.append(("image", image_name))
        return {"score": 0.99}

    runner._click_found_image = click_found_image

    monkeypatch.setattr(runner_module.vision, "wait_for_image", lambda *args, **kwargs: {"score": 0.98})
    monkeypatch.setattr(runner_module.wm, "get_window_rect_screen", lambda hwnd: (0, 0, 0, 0))
    monkeypatch.setattr(runner_module.time, "sleep", lambda seconds: None)

    assert runner._reach_event_act_selected(hwnd=123, stop_event=threading.Event(), act="1") is True
    assert [event[1] for event in events if event[0] == "image"] == [
        "nav_event", "Villain_Invasion", "event_gamemode", "villian1"
    ]
    assert events == [
        ("image", "nav_event"),
        ("image", "Villain_Invasion"),
        ("image", "event_gamemode"),
        ("image", "villian1"),
    ]


def test_reach_event_act_selected_backs_out_when_villain_invasion_missing(monkeypatch):
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

    assert runner._reach_event_act_selected(hwnd=456, stop_event=threading.Event(), act="1") is False
    assert clicked == ["nav_event", "Villain_Invasion"]
    assert backs == [456]
