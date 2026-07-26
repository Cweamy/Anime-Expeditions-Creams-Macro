"""Platform dispatcher for the window-management layer.

Callers can import core.window directly or use get_window_manager().
The implementation loaded depends on the operating system:
- window_win.py for Windows (ctypes/user32)
- window_mac.py for macOS (pyobjc/Quartz/Accessibility)
"""
import abc
import sys
from typing import Optional, Tuple, Any


class BaseWindowManager(abc.ABC):
    """Abstract base class for platform-specific window managers."""

    def __init__(self, title_substring: Optional[str] = None):
        self.title_substring = title_substring
        self.hwnd = None

    @abc.abstractmethod
    def find_window(self) -> Optional[Any]:
        """Locate the target window and return its handle."""
        pass

    @abc.abstractmethod
    def activate(self) -> None:
        """Bring the target window to the foreground."""
        pass

    @abc.abstractmethod
    def get_rect(self) -> Tuple[int, int, int, int]:
        """Return the window rect as (left, top, right, bottom)."""
        pass

    @abc.abstractmethod
    def set_bounds(self, x: int, y: int, width: int, height: int) -> None:
        """Set the window position and dimensions."""
        pass


def get_window_manager(title_substring: Optional[str] = None) -> BaseWindowManager:
    """Factory function returning the platform-specific WindowManager instance."""
    kwargs = {} if title_substring is None else {"title_substring": title_substring}
    if sys.platform == "darwin":
        from .window_mac import MacWindowManager
        return MacWindowManager(**kwargs)
    from .window_win import WindowsWindowManager
    return WindowsWindowManager(**kwargs)


def __getattr__(name: str) -> Any:
    if sys.platform == "darwin":
        from . import window_mac as _mod
    else:
        from . import window_win as _mod
    if hasattr(_mod, name):
        return getattr(_mod, name)
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
