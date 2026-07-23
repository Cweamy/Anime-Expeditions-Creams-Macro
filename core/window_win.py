import os
import ctypes
from ctypes import wintypes

from . import config

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

# Pointer-sized return value: without this, ctypes' default c_int return
# truncates the handle on 64-bit Windows and GWL_HWNDPARENT reads back garbage.
user32.GetWindowLongPtrW.restype = ctypes.c_void_p
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]

# Handle-sized return/params -- same truncation risk as GetWindowLongPtrW
# above (ctypes defaults to c_int, which chops a 64-bit HICON/HWND).
user32.LoadImageW.restype = ctypes.c_void_p
user32.LoadImageW.argtypes = [
    wintypes.HINSTANCE, wintypes.LPCWSTR, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.SendMessageW.restype = ctypes.c_void_p
user32.SendMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, ctypes.c_void_p]

# Used by activate_window's AttachThreadInput workaround below.
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, wintypes.LPDWORD]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

# HANDLE-returning/consuming calls used by is_process_elevated -- like
# GetWindowLongPtrW above, ctypes' default c_int return/args would
# truncate a 64-bit HANDLE, so these need explicit pointer-sized types.
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
advapi32.OpenProcessToken.restype = wintypes.BOOL
advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
advapi32.GetTokenInformation.restype = wintypes.BOOL
advapi32.GetTokenInformation.argtypes = [
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]

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
    (config.FIXED_WIN_W/H), and every fixed-coordinate click/search region
    in core.runner assumes screen pixels and "Windows pixels" are the same
    thing -- if this process and Roblox disagree on what a "pixel" is
    (non-100% Windows display scaling), everything drifts: docking sizes
    the game wrong, and clicks land near but not on the right spot (a
    reported bug that only showed up in the packaged exe, on other users'
    machines with scaling other than 100% -- never on a 100%-scale dev
    machine, and never from source).

    The bug: each of these calls can fail (return FALSE / raise) without
    ctypes surfacing that as a Python exception -- a bare call whose return
    value is never checked "succeeds" even when it did nothing. A frozen
    exe is more likely to already have a DPI-awareness mode set by its own
    embedded manifest before this ever runs, which makes
    SetProcessDpiAwarenessContext fail this way (ERROR_ACCESS_DENIED --
    Windows only allows setting it once), and the old code treated that
    failure as success and never tried the fallbacks below. Now every
    attempt's actual result is checked, and get_display_scale_percent()
    (used at startup for logging/diagnostics) tells us whether this
    actually took effect.
    """
    try:
        if user32.SetProcessDpiAwarenessContext(-4):
            return
    except (OSError, AttributeError):
        pass
    try:
        # S_OK == 0 -- this one's a HRESULT, not a BOOL, so success is 0,
        # not truthy.
        if ctypes.WinDLL("shcore").SetProcessDpiAwareness(2) == 0:
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


def disable_mss_captureblt() -> None:
    """Call once at startup, before any screen capture. mss's Windows
    backend grabs the screen with BitBlt(SRCCOPY | CAPTUREBLT), and
    CAPTUREBLT is a known screen-flicker source: every grab forces GDI to
    compose layered (transparent overlay) windows into the copy, which
    briefly redraws them on the real screen. One grab is invisible; this
    app polls a grab every ~0.3s the whole time a macro runs (core.vision's
    wait_for_image), which turned the per-grab blink into a constant white
    flashing for a user running NVIDIA's overlay/recorder (whose overlay is
    exactly such a layered window) on top of the game.

    Zeroing the module-level constant makes every subsequent BitBlt use
    plain SRCCOPY -- mss's own documented workaround for this. The only
    thing lost is capturing layered windows' contents, which is a feature
    here, not a cost: matching wants the game's pixels, never a recording
    overlay drawn on top of them.

    Handles both mss layouts (>=10.2 moved the constant from mss.windows to
    mss.windows.gdi) and swallows everything -- a capture that still
    flickers beats an app that can't start over a patch of someone else's
    internals.
    """
    try:
        import mss.windows
        modules = [mss.windows]
        try:
            from mss.windows import gdi
            modules.append(gdi)
        except ImportError:
            pass
        for mod in modules:
            if getattr(mod, "CAPTUREBLT", None):
                mod.CAPTUREBLT = 0
    except Exception:
        pass


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


def is_process_elevated(hwnd: int) -> bool:
    """Whether hwnd's owning process is running with an elevated
    (Administrator) token. SendInput cannot inject input into a window
    owned by a HIGHER-integrity-level process than the sender's own --
    Windows drops it silently, no exception, no error, which matches
    reports of "finds Play correctly but the click just never registers,
    cursor doesn't even move" exactly: if Roblox ever ends up elevated
    (self-elevated, launched via "Run as administrator", certain anti-
    cheat/launcher setups) while this macro runs as a normal user, every
    SendInput call keeps silently failing no matter how many times a click
    is retried. Returns False (assume not elevated) if the check itself
    fails for any reason -- this only ever backs an advisory log message,
    never a control-flow decision, so a wrong guess here should default to
    NOT warning rather than risk a false alarm."""
    TOKEN_QUERY = 0x0008
    TOKEN_ELEVATION = 20
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == 0:
        return False
    h_process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h_process:
        return False
    try:
        h_token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(h_process, TOKEN_QUERY, ctypes.byref(h_token)):
            return False
        try:
            elevation = wintypes.DWORD()
            size = wintypes.DWORD()
            ok = advapi32.GetTokenInformation(
                h_token, TOKEN_ELEVATION, ctypes.byref(elevation), ctypes.sizeof(elevation), ctypes.byref(size))
            return bool(ok) and bool(elevation.value)
        finally:
            kernel32.CloseHandle(h_token)
    except OSError:
        return False
    finally:
        kernel32.CloseHandle(h_process)


def is_self_elevated() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


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


def get_window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def list_roblox_windows() -> list:
    """Every currently open, standalone Roblox window (same title+process
    check as find_roblox_window, but doesn't stop at the first match) --
    for letting someone with multiple Roblox instances open (alts, several
    accounts) pick which one to dock instead of always getting whichever
    happens to enumerate first. A window that's already docked is reparented
    under the GUI window and hidden, so EnumWindows (top-level only) and the
    IsWindowVisible check both naturally exclude it -- this only ever lists
    ones that AREN'T already attached."""
    results = []

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
            results.append({"hwnd": hwnd, "pid": get_window_pid(hwnd), "title": buf.value})
        return True

    user32.EnumWindows(_enum_proc, 0)
    return results


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


WM_SETICON = 0x0080
ICON_SMALL = 0
ICON_BIG = 1
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040


def set_window_icon(hwnd: int, ico_path: str) -> bool:
    """Sets the titlebar/taskbar icon for a plain (non-frozen) window --
    pywebview's own `icon=` start() param only works on the GTK/QT backends,
    not Windows' EdgeChromium one, so this is done directly via WM_SETICON
    instead. Loaded twice (small/big) since Windows uses different sizes for
    the titlebar vs. Alt-Tab/taskbar and LoadImageW with LR_DEFAULTSIZE only
    picks one size per call."""
    if not os.path.isfile(ico_path):
        return False
    ok = True
    for icon_type, size in ((ICON_SMALL, 16), (ICON_BIG, 32)):
        handle = user32.LoadImageW(None, ico_path, IMAGE_ICON, size, size, LR_LOADFROMFILE)
        if not handle:
            ok = False
            continue
        user32.SendMessageW(hwnd, WM_SETICON, icon_type, handle)
    return ok


def activate_window(hwnd: int) -> bool:
    """Brings hwnd to the foreground -- returns whether it actually worked.

    Every click this app sends goes through SendInput, which is GLOBAL
    synthetic input: it goes to whatever window currently has real OS
    input focus, not to a specific hwnd. If activation silently failed,
    every click after it can miss Roblox entirely and land nowhere (or on
    whatever else has focus) -- this exact symptom ("it finds the button
    correctly, the click just doesn't register") is consistent with plain
    SetForegroundWindow failing, which the old code never checked for
    (same class of bug as core.window.set_dpi_aware's fix).

    Windows deliberately restricts SetForegroundWindow: a call from a
    process that isn't already "in the foreground lineage" can silently
    fail and return FALSE (an anti-annoyance measure so background apps
    can't just steal focus at will) -- extremely plausible for a docked-
    window automation app clicking programmatically rather than through a
    real user-initiated click. The standard, reliable workaround:
    temporarily attach this thread's input queue to both the CURRENT
    foreground window's thread and hwnd's own thread, which makes Windows
    treat the call as if it came from an already-foreground process
    (always allowed), then detach again either way.
    """
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    if user32.SetForegroundWindow(hwnd):
        return True

    current_thread = kernel32.GetCurrentThreadId()
    fg_hwnd = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)

    attached_fg = attached_target = False
    if fg_thread and fg_thread != current_thread:
        attached_fg = bool(user32.AttachThreadInput(current_thread, fg_thread, True))
    if target_thread and target_thread != current_thread:
        attached_target = bool(user32.AttachThreadInput(current_thread, target_thread, True))
    try:
        ok = bool(user32.SetForegroundWindow(hwnd))
    finally:
        if attached_fg:
            user32.AttachThreadInput(current_thread, fg_thread, False)
        if attached_target:
            user32.AttachThreadInput(current_thread, target_thread, False)
    return ok


