import threading
from unittest.mock import Mock

from core.runner import MacroRunner
from core.runner_crafting import CraftingOps


class DummyKeyboard:
    def __init__(self):
        self.taps = []
        self.typed = []

    def tap(self, key_code):
        self.taps.append(key_code)

    def combo(self, *keys):
        pass

    def type_text(self, text):
        self.typed.append(text)


class DummyMouse:
    def __init__(self):
        self.positions = []
        self.clicks = []

    def move_to(self, x, y):
        self.positions.append((x, y))

    def click(self, x, y):
        self.clicks.append((x, y))


class MockRunner(CraftingOps):
    def __init__(self):
        self._keyboard = DummyKeyboard()
        self._mouse = DummyMouse()
        self.logs = []
        self.statuses = []
        self._crafting_settings = {
            "enabled": True,
            "count": 0,
            "every": 1,
            "items": [{"key": "sprite_red", "enabled": True, "amount": "max"}],
        }

    def _log(self, message):
        self.logs.append(message)

    def _set_status(self, **kwargs):
        self.statuses.append(kwargs)

    def _checkpoint(self, stop_event):
        return False

    def _ensure_lobby(self, hwnd, stop_event):
        return True

    def _recover_to_lobby(self, hwnd, stop_event):
        return True

    def _click_found_image(self, hwnd, name, timeout, stop_event):
        return {"score": 1.0, "cx": 100, "cy": 100}

    def _get_crafting_settings(self):
        return self._crafting_settings

    def _set_crafting_count(self, count):
        self._crafting_settings["count"] = count


def test_run_crafting_does_not_click_nav_closeui_when_craft_menu_opens(monkeypatch):
    runner = MockRunner()
    stop_event = threading.Event()
    searched_images = []

    def mock_wait_for(hwnd, name, timeout, stop_event):
        searched_images.append(name)
        return True

    def mock_find(hwnd, name, timeout, stop_event, region=None):
        searched_images.append(name)
        return {"score": 1.0, "cx": 50, "cy": 50}

    monkeypatch.setattr(runner, "_crafting_wait_for", mock_wait_for)
    monkeypatch.setattr(runner, "_crafting_find", mock_find)

    # Execute crafting pass
    runner._run_crafting(123, stop_event, force=True)

    # nav_closeui must NOT be searched or clicked when craft_menu opens normally
    assert "nav_closeui" not in searched_images
    assert "[Craft] Pressing E to open the crafting menu." in runner.logs
    assert "[Craft] Crafting pass finished -- returning to the lobby." in runner.logs
    # Verify mouse was parked at far right edge
    assert len(runner._mouse.positions) > 0


def test_run_crafting_fallback_nav_closeui_when_craft_menu_blocked(monkeypatch):
    runner = MockRunner()
    stop_event = threading.Event()
    searched_find = []
    wait_for_calls = []

    def mock_wait_for(hwnd, name, timeout, stop_event):
        wait_for_calls.append(name)
        if name == "craft_menu":
            # First craft_menu call fails (blocked), second succeeds (after closing overlay)
            count = wait_for_calls.count("craft_menu")
            return count > 1
        return True

    def mock_find(hwnd, name, timeout, stop_event, region=None):
        searched_find.append(name)
        return {"score": 1.0, "cx": 50, "cy": 50}

    monkeypatch.setattr(runner, "_crafting_wait_for", mock_wait_for)
    monkeypatch.setattr(runner, "_crafting_find", mock_find)

    # Execute crafting pass
    runner._run_crafting(123, stop_event, force=True)

    # nav_closeui should be searched as a fallback when craft_menu fails initially
    assert "nav_closeui" in searched_find
    assert "[Craft] Closing blocking UI overlay (nav_closeui) and retrying E." in runner.logs
    assert "[Craft] Crafting pass finished -- returning to the lobby." in runner.logs


def test_current_qualifying_win_triggers_exact_threshold():
    runner = MockRunner()
    runner._crafting_settings.update({"count": 24, "every": 25})
    mastery = {"mode": "story", "stage": "Mastery"}

    assert runner._crafting_wants_in(mastery, "win") is True
    assert runner._crafting_wants_in(mastery, "loss") is False


