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

    def type_text(self, text: str, delay: float = 0.02) -> list:
        """Types `text` on the CURRENT keyboard layout. Returns the list of
        characters that had to be skipped (empty when everything was typed).

        Unmappable characters are skipped, not raised on. The only caller is
        runner._search_and_set_toggle, which types a user-supplied Setting
        block name; it has no try/except, and neither does _run_session (just
        try/finally), so raising here would take the whole run thread down
        over one stray character. Skipping degrades into the existing, handled
        failure -- the Settings search simply doesn't match and the block logs
        "not found in Settings -- skipping".
        """
        # Resolved once, not per character: it never changes mid-string.
        mapper = getattr(backend, "key_for_char", None)
        skipped = []
        # pace=False per character -- a paced tap on every letter would
        # turn typing a setting name into several seconds at higher Macro
        # Speed delays. One pause at the end covers the whole string.
        for ch in text:
            mapped = mapper(ch) if mapper is not None else (ord(ch.upper()), ())
            if mapped is None:
                skipped.append(ch)
                continue
            vk, modifiers = mapped
            for modifier in modifiers:
                self.key_down(modifier)
            try:
                self.tap(vk, pace=False)
            finally:
                # finally, so a failure mid-tap can't strand Shift/Ctrl/Alt
                # held down for every keystroke that follows.
                for modifier in reversed(modifiers):
                    self.key_up(modifier)
            time.sleep(delay)
        pacing.action_pause()
        return skipped

    def combo(self, *vks: int, hold: float = 0.05) -> None:
        for vk in vks:
            self.key_down(vk)
        time.sleep(hold)
        for vk in reversed(vks):
            self.key_up(vk)
        pacing.action_pause()
