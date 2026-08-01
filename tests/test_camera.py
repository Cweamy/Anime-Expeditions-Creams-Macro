import pytest

from core import camera


class FakeMouse:
    def __init__(self, fail_on_drag=None):
        self.events = []
        self.drag_count = 0
        self.fail_on_drag = fail_on_drag

    def move_to(self, x, y):
        self.events.append(("move_to", x, y))

    def nudge(self, x=0, y=0):
        self.events.append(("nudge", x, y))
        if y:
            self.drag_count += 1
            if self.drag_count == self.fail_on_drag:
                raise RuntimeError("drag failed")

    def down(self, button):
        self.events.append(("down", button))

    def up(self, button):
        self.events.append(("up", button))


class FakeKeyboard:
    def __init__(self):
        self.events = []

    def key_down(self, key):
        self.events.append(("down", key))

    def key_up(self, key):
        self.events.append(("up", key))


@pytest.fixture(autouse=True)
def no_camera_delays(monkeypatch):
    monkeypatch.setattr(camera.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(camera.wm, "get_window_rect_screen", lambda _hwnd: (100, 200, 500, 600))


def test_tilt_camera_top_down_pins_pitch_without_keyboard_input():
    mouse = FakeMouse()

    camera.tilt_camera_top_down(mouse, hwnd=123)

    assert mouse.events[0] == ("move_to", 300, 400)
    assert mouse.events[1] == ("nudge", 0, 0)
    assert mouse.events[2] == ("down", "right")
    assert mouse.events[-1] == ("up", "right")
    assert mouse.events.count(("nudge", 0, 80)) == 40


def test_tilt_camera_top_down_releases_right_button_after_drag_failure():
    mouse = FakeMouse(fail_on_drag=3)

    with pytest.raises(RuntimeError, match="drag failed"):
        camera.tilt_camera_top_down(mouse, hwnd=123)

    assert mouse.events[-1] == ("up", "right")


def test_standard_camera_setup_still_adds_the_o_zoom_hold():
    mouse = FakeMouse()
    keyboard = FakeKeyboard()

    camera.run_camera_setup(mouse, keyboard, hwnd=123, hold_ms=2000)

    assert keyboard.events == [("down", ord("O")), ("up", ord("O"))]
