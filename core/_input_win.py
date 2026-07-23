"""Windows implementation of the low-level input primitives Mouse/Keyboard
are built on -- a thin adapter over core._sendinput (the raw Win32
SendInput plumbing, unchanged) exposing the same small primitive set
core._input_mac implements with Quartz, so mouse.py/keyboard.py stay one
cross-platform implementation each instead of forking per OS.

Primitive contract (both platforms):
    move_abs(x, y)        -- absolute cursor move, screen coords
    move_rel(dx, dy)      -- small relative move (real hover-move event)
    button_down/up(btn)   -- "left" / "right" / "middle"
    scroll(amount)        -- vertical wheel, Windows delta units (+-120/notch)
    cursor_pos() -> (x,y)
    key_down/up(vk)       -- Win32 virtual-key code (core.keys is the
                             app-wide currency; mac translates internally)
    is_key_down(vk)       -- live physical key state (for the walk-path
                             recorder's polling, see core.paths)
"""
import ctypes
from ctypes import wintypes

from . import _sendinput as si

_BTN_DOWN = {"left": si.MOUSEEVENTF_LEFTDOWN, "right": si.MOUSEEVENTF_RIGHTDOWN, "middle": si.MOUSEEVENTF_MIDDLEDOWN}
_BTN_UP = {"left": si.MOUSEEVENTF_LEFTUP, "right": si.MOUSEEVENTF_RIGHTUP, "middle": si.MOUSEEVENTF_MIDDLEUP}


def move_abs(x: int, y: int) -> None:
    abs_x, abs_y = si.screen_to_absolute(x, y)
    si.send_mouse_input(si.MouseInput(
        dx=abs_x, dy=abs_y, mouseData=0,
        dwFlags=si.MOUSEEVENTF_MOVE | si.MOUSEEVENTF_ABSOLUTE | si.MOUSEEVENTF_VIRTUALDESK,
        time=0, dwExtraInfo=0))


def move_rel(dx: int, dy: int) -> None:
    si.send_mouse_input(si.MouseInput(dx=dx, dy=dy, mouseData=0, dwFlags=si.MOUSEEVENTF_MOVE, time=0, dwExtraInfo=0))


def button_down(button: str) -> None:
    si.send_mouse_input(si.MouseInput(dx=0, dy=0, mouseData=0, dwFlags=_BTN_DOWN[button], time=0, dwExtraInfo=0))


def button_up(button: str) -> None:
    si.send_mouse_input(si.MouseInput(dx=0, dy=0, mouseData=0, dwFlags=_BTN_UP[button], time=0, dwExtraInfo=0))


def scroll(amount: int) -> None:
    si.send_mouse_input(si.MouseInput(dx=0, dy=0, mouseData=amount, dwFlags=si.MOUSEEVENTF_WHEEL, time=0, dwExtraInfo=0))


def cursor_pos():
    pt = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


# Keys whose scancode collides with a numpad key unless the EXTENDEDKEY
# flag marks them as the "extended" variant: without it, VK_LEFT's scan
# (0x4B) IS numpad-4 to anything reading raw scancodes -- confirmed live
# with Camera Setup 3's Left-arrow hold doing nothing in Roblox. A real
# keyboard driver sets the E0 prefix for these; SendInput needs the flag
# to say the same thing.
_EXTENDED_VKS = {
    0x21, 0x22, 0x23, 0x24,  # PgUp, PgDn, End, Home
    0x25, 0x26, 0x27, 0x28,  # Left, Up, Right, Down arrows
    0x2D, 0x2E,              # Insert, Delete
    0x6F,                    # Numpad divide
    0x90,                    # NumLock
    0xA3, 0xA5,              # Right Ctrl, Right Alt
}


def _key_flags(vk: int) -> int:
    flags = si.KEYEVENTF_SCANCODE
    if vk in _EXTENDED_VKS:
        flags |= si.KEYEVENTF_EXTENDEDKEY
    return flags


def key_down(vk: int) -> None:
    # Scan codes, not VK codes, for the actual event -- matches what a real
    # keyboard driver reports, picked up more reliably by games.
    scan = si.vk_to_scan(vk)
    si.send_keyboard_input(si.KeyBdInput(wVk=0, wScan=scan, dwFlags=_key_flags(vk), time=0, dwExtraInfo=0))


def key_up(vk: int) -> None:
    scan = si.vk_to_scan(vk)
    si.send_keyboard_input(si.KeyBdInput(
        wVk=0, wScan=scan, dwFlags=_key_flags(vk) | si.KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))


def is_key_down(vk: int) -> bool:
    # GetAsyncKeyState reads real physical key state regardless of which
    # window has focus -- see core.paths' recorder for why that matters.
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
