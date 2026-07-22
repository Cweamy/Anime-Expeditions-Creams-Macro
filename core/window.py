"""Platform dispatcher for the window-management layer -- every caller
keeps importing `core.window` exactly as before; which implementation
actually loads depends on the OS:

  window_win.py -- the original Win32 implementation (ctypes/user32):
    docking via SetParent, PrintWindow capture, DPI awareness, etc.

  window_mac.py -- the macOS implementation (pyobjc/Quartz/Accessibility):
    same function-level API, but a "window handle" is a CGWindowID and
    everything SetParent-shaped is stubbed -- macOS cannot reparent
    another app's window at all, so docking is replaced by side-by-side
    arrangement (see core.dock's darwin branch).

Split as two full modules rather than if/else per function because the
Win32 module runs ctypes.WinDLL loads at import time (crashes outright on
mac) and shares Win32-only structures across its functions -- a clean
per-OS module each beats a module interleaved with platform guards.
"""
import sys

if sys.platform == "darwin":
    from .window_mac import *  # noqa: F401,F403
    from .window_mac import WindowManager  # noqa: F401 -- explicit for IDEs/linters
else:
    from .window_win import *  # noqa: F401,F403
    from .window_win import WindowManager  # noqa: F401
