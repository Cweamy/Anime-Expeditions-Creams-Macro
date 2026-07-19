import os
import ctypes
from ctypes import wintypes

from . import config

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

# Pointer-sized return value: without this, ctypes' default c_int return
# truncates the handle on 64-bit Windows and GWL_HWNDPARENT reads back garbage.
user32.GetWindowLongPtrW.restype = ctypes.c_void_p
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ROBLOX_PROCESS_NAME = "robloxplayerbeta.exe"

GWL_STYLE = -16
GWL_EXSTYLE = -20
GWL_HWNDPARENT = -8
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_FRAMECHANGED = 0x0020
SW_HIDE = 0
SW_SHOW = 5
SW_RESTORE = 9
HWND_TOP = 0
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
WS_CAPTION = 0x00C00000
WS_BORDER = 0x00800000
WS_THICKFRAME = 0x00040000


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class WindowManager:
    """Locates the Roblox window and keeps coordinate math in one place.

    Placement/detection code should work in *client* coordinates (0,0 at
    the top-left of the game viewport) and call client_to_screen() only
    at the point of actually moving the mouse/clicking.
    """

    def __init__(self, title_substring: str = config.ROBLOX_WINDOW_TITLE):
        self.title_substring = title_substring
        self.hwnd = None

    def find(self):
        matches = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _enum_proc(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if self.title_substring.lower() in buf.value.lower():
                matches.append(hwnd)
                return False
            return True

        user32.EnumWindows(_enum_proc, 0)
        self.hwnd = matches[0] if matches else None
        return self.hwnd

    def _require_hwnd(self):
        if not self.hwnd:
            raise RuntimeError("No window handle, call find() first (and check it returned non-None)")
        return self.hwnd

    def get_window_rect(self):
        hwnd = self._require_hwnd()
        rect = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return rect.left, rect.top, rect.right, rect.bottom

    def get_client_size(self):
        hwnd = self._require_hwnd()
        rect = RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        return rect.right - rect.left, rect.bottom - rect.top

    def client_to_screen(self, x: int, y: int):
        hwnd = self._require_hwnd()
        pt = wintypes.POINT(x, y)
        user32.ClientToScreen(hwnd, ctypes.byref(pt))
        return pt.x, pt.y

    def resize_client_to(self, width: int = config.FIXED_WIN_W, height: int = config.FIXED_WIN_H) -> None:
        hwnd = self._require_hwnd()
        new_w, new_h = client_size_to_window_size(hwnd, width, height)
        left, top, _, _ = self.get_window_rect()
        user32.SetWindowPos(hwnd, 0, left, top, new_w, new_h, SWP_NOZORDER | SWP_NOACTIVATE)

    def is_client_size_correct(self, width: int = config.FIXED_WIN_W, height: int = config.FIXED_WIN_H) -> bool:
        return self.get_client_size() == (width, height)

    def bring_to_front(self) -> None:
        hwnd = self._require_hwnd()
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)


# ── Generic hwnd utilities (used on both the Roblox window and our own GUI
# window, e.g. by core.dock), not tied to WindowManager's tracked handle. ──

def client_size_to_window_size(hwnd: int, width: int, height: int):
    """Outer window size needed for a given *client* area, based on hwnd's
    current style/border. Read the style AFTER restore_borders()/remove_borders()
    has already been applied, or this will be computed against the wrong frame."""
    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
    ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    rect = RECT(0, 0, width, height)
    user32.AdjustWindowRectEx(ctypes.byref(rect), style, False, ex_style)
    return rect.right - rect.left, rect.bottom - rect.top


def set_dpi_aware() -> None:
    """Call once at startup. Roblox is docked at a hardcoded pixel size
    (config.FIXED_WIN_W/H); if this process and Roblox disagree on what a
    'pixel' is (non-100% Windows display scaling), the docked game ends up
    the wrong size on screen."""
    try:
        user32.SetProcessDpiAwarenessContext(-4)
        return
    except (OSError, AttributeError):
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        return
    except (OSError, AttributeError):
        pass
    try:
        user32.SetProcessDPIAware()
    except (OSError, AttributeError):
        pass


def get_display_scale_percent() -> int:
    try:
        return round(user32.GetDpiForSystem() / 96 * 100)
    except (OSError, AttributeError):
        return 100


def get_screen_size():
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def get_process_name(hwnd: int) -> str:
    """Executable name for a window's owning process (e.g. 'robloxplayerbeta.exe')."""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == 0:
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    buf = ctypes.create_unicode_buffer(260)
    size = wintypes.DWORD(260)
    kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
    kernel32.CloseHandle(handle)
    return os.path.basename(buf.value).lower()


