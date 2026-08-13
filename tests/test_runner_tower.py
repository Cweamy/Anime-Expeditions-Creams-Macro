import threading

from core import runner
from core import vision
from core.runner import MacroRunner


class TowerProbe:
    def __init__(self):
        self.clicked = []
        self.logs = []
        self.statuses = []
        self.spam_back_calls = 0
        self.ensure_lobby_calls = 0
        self.click_play_calls = 0

    def _log(self, message):
        self.logs.append(message)

    def _set_status(self, **kwargs):
        self.statuses.append(kwargs)

    def _checkpoint(self, _stop):
        return False

    def _spam_back_until_gone(self, *_args):
        self.spam_back_calls += 1
        return True

    def _ensure_lobby(self, *_args):
        self.ensure_lobby_calls += 1
        return True

    def _click_play(self, *_args):
        self.click_play_calls += 1
        return True

    def _click_found_image(self, _hwnd, name, timeout, _stop, *args, **kwargs):
        self.clicked.append((name, timeout))
        return {"score": 0.99}


def test_reach_tower_traitless_clicks_tower_then_traitless(monkeypatch):
    probe = TowerProbe()
    monkeypatch.setattr(vision, "find_image", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)

    assert MacroRunner._reach_tower_selected(
        probe, 123, threading.Event(), {"mode": "tower", "tower_mode": "traitless"}
    ) is True

    assert [name for name, _timeout in probe.clicked] == ["nav_tower", "Traitless_Tower", "Floor"]
    assert probe.ensure_lobby_calls == 1
    assert probe.click_play_calls == 1
    assert any("Traitless Tower selected" in message for message in probe.logs)


def test_reach_tower_normal_clicks_tower_only(monkeypatch):
    probe = TowerProbe()
    monkeypatch.setattr(vision, "find_image", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)

    assert MacroRunner._reach_tower_selected(
        probe, 123, threading.Event(), {"mode": "tower", "tower_mode": "normal"}
    ) is True

    assert [name for name, _timeout in probe.clicked] == ["nav_tower", "normal_tower", "Floor"]
    assert any("Normal Tower" in message for message in probe.logs)


class MouseProbe:
    def __init__(self):
        self.moves = []

    def move_to(self, x, y):
        self.moves.append((x, y))


class MatchResultProbe:
    def __init__(self):
        self.logs = []
        self.statuses = []
        self.clicked_verify = []
        self.wait_gone = []
        self._mouse = MouseProbe()
        self._coords = {"unit_info_reset_x": 11, "unit_info_reset_y": 22}
        self._act4_wants_in = False

    def _log(self, message):
        self.logs.append(message)

    def _note_win_for_crafting(self, *_args):
        return None

    def _release_quick_place_shift(self):
        return None

    def _set_status(self, **kwargs):
        self.statuses.append(kwargs)

    def _capture_result_screenshot(self, _hwnd):
        return None

    def _finish_match_result_background(self, *_args):
        return None

    def _dismiss_reward_card_if_found(self, _hwnd):
        return False

    def _clear_result_obtainment_modal(self, *_args):
        return True

    def _click_and_verify_gone(self, _hwnd, _stop, image, timeout, **_kwargs):
        self.clicked_verify.append((image, timeout))
        return True

    def _wait_for_image_gone(self, _hwnd, names, timeout, _stop):
        self.wait_gone.append((names, timeout))
        return True

    def _click_return_to_lobby_if_found(self, *_args):
        return True

    def _relic_dropped(self, _hwnd):
        return False


class ImmediateThread:
    def __init__(self, target, args=(), daemon=None, **_kwargs):
        self.target = target
        self.args = args
        self.daemon = daemon

    def start(self):
        self.target(*self.args)


def test_handle_match_result_uses_tower_floor_buttons_and_story_repeat_stage(monkeypatch):
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(runner.wm, "get_window_rect_screen", lambda _hwnd: (100, 200, 300, 400))
    monkeypatch.setattr(runner.threading, "Thread", ImmediateThread)

    tower_win_probe = MatchResultProbe()
    assert MacroRunner._handle_match_result(
        tower_win_probe, 123, threading.Event(), {"mode": "tower", "map": "Tower"},
        "win", "1m 2s", {}, repeat=True
    ) is True

    tower_defeat_probe = MatchResultProbe()
    assert MacroRunner._handle_match_result(
        tower_defeat_probe, 123, threading.Event(), {"mode": "tower", "map": "Tower"},
        "loss", "1m 2s", {}, repeat=True
    ) is True

    story_probe = MatchResultProbe()
    assert MacroRunner._handle_match_result(
        story_probe, 123, threading.Event(), {"mode": "story", "map": "Rose Kingdom"},
        "win", "1m 2s", {}, repeat=True
    ) is True

    assert tower_win_probe.clicked_verify[0][0] == "Next_Floor"
    assert tower_defeat_probe.clicked_verify[0][0] == "Repeat_Floor"
    assert story_probe.clicked_verify[0][0] == "repeat_stage"
