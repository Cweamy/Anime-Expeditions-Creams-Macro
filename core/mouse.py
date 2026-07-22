import sys
import time

from . import pacing

# One cross-platform Mouse class over per-OS input primitives: Win32
# SendInput on Windows (see _input_win/_sendinput), Quartz CGEvents on
# macOS (see _input_mac). All the click choreography below (hold timing,
# hover nudges, shuffle approach) is platform-neutral behavior tuned
# against the game -- only the raw event synthesis differs per OS.
if sys.platform == "darwin":
    from . import _input_mac as backend
else:
    from . import _input_win as backend


class Mouse:
    """Screen-space mouse controller.

    All coordinates are absolute screen pixels (macOS: global display
    points -- same thing as far as callers are concerned, see
    _input_mac's coordinate note). If you're clicking inside the Roblox
    window, convert client coordinates to screen coordinates first.

    Every click-style action ends with pacing.action_pause() -- the
    user-adjustable "Macro Speed" extra delay (Settings > General), a
    no-op at its default 0ms. Applied here at the choke point rather than
    per call site so ONE setting slows every click the macro makes.
    """

    def move_to(self, x: int, y: int) -> None:
        backend.move_abs(int(x), int(y))

    def down(self, button: str = "left") -> None:
        backend.button_down(button)

    def up(self, button: str = "left") -> None:
        backend.button_up(button)

    def nudge(self, dx: int = 1, dy: int = 0) -> None:
        """Sends a tiny *relative* move. A jump straight to a point via
        move_to() is an absolute positioning message -- some UI elements
        (buttons, scrollable panels) only register real hover from an
        actual relative mouse-move event, which that absolute jump doesn't
        reliably fire on its own."""
        backend.move_rel(dx, dy)

    def click(self, x: int = None, y: int = None, button: str = "left", hold: float = 0.05) -> None:
        if x is not None and y is not None:
            self.move_to(x, y)
            time.sleep(0.01)
            self.nudge()
            time.sleep(0.005)
        self.down(button)
        time.sleep(hold)
        self.up(button)
        pacing.action_pause()

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
        move event, before finally clicking."""
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
        pacing.action_pause()

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
        pacing.action_pause()

    def scroll(self, amount: int) -> None:
        # Windows wheel-delta units (+-120 per notch) on both platforms --
        # the mac backend converts to scroll lines itself. No
        # action_pause() here: scrolls run in tight caller-paced loops
        # (map carousel, reward list) that already sleep between notches.
        backend.scroll(amount)

    def position(self):
        return backend.cursor_pos()
