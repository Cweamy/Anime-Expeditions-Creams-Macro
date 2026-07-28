import threading

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
