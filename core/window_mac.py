"""macOS implementation of core.window's API (see core/window.py for how
the dispatch works, core/window_win.py for the Win32 original each
function mirrors).

A "window handle" here is a CGWindowID (the integer the window server
uses) so the int-shaped hwnd plumbing through main/runner/dock keeps
working unchanged. Two macOS realities shape everything below:

1. READING window state (enumerate, rects, z-order, capture) is easy and
   permissionless-ish via Quartz's CGWindowList APIs (capture needs the
   Screen Recording permission).

2. CHANGING another app's windows (move/resize/raise) is only possible
   through the Accessibility API (AXUIElement), which requires the user
   to grant this app Accessibility permission in System Settings >
   Privacy & Security. Every AX call here is best-effort: without the
   permission it fails quietly and the macro logs/limps rather than
   crashing -- ax_trusted() is surfaced at startup so the user gets told
   exactly what to enable.

There is deliberately NO SetParent equivalent: macOS cannot reparent
another process's window, period. set_parent/get_parent/remove_borders/
restore_borders exist as no-op stubs purely so core.dock's shared call
sites don't need per-platform guards -- the darwin GameDocker (see
core/dock.py) arranges windows side by side instead of embedding.

UNTESTED-ON-REAL-MAC NOTE for testers: pyobjc attribute names and the
title-bar height estimate (_TITLEBAR_PT) are the two most likely things
to need a first-run fix. debug.log will show any AX/Quartz exception
with a [window_mac] prefix.
"""
import os

import Quartz
from AppKit import NSRunningApplication, NSApplicationActivateIgnoringOtherApps
try:
    # AX* symbols live in the ApplicationServices/HIServices bindings.
    from ApplicationServices import (
        AXIsProcessTrusted, AXUIElementCreateApplication, AXUIElementCopyAttributeValue,
        AXUIElementSetAttributeValue, AXUIElementPerformAction, AXValueCreate, AXValueGetValue,
        kAXWindowsAttribute, kAXPositionAttribute, kAXSizeAttribute, kAXRaiseAction,
        kAXValueCGPointType, kAXValueCGSizeType,
    )
    _HAVE_AX = True
except Exception:  # pragma: no cover -- older pyobjc layouts; AX features degrade to no-ops
    _HAVE_AX = False

from . import config

ROBLOX_PROCESS_NAME = "robloxplayer"  # matched case-insensitively against the owning app's name

# macOS window bounds (kCGWindowBounds) are the OUTER frame including the
# title bar; the game's client/content area -- what every fixed coordinate
# and capture in this app is defined against -- starts below it. Standard
# macOS title bars are 28pt; if Roblox's differs on some version, clicks/
# captures will all be vertically offset by the same amount, which is the
# first thing to calibrate on a real Mac (testers: adjust this constant).
_TITLEBAR_PT = 28

# CGWindowID -> owning pid, filled by the enumeration helpers so the AX
# calls (which are per-APPLICATION, keyed by pid) can find their way back
# from the window id the rest of the app passes around.
_window_pids = {}


def _log(msg: str) -> None:
    # Lightweight breadcrumb into stderr -- ends up in debug.log via the
    # app's logger redirection; window-layer failures on an untested
    # platform must never be silent.
    print(f"[window_mac] {msg}")


def ax_trusted() -> bool:
    """Whether the Accessibility permission is granted -- without it every
    move/resize/raise below silently does nothing. Surfaced at startup by
    main so the user is told to enable it instead of watching windows not
    move."""
    if not _HAVE_AX:
        return False
    try:
        return bool(AXIsProcessTrusted())
    except Exception:
        return False


def _window_list(on_screen_only: bool = True):
    opts = Quartz.kCGWindowListExcludeDesktopElements
    if on_screen_only:
        opts |= Quartz.kCGWindowListOptionOnScreenOnly
    return Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []


def _window_info(window_id: int):
    for info in _window_list(on_screen_only=False):
        if info.get(Quartz.kCGWindowNumber) == window_id:
            return info
    return None


def _is_roblox_info(info) -> bool:
    owner = (info.get(Quartz.kCGWindowOwnerName) or "").lower()
    # Layer 0 = normal windows -- skips Roblox's menu bar/status items.
    return ROBLOX_PROCESS_NAME in owner and info.get(Quartz.kCGWindowLayer, 0) == 0


def find_roblox_window() -> int:
    for info in _window_list():
        if _is_roblox_info(info):
            win_id = int(info.get(Quartz.kCGWindowNumber) or 0)
            _window_pids[win_id] = int(info.get(Quartz.kCGWindowOwnerPID) or 0)
            return win_id
    return 0


def list_roblox_windows() -> list:
    results = []
    for info in _window_list():
        if _is_roblox_info(info):
            win_id = int(info.get(Quartz.kCGWindowNumber) or 0)
            pid = int(info.get(Quartz.kCGWindowOwnerPID) or 0)
            _window_pids[win_id] = pid
            results.append({"hwnd": win_id, "pid": pid, "title": info.get(Quartz.kCGWindowName) or "Roblox"})
    return results


