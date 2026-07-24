import sys
import time

from . import pacing

# Same per-OS primitive split as core.mouse -- Win32 scan-code SendInput on
# Windows, Quartz CGEvents on macOS (which also translates the app-wide
# Win32 VK codes to mac keycodes, see _input_mac._VK_TO_MAC).
if sys.platform == "darwin":
    from . import _input_mac as backend
else:
    from . import _input_win as backend


class Keyboard:
    """Keyboard controller over the per-OS input backend. Callers speak
    Win32 virtual-key codes everywhere (core.keys, recorded walk paths,
    block hotkeys) regardless of platform -- keeps every stored
    keybinding/recording portable between Windows and macOS.

    tap()/combo() end with pacing.action_pause() -- the same
    user-adjustable Macro Speed delay Mouse's clicks get (a no-op at the
    default 0ms). key_down/key_up stay raw: walk-path replay times those
    precisely from the recording and must not be skewed per event.
    """

    def key_down(self, vk: int) -> None:
        backend.key_down(vk)

    def key_up(self, vk: int) -> None:
        backend.key_up(vk)

    # Movement/action keys by NAME ("w"/"a"/"s"/"d"/"i"/"o"), sent by fixed
    # physical position so walk paths work on any keyboard layout (AZERTY,
    # QWERTZ, ...), not just QWERTY -- see the backend move_key_* functions.
    def move_key_down(self, name: str) -> None:
        backend.move_key_down(name)

    def move_key_up(self, name: str) -> None:
        backend.move_key_up(name)

    def tap(self, vk: int, hold: float = 0.03, pace: bool = True) -> None:
        self.key_down(vk)
        time.sleep(hold)
        self.key_up(vk)
        if pace:
            pacing.action_pause()

    def type_text(self, text: str, delay: float = 0.02) -> None:
        # pace=False per character -- a paced tap on every letter would
        # turn typing a setting name into several seconds at higher Macro
        # Speed delays. One pause at the end covers the whole string.
        for ch in text:
            self.tap(ord(ch.upper()), pace=False)
            time.sleep(delay)
        pacing.action_pause()

    def combo(self, *vks: int, hold: float = 0.05) -> None:
        for vk in vks:
            self.key_down(vk)
        time.sleep(hold)
        for vk in reversed(vks):
            self.key_up(vk)
        pacing.action_pause()
