import ctypes
import time
from ctypes import wintypes

from . import _sendinput as si


class Mouse:
    """Screen-space mouse controller built on SendInput.

    All coordinates are absolute screen pixels. If you're clicking inside
    the Roblox window, convert client coordinates to screen coordinates
    with WindowManager.client_to_screen() first.
    """

    _DOWN_FLAG = {
        "left": si.MOUSEEVENTF_LEFTDOWN,
        "right": si.MOUSEEVENTF_RIGHTDOWN,
        "middle": si.MOUSEEVENTF_MIDDLEDOWN,
    }
    _UP_FLAG = {
        "left": si.MOUSEEVENTF_LEFTUP,
        "right": si.MOUSEEVENTF_RIGHTUP,
        "middle": si.MOUSEEVENTF_MIDDLEUP,
    }

    def move_to(self, x: int, y: int) -> None:
        abs_x, abs_y = si.screen_to_absolute(x, y)
        mi = si.MouseInput(
            dx=abs_x, dy=abs_y, mouseData=0,
            dwFlags=si.MOUSEEVENTF_MOVE | si.MOUSEEVENTF_ABSOLUTE | si.MOUSEEVENTF_VIRTUALDESK,
            time=0, dwExtraInfo=0,
        )
        si.send_mouse_input(mi)

    def down(self, button: str = "left") -> None:
        mi = si.MouseInput(dx=0, dy=0, mouseData=0, dwFlags=self._DOWN_FLAG[button], time=0, dwExtraInfo=0)
        si.send_mouse_input(mi)

    def up(self, button: str = "left") -> None:
        mi = si.MouseInput(dx=0, dy=0, mouseData=0, dwFlags=self._UP_FLAG[button], time=0, dwExtraInfo=0)
        si.send_mouse_input(mi)

    def nudge(self, dx: int = 1, dy: int = 0) -> None:
        """Sends a tiny *relative* move. A jump straight to a point via
        move_to() is an absolute positioning message -- some UI elements
        (buttons, scrollable panels) only register real hover from an
        actual relative mouse-move event, which that absolute jump doesn't
        reliably fire on its own."""
        mi = si.MouseInput(dx=dx, dy=dy, mouseData=0, dwFlags=si.MOUSEEVENTF_MOVE, time=0, dwExtraInfo=0)
        si.send_mouse_input(mi)

    def click(self, x: int = None, y: int = None, button: str = "left", hold: float = 0.05) -> None:
        if x is not None and y is not None:
            self.move_to(x, y)
            time.sleep(0.01)
            self.nudge()
            time.sleep(0.005)
        self.down(button)
        time.sleep(hold)
        self.up(button)

    def double_click(self, x: int = None, y: int = None, button: str = "left", gap: float = 0.08) -> None:
        self.click(x, y, button)
        time.sleep(gap)
        self.click(x, y, button)

    def shuffle_click(self, x: int, y: int, button: str = "left", hold: float = 0.05) -> None:
        """Like click(), but hovers into the target with a few small
        relative moves first instead of just the one tiny nudge() click()
        already does. Some buttons (reported: Expedition's "extract"
        confirm) apparently need genuine hover-in movement to actually
        register a click on the game's side even though the click itself
        visually lands -- a single absolute jump + one nudge wasn't always
        enough. Approaches from a random-ish nearby offset and nudges in
        toward the real point over a few steps, each a real relative
        MOUSEEVENTF_MOVE, before finally clicking."""
        self.move_to(x - 6, y - 4)
        time.sleep(0.03)
        for dx, dy in ((3, 2), (2, 1), (1, 1)):
            self.nudge(dx, dy)
            time.sleep(0.03)
        self.move_to(x, y)
        time.sleep(0.03)
        self.nudge()
        time.sleep(0.03)
        self.down(button)
        time.sleep(hold)
        self.up(button)

    def drag(self, x1: int, y1: int, x2: int, y2: int, button: str = "left", steps: int = 15, duration: float = 0.2) -> None:
        self.move_to(x1, y1)
        time.sleep(0.01)
        self.down(button)
        step_delay = duration / steps
        for i in range(1, steps + 1):
            ix = x1 + (x2 - x1) * i / steps
            iy = y1 + (y2 - y1) * i / steps
            self.move_to(int(ix), int(iy))
            time.sleep(step_delay)
        self.up(button)

    def scroll(self, amount: int) -> None:
        mi = si.MouseInput(dx=0, dy=0, mouseData=amount, dwFlags=si.MOUSEEVENTF_WHEEL, time=0, dwExtraInfo=0)
        si.send_mouse_input(mi)

    def position(self):
        pt = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y