def get_window_pid(window_id: int) -> int:
    pid = _window_pids.get(window_id)
    if pid:
        return pid
    info = _window_info(window_id)
    if info:
        pid = int(info.get(Quartz.kCGWindowOwnerPID) or 0)
        _window_pids[window_id] = pid
    return pid or 0


def is_window(window_id: int) -> bool:
    # Full list, not just on-screen: a minimized window must still count
    # as existing (mirrors Win32 IsWindow vs IsWindowVisible).
    return _window_info(window_id) is not None


def is_window_visible(window_id: int) -> bool:
    info = _window_info(window_id)
    return bool(info and info.get(Quartz.kCGWindowIsOnscreen))


def get_window_rect_screen(window_id: int):
    """CONTENT rect (title bar excluded -- see _TITLEBAR_PT) in global
    display points, as (left, top, right, bottom) to match the Win32
    signature. Every coordinate the app computes against this is therefore
    game-viewport-relative, same convention as Windows' borderless docked
    child."""
    info = _window_info(window_id)
    if not info:
        return 0, 0, 0, 0
    b = info.get(Quartz.kCGWindowBounds) or {}
    left = int(b.get("X", 0))
    top = int(b.get("Y", 0)) + _TITLEBAR_PT
    right = left + int(b.get("Width", 0))
    bottom = int(b.get("Y", 0)) + int(b.get("Height", 0))
    return left, top, right, bottom


def _ax_first_window(window_id: int):
    """The AXUIElement for this pid's first window. Matching a specific
    CGWindowID to its AX element has no public API -- Roblox only ever has
    one real window, so "the app's first AX window" is the practical
    stand-in. Returns None (logged) without Accessibility permission."""
    if not _HAVE_AX:
        return None
    pid = get_window_pid(window_id)
    if not pid:
        return None
    try:
        app = AXUIElementCreateApplication(pid)
        err, windows = AXUIElementCopyAttributeValue(app, kAXWindowsAttribute, None)
        if err != 0 or not windows:
            _log(f"AX window lookup failed (err {err}) -- is Accessibility permission granted?")
            return None
        return windows[0]
    except Exception as exc:
        _log(f"AX window lookup raised: {exc}")
        return None


def move_window(window_id: int, x: int, y: int, w: int, h: int) -> None:
    """Move/resize the OUTER frame (x/y/w/h in global points) via AX --
    best-effort, see the module docstring's permission note."""
    win = _ax_first_window(window_id)
    if win is None:
        return
    try:
        point = Quartz.CGPoint(x, y)
        size = Quartz.CGSize(w, h)
        AXUIElementSetAttributeValue(win, kAXPositionAttribute, AXValueCreate(kAXValueCGPointType, point))
        AXUIElementSetAttributeValue(win, kAXSizeAttribute, AXValueCreate(kAXValueCGSizeType, size))
    except Exception as exc:
        _log(f"AX move/resize raised: {exc}")


def bring_to_top(window_id: int) -> None:
    win = _ax_first_window(window_id)
    if win is None:
        return
    try:
        AXUIElementPerformAction(win, kAXRaiseAction)
    except Exception as exc:
        _log(f"AX raise raised: {exc}")


def activate_window(window_id: int) -> bool:
    """Bring the owning app frontmost + raise the window. Input on macOS
    (like SendInput on Windows) goes to the focused app, so this matters
    before every click burst, same as the Win32 version."""
    pid = get_window_pid(window_id)
    if not pid:
        return False
    try:
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is None:
            return False
        ok = bool(app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps))
        bring_to_top(window_id)
        return ok
    except Exception as exc:
        _log(f"activate raised: {exc}")
        return False


def is_foreground(window_id: int) -> bool:
    try:
        from AppKit import NSWorkspace
        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        return bool(front and front.processIdentifier() == get_window_pid(window_id))
    except Exception:
        return False


# ── Visibility: macOS can't hide another app's window the way ShowWindow
# (SW_HIDE) does. On Windows hide/show exist purely because the DOCKED
# child would paint over the app's own non-game screens -- on mac the game
# sits BESIDE the panel (see core/dock.py), never over it, so these are
# honest no-ops rather than emulations (minimizing would stop the game
# rendering, breaking captures -- worse than doing nothing). ──

def hide_window(window_id: int) -> None:
    pass


def show_window(window_id: int) -> None:
    pass