def test_current_win_does_not_trigger_before_threshold():
    runner = MockRunner()
    runner._crafting_settings.update({"count": 23, "every": 25})

    assert runner._crafting_wants_in(
        {"mode": "story", "stage": "Mastery"}, "win") is False


def test_challenge_win_qualifies_for_projected_threshold():
    runner = MockRunner()
    runner._crafting_settings.update({"count": 24, "every": 25})

    assert runner._crafting_wants_in(
        {"is_challenge": True}, "win") is True


def test_no_enabled_sprites_does_not_accumulate_impossible_progress():
    runner = MockRunner()
    runner._crafting_settings["count"] = 33
    runner._crafting_settings["every"] = 25
    runner._crafting_settings["items"][0]["enabled"] = False
    mastery = {"mode": "story", "stage": "Mastery"}

    assert runner._crafting_wants_in() is False

    assert runner._crafting_settings["count"] == 0
    runner._note_win_for_crafting(mastery, "win")
    assert runner._crafting_wants_in(mastery, "win") is False
    assert runner.logs == [
        "[Craft] Invalid Auto Crafting setup -- no sprites are selected. "
        "Skipping Auto Crafting until at least one sprite is enabled."
    ]


def test_invalid_crafting_warning_logs_once_until_configuration_is_fixed():
    runner = MockRunner()
    runner._crafting_settings["items"][0]["enabled"] = False

    assert runner._crafting_wants_in() is False
    assert runner._crafting_wants_in() is False
    assert len(runner.logs) == 1

    runner._crafting_settings["items"][0]["enabled"] = True
    assert runner._crafting_wants_in() is False
    runner._crafting_settings["items"][0]["enabled"] = False
    assert runner._crafting_wants_in() is False
    assert len(runner.logs) == 2


def _run_two_repeat_flow(settings):
    def set_count(count):
        settings["count"] = count

    runner = MacroRunner(
        mouse=Mock(), keyboard=Mock(), log=Mock(),
        get_crafting_settings=lambda: settings,
        set_crafting_count=set_count,
    )
    runner._stop_event = threading.Event()
    runner._current_hwnd = None
    runner._run_task_setup = Mock(return_value=True)
    runner._play_one_match = Mock(return_value="win")
    runner._challenge_has_ready_stage = Mock(return_value=False)
    repeat_decisions = []

    def handle_result(_hwnd, _stop, task, result, _duration, _webhook, repeat):
        repeat_decisions.append(repeat)
        runner._note_win_for_crafting(task, result)
        return True

    runner._handle_match_result = Mock(side_effect=handle_result)

    def craft(_hwnd, _stop):
        set_count(0)

    runner._run_crafting = Mock(side_effect=craft)
    runner._wait_teleport_in = Mock(return_value=True)
    task = {
        "mode": "story",
        "map": "King's Tomb",
        "stage": "Mastery",
        "repeat": 2,
        "play_mode": "solo",
    }

    assert runner._run_task(
        123, runner._stop_event, task, 1, 1, {}, 3, 8, {}, {}) is True
    return runner, repeat_decisions


def test_repeat_flow_leaves_and_crafts_on_threshold_match():
    settings = {
        "enabled": True,
        "count": 24,
        "every": 25,
        "items": [{"key": "sprite_red", "enabled": True, "amount": "max"}],
    }

    runner, repeat_decisions = _run_two_repeat_flow(settings)

    # The first match is the 25th qualifying win. It must Leave Stage
    # (repeat=False), craft once, and only then re-enter for repeat #2.
    assert repeat_decisions == [False, False]
    runner._run_crafting.assert_called_once()
    assert runner._run_task_setup.call_count == 2


def test_invalid_setup_behaves_like_crafting_is_disabled():
    settings = {
        "enabled": True,
        "count": 33,
        "every": 25,
        "items": [{"key": "sprite_red", "enabled": False, "amount": "max"}],
    }

    runner, repeat_decisions = _run_two_repeat_flow(settings)

    assert repeat_decisions == [True, False]
    runner._run_crafting.assert_not_called()
    runner._wait_teleport_in.assert_called_once()
    assert settings["count"] == 0