def find_roblox_window() -> int:
    """Find Roblox by title AND owning process name. Title alone isn't enough:
    a Chrome tab titled 'Roblox' (or a YouTube video, Discord DM, etc.) matches
    a plain substring search just as well as the real game window does."""
    matches = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _enum_proc(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if "roblox" in buf.value.lower() and get_process_name(hwnd) == ROBLOX_PROCESS_NAME:
            matches.append(hwnd)
            return False
        return True

    user32.EnumWindows(_enum_proc, 0)
    return matches[0] if matches else 0


def is_window(hwnd: int) -> bool:
    """Is this handle still a real window at all? Deliberately IsWindow, not
    IsWindowVisible: dock()/undock()/the watchdog all use this to mean "has
    Roblox closed", and intentionally hiding it (hide_window(), for the
    Info/Settings screens) must not be mistaken for that."""
    return bool(user32.IsWindow(hwnd))


def get_window_rect_screen(hwnd: int):
    """hwnd's bounding box in screen coordinates -- works the same whether
    it's a docked child or a standalone top-level window, so callers (e.g.
    the debug screenshot) don't need to reparent/move anything just to know
    where it currently is on screen."""
    rect = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def is_foreground(hwnd: int) -> bool:
    return user32.GetForegroundWindow() == hwnd


def is_window_visible(hwnd: int) -> bool:
    return bool(user32.IsWindowVisible(hwnd))


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


PW_RENDERFULLCONTENT = 0x00000002  # undocumented pre-Win8.1, required for DX-composited windows


def capture_window_rgb(hwnd: int):
    """Grabs hwnd's own rendered contents via PrintWindow, NOT a screen-region
    grab -- so it returns the actual window pixels even when the window is
    hidden or covered by something else (a screen grab of a hidden window's
    rect would just capture whatever is drawn there instead, e.g. our own GUI).

    Returns (rgb_bytes, width, height) or None if the window couldn't be
    rendered (PrintWindow failed or produced an all-black frame, which is what
    a hidden DirectX window with no live swapchain surface typically yields).
    Sends no input and never changes focus/visibility.
    """
    rect = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return None
    hdc = user32.GetWindowDC(hwnd)
    if not hdc:
        return None
    mem_dc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mem_dc, bmp)
    try:
        # A fresh GDI bitmap is NOT guaranteed zeroed -- it can hold recycled
        # framebuffer garbage, frequently whatever was last on screen. If
        # PrintWindow then "succeeds" without actually drawing (a hidden DX
        # window with no frame), that garbage would pass the all-black check
        # below and get returned as a plausible-looking but wrong capture
        # (this is exactly how "Use Roblox Screen" kept returning the macro's
        # own UI). Blacken the bitmap first so a no-op PrintWindow is always
        # detected as the failure it is.
        gdi32.PatBlt(mem_dc, 0, 0, w, h, 0x00000042)  # BLACKNESS
        if not user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT):
            return None
        bmi = _BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h  # negative = top-down row order
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0  # BI_RGB
        buf = (ctypes.c_char * (w * h * 4))()
        if gdi32.GetDIBits(mem_dc, bmp, 0, h, buf, ctypes.byref(bmi), 0) != h:
            return None
        raw = bytearray(buf)  # BGRA
        rgb = bytearray(w * h * 3)
        rgb[0::3] = raw[2::4]
        rgb[1::3] = raw[1::4]
        rgb[2::3] = raw[0::4]
        if not any(rgb):  # "success" but nothing was actually rendered
            return None
        return bytes(rgb), w, h
    finally:
        gdi32.SelectObject(mem_dc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, hdc)


def hide_window(hwnd: int) -> None:
    user32.ShowWindow(hwnd, SW_HIDE)


def show_window(hwnd: int) -> None:
    user32.ShowWindow(hwnd, SW_SHOW)


def activate_window(hwnd: int) -> None:
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)


def move_window(hwnd: int, x: int, y: int, w: int, h: int) -> None:
    user32.MoveWindow(hwnd, x, y, w, h, True)


def bring_to_top(hwnd: int) -> None:
    user32.SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)


def set_always_on_top(hwnd: int, on: bool = True) -> None:
    flag = HWND_TOPMOST if on else HWND_NOTOPMOST
    user32.SetWindowPos(hwnd, flag, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)


def get_parent(hwnd: int) -> int:
    """Raw GWL_HWNDPARENT read, not the GetParent() API: GetParent() only
    reports a meaningful value for windows with WS_CHILD or WS_POPUP set, and
    silently returns 0 for a plain top-level window even after SetParent has
    genuinely reparented it -- exactly the case here, since dock()/undock()
    reparent Roblox without ever toggling its WS_CHILD bit. GWL_HWNDPARENT
    reflects the real value regardless of window style."""
    return user32.GetWindowLongPtrW(hwnd, GWL_HWNDPARENT) or 0


def set_parent(child: int, parent: int) -> bool:
    """Reparent child into parent. Pass parent=0 to make it standalone again.

    Returns whether the OS actually accepted the change. SetParent returning 0
    does NOT by itself mean failure (0 is also the legitimate old-parent value
    when the window had none before), so per Win32 convention this only
    trusts a 0 return as a real failure if GetLastError was actually set by
    this call. Silently ignoring a real failure here is exactly how Roblox
    could stay parented to the GUI window right up until it gets destroyed
    and destroys of Roblox along with it (see core.dock.GameDocker.undock)."""
    kernel32.SetLastError(0)
    result = user32.SetParent(child, parent)
    if result == 0:
        return kernel32.GetLastError() == 0
    return True


def remove_borders(hwnd: int) -> None:
    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
    style &= ~WS_CAPTION
    style &= ~WS_BORDER
    style &= ~WS_THICKFRAME
    user32.SetWindowLongW(hwnd, GWL_STYLE, style)
    user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)


def restore_borders(hwnd: int) -> None:
    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
    style |= WS_CAPTION | WS_BORDER | WS_THICKFRAME
    user32.SetWindowLongW(hwnd, GWL_STYLE, style)
    user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)