def capture_window_rgb(window_id: int):
    """The window's own rendered contents even if covered -- mac's
    CGWindowListCreateImage is the direct PrintWindow analog. Needs the
    Screen Recording permission (macOS prompts on first use). Returns
    (rgb_bytes, width, height) at the image's native (Retina = 2x) pixel
    size, or None -- callers already resize captures to reference
    dimensions (see core.vision), so the 2x density is absorbed there."""
    try:
        image = Quartz.CGWindowListCreateImage(
            Quartz.CGRectNull, Quartz.kCGWindowListOptionIncludingWindow, window_id,
            Quartz.kCGWindowImageBoundsIgnoreFraming)
        if image is None:
            return None
        w = Quartz.CGImageGetWidth(image)
        h = Quartz.CGImageGetHeight(image)
        if w <= 0 or h <= 0:
            return None
        bpr = Quartz.CGImageGetBytesPerRow(image)
        data = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(image))
        raw = bytes(data)
        rgb = bytearray(w * h * 3)
        # BGRA rows padded to bytesPerRow -- walk rows explicitly.
        for row in range(h):
            src = row * bpr
            dst = row * w * 3
            line = raw[src:src + w * 4]
            rgb[dst + 0::3] = line[2::4]
            rgb[dst + 1::3] = line[1::4]
            rgb[dst + 2::3] = line[0::4]
        return bytes(rgb), w, h
    except Exception as exc:
        _log(f"capture raised: {exc} -- is Screen Recording permission granted?")
        return None


def get_process_name(window_id: int) -> str:
    try:
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(get_window_pid(window_id))
        return (app.localizedName() or "").lower() if app else ""
    except Exception:
        return ""


# ── Win32-shaped stubs: keep shared call sites platform-blind. ──

def set_dpi_aware() -> None:
    pass  # macOS handles scaling per-window; Retina is a capture-side concern (core.vision)


def disable_mss_captureblt() -> None:
    pass  # CAPTUREBLT is a Windows GDI flag; mss's mac backend has no equivalent flicker


def get_display_scale_percent() -> int:
    # Always 100: coordinates here are logical points end to end, so the
    # Windows-specific "display scale breaks fixed coordinates" warning
    # never applies (Retina density is normalized at capture time instead).
    return 100


def get_screen_size():
    bounds = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
    return int(bounds.size.width), int(bounds.size.height)


def set_window_icon(window_id: int, ico_path: str) -> bool:
    return False  # pywebview's Cocoa backend owns the app icon on mac


def is_process_elevated(window_id: int) -> bool:
    return False  # the Win32 SendInput-vs-elevation trap has no mac equivalent


def is_self_elevated() -> bool:
    return False


def get_parent(window_id: int) -> int:
    return 0  # no reparenting on macOS -- always standalone


def set_parent(child: int, parent: int) -> bool:
    return True  # honest no-op: "already standalone" is the only state


def remove_borders(window_id: int) -> None:
    pass  # can't strip another app's title bar on macOS; _TITLEBAR_PT compensates instead


def restore_borders(window_id: int) -> None:
    pass


def set_always_on_top(window_id: int, on: bool = True) -> None:
    pass  # would need AX tricks with poor support -- the arranger re-raises instead


def client_size_to_window_size(window_id: int, width: int, height: int):
    # Outer frame for a desired CONTENT size -- just the title bar to add.
    return width, height + _TITLEBAR_PT


class WindowManager:
    """Same shape as window_win.WindowManager -- locate a window by title
    (or, for Roblox, by owning app) and do coordinate/resize helpers."""

    def __init__(self, title_substring: str = config.ROBLOX_WINDOW_TITLE):
        self.title_substring = title_substring
        self.hwnd = None

    def find(self):
        wanted = self.title_substring.lower()
        for info in _window_list():
            if info.get(Quartz.kCGWindowLayer, 0) != 0:
                continue
            title = (info.get(Quartz.kCGWindowName) or "").lower()
            owner = (info.get(Quartz.kCGWindowOwnerName) or "").lower()
            if wanted in title or wanted in owner:
                self.hwnd = int(info.get(Quartz.kCGWindowNumber) or 0)
                _window_pids[self.hwnd] = int(info.get(Quartz.kCGWindowOwnerPID) or 0)
                return self.hwnd
        self.hwnd = None
        return None

    def _require_hwnd(self):
        if not self.hwnd:
            raise RuntimeError("No window handle, call find() first (and check it returned non-None)")
        return self.hwnd

    def get_window_rect(self):
        return get_window_rect_screen(self._require_hwnd())

    def get_client_size(self):
        left, top, right, bottom = get_window_rect_screen(self._require_hwnd())
        return right - left, bottom - top

    def client_to_screen(self, x: int, y: int):
        left, top, _, _ = get_window_rect_screen(self._require_hwnd())
        return left + x, top + y

    def resize_client_to(self, width: int = config.FIXED_WIN_W, height: int = config.FIXED_WIN_H) -> None:
        window_id = self._require_hwnd()
        info = _window_info(window_id)
        if not info:
            return
        b = info.get(Quartz.kCGWindowBounds) or {}
        outer_w, outer_h = client_size_to_window_size(window_id, width, height)
        move_window(window_id, int(b.get("X", 0)), int(b.get("Y", 0)), outer_w, outer_h)

    def is_client_size_correct(self, width: int = config.FIXED_WIN_W, height: int = config.FIXED_WIN_H) -> bool:
        return self.get_client_size() == (width, height)

    def bring_to_front(self) -> None:
        activate_window(self._require_hwnd())
