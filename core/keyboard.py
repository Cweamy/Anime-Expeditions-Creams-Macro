import time

from . import _sendinput as si


class Keyboard:
    """Keyboard controller built on SendInput, using scan codes (not virtual-key
    codes) for the actual event: scan-code injection matches what a real
    keyboard driver reports and is picked up more reliably by games."""

    def key_down(self, vk: int) -> None:
        scan = si.vk_to_scan(vk)
        ki = si.KeyBdInput(wVk=0, wScan=scan, dwFlags=si.KEYEVENTF_SCANCODE, time=0, dwExtraInfo=0)
        si.send_keyboard_input(ki)

    def key_up(self, vk: int) -> None:
        scan = si.vk_to_scan(vk)
        ki = si.KeyBdInput(
            wVk=0, wScan=scan,
            dwFlags=si.KEYEVENTF_SCANCODE | si.KEYEVENTF_KEYUP,
            time=0, dwExtraInfo=0,
        )
        si.send_keyboard_input(ki)

    def tap(self, vk: int, hold: float = 0.03) -> None:
        self.key_down(vk)
        time.sleep(hold)
        self.key_up(vk)

    def type_text(self, text: str, delay: float = 0.02) -> None:
        for ch in text:
            self.tap(ord(ch.upper()))
            time.sleep(delay)

    def combo(self, *vks: int, hold: float = 0.05) -> None:
        for vk in vks:
            self.key_down(vk)
        time.sleep(hold)
        for vk in reversed(vks):
            self.key_up(vk)