def move_window(hwnd: int, x: int, y: int, w: int, h: int) -> None:
    user32.MoveWindow(hwnd, x, y, w, h, True)


def bring_to_top(hwnd: int) -> None:
    user32.SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)


def place_topmost(hwnd: int, x: int, y: int, w: int, h: int) -> None:
    """Position + promote into the TOPMOST band in one call, no activation
    -- cutout mode's 'show': the game floats over the GUI's game slot.
    c_void_p wrapper for the same 64-bit pseudo-handle reason as
    set_always_on_top."""
    user32.SetWindowPos(hwnd, ctypes.c_void_p(HWND_TOPMOST), x, y, w, h, SWP_NOACTIVATE)


def send_to_bottom(hwnd: int) -> None:
    """Drop out of the topmost band AND to the bottom of the z-order --
    cutout mode's 'hide': everything (the GUI included) covers the game,
    which keeps rendering for window-content captures."""
    user32.SetWindowPos(hwnd, ctypes.c_void_p(HWND_NOTOPMOST), 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    HWND_BOTTOM = 1
    user32.SetWindowPos(hwnd, ctypes.c_void_p(HWND_BOTTOM), 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def set_always_on_top(hwnd: int, on: bool = True) -> None:
    # The pseudo-handles are NEGATIVE (-1/-2), and ctypes without argtypes
    # passes a bare Python -1 as a 32-bit int into the 64-bit
    # hWndInsertAfter slot -- it arrives as 0xFFFFFFFF, an invalid handle,
    # and SetWindowPos just returns 0. Wrapping in c_void_p produces the
    # proper pointer-sized -1. (Latent since this helper was written;
    # cutout mode is its first real caller.)
    flag = ctypes.c_void_p(HWND_TOPMOST if on else HWND_NOTOPMOST)
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
