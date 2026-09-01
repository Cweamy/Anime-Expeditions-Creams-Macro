"""Portal picker: search Summer, click the tier card, confirm -- the extra
step that makes the Portal event kind specialized. Used both on entry (after
the Portal kind card) and post-victory (after the Victory screen's Select
Portal button)."""

import threading

import core.runner as runner_module
from core.runner import MacroRunner


def _runner():
    runner = object.__new__(MacroRunner)
    runner.clicked = []
    runner.backs = []
    runner.logged = []
    runner.typed = []
    runner._checkpoint = lambda stop_event: False
    runner._set_status = lambda **kw: None
    runner._log = lambda message: runner.logged.append(message)
    runner._spam_back_until_gone = lambda hwnd, stop_event: runner.backs.append(hwnd)
    kb = type("Kb", (), {})()
    kb.combo = lambda *a, **k: runner.clicked.append(("combo", a))
    kb.tap = lambda vk, **k: runner.clicked.append(("tap", vk))
    kb.type_text = lambda text, **k: runner.typed.append(text)
    runner._keyboard = kb
    runner._click_found_image = (
        lambda hwnd, name, timeout, stop_event: runner.clicked.append(("image", name)) or {"score": 0.99})
    return runner


def test_select_summer_portal_entry_skips_select_new_portal(monkeypatch):
    runner = _runner()
    monkeypatch.setattr(runner_module.time, "sleep", lambda s: None)
    assert runner._select_summer_portal(hwnd=1, stop_event=threading.Event(), entry=True) is True
    images = [call[1] for call in runner.clicked if call[0] == "image"]
    assert images == ["portal_search", "summer_portal", "portal_activate"]
    assert "select_new_portal" not in images
    assert runner.typed == ["summer"]


def test_select_summer_portal_post_victory_clicks_select_new_portal_first(monkeypatch):
    runner = _runner()
    monkeypatch.setattr(runner_module.time, "sleep", lambda s: None)
    assert runner._select_summer_portal(hwnd=1, stop_event=threading.Event(), entry=False) is True
    images = [call[1] for call in runner.clicked if call[0] == "image"]
    assert images == ["select_new_portal", "portal_search", "summer_portal", "portal_activate"]
    assert runner.typed == ["summer"]


def test_select_summer_portal_bad_start_tier_card_clears_and_types(monkeypatch):
    runner = _runner()
    monkeypatch.setattr(runner_module.time, "sleep", lambda s: None)
    runner._select_summer_portal(hwnd=1, stop_event=threading.Event(), entry=True)
    assert ("combo", (runner_module.keys.VK_CONTROL, ord("A"))) in runner.clicked  # noqa: E721
    assert any(call[0] == "tap" for call in runner.clicked)


def test_select_summer_portal_backs_out_when_tier_card_missing(monkeypatch):
    runner = _runner()
    monkeypatch.setattr(runner_module.time, "sleep", lambda s: None)
    runner._click_found_image = (
        lambda hwnd, name, timeout, stop_event: runner.clicked.append(("image", name)) or (
            None if name == "summer_portal" else {"score": 0.99}))
    assert runner._select_summer_portal(hwnd=1, stop_event=threading.Event(), entry=True) is False
    assert runner.backs == [1]


def test_select_summer_portal_backs_out_when_confirm_missing(monkeypatch):
    runner = _runner()
    monkeypatch.setattr(runner_module.time, "sleep", lambda s: None)
    runner._click_found_image = (
        lambda hwnd, name, timeout, stop_event: runner.clicked.append(("image", name)) or (
            None if name == "portal_activate" else {"score": 0.99}))
    assert runner._select_summer_portal(hwnd=1, stop_event=threading.Event(), entry=False) is False
    assert runner.backs == [1]


def _enter_stage_runner(calls):
    runner = object.__new__(MacroRunner)
    runner._checkpoint = lambda stop_event: False
    runner._set_status = lambda **kw: None
    runner._log = lambda message: None
    runner._click_and_verify_gone = lambda *a, **k: (calls.append(("confirm", a)) or True)
    runner._click_start_and_wait_teleport = lambda *a, **k: (calls.append(("start", a)) or True)
    runner._click_enter_matchmaking = lambda *a, **k: True
    runner._wait_teleport_in = lambda *a, **k: True
    return runner


def test_enter_selected_stage_portal_leaps_straight_to_start():
    """Portal's activate step already landed on the Start screen -- no
    nav_select_stage confirm; the solo tail clicks nav_start directly."""
    calls = []
    runner = _enter_stage_runner(calls)
    task = {"play_mode": "solo", "mode": "event", "stage": "portal"}
    assert runner._enter_selected_stage(
        hwnd=1, stop_event=threading.Event(), task=task, mode="event", coords={}, webhook={}) is True
    assert not any(c[0] == "confirm" for c in calls)
    assert any(c[0] == "start" for c in calls)


def test_enter_selected_stage_story_still_clicks_select_stage_confirm():
    """Non-portal modes still press the nav_select_stage confirm before Start."""
    calls = []
    runner = _enter_stage_runner(calls)
    task = {"play_mode": "solo", "mode": "story", "stage": "1"}
    assert runner._enter_selected_stage(
        hwnd=1, stop_event=threading.Event(), task=task, mode="story", coords={}, webhook={}) is True
    assert any(c[0] == "confirm" for c in calls)
    assert any(c[0] == "start" for c in calls)
