"""Low-level Win32 SendInput plumbing shared by Mouse and Keyboard.

SendInput is used instead of SetCursorPos/mouse_event/pyautogui's default
backend because it pushes events through the real input stack, which is
what most games (and DirectInput titles) actually listen to. Cursor-only
writes are ignored by a lot of games.
"""
import ctypes
import sys

if not sys.platform.startswith("win"):
    raise RuntimeError("core._sendinput requires Windows (uses ctypes.WinDLL('user32'))")

user32 = ctypes.WinDLL("user32", use_last_error=True)

ULONG_PTR = ctypes.c_size_t

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

MAPVK_VK_TO_VSC = 0


class MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KeyBdInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HardwareInput(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_short),
        ("wParamH", ctypes.c_ushort),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", MouseInput), ("ki", KeyBdInput), ("hi", HardwareInput)]


class Input(ctypes.Structure):
    _anonymous_ = ("_u",)
    _fields_ = [("type", ctypes.c_ulong), ("_u", _InputUnion)]


def send_mouse_input(mi: MouseInput) -> None:
    inp = Input(type=INPUT_MOUSE, mi=mi)
    _dispatch(inp)


def send_keyboard_input(ki: KeyBdInput) -> None:
    inp = Input(type=INPUT_KEYBOARD, ki=ki)
    _dispatch(inp)


def _dispatch(inp: Input) -> None:
    ctypes.set_last_error(0)
    sent = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(Input))
    if sent != 1:
        raise ctypes.WinError(ctypes.get_last_error())


def virtual_screen_rect():
    x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    return x, y, w, h


def screen_to_absolute(x: int, y: int):
    vx, vy, vw, vh = virtual_screen_rect()
    abs_x = int(((x - vx) * 65536) / vw)
    abs_y = int(((y - vy) * 65536) / vh)
    return abs_x, abs_y


def vk_to_scan(vk: int) -> int:
    return user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
