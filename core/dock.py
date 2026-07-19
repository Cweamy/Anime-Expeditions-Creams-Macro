import threading
import time

from . import window as wm
from . import config


class GameDocker:
    """Embeds the Roblox window as a borderless child inside our GUI window
    (same technique used in the Anime Squadron macro): Roblox renders
    directly inside the app instead of sitting in a separate window.

    dock()/undock() share a lock: Windows destroys child windows when their
    parent is destroyed, so if the app quits while a background thread is
    mid-dock, undock() must wait for that in-flight dock() to finish (not
    race it) before it can safely cut Roblox loose.
    """

    def __init__(self):
        self.docked = False
        self._lock = threading.Lock()

    def dock(self, game_hwnd: int, gui_hwnd: int, x: int = 0, y: int = 0,
              width: int = config.FIXED_WIN_W, height: int = config.FIXED_WIN_H) -> None:
        if not game_hwnd or not wm.is_window(game_hwnd):
            return

        with self._lock:
            if not self.docked:
                wm.remove_borders(game_hwnd)
                time.sleep(0.05)
                wm.set_parent(game_hwnd, gui_hwnd)
                self.docked = True
                time.sleep(0.1)

            wm.move_window(game_hwnd, x, y, width, height)
            wm.bring_to_top(game_hwnd)

    def undock(self, game_hwnd: int, x: int = 100, y: int = 100,
               width: int = config.FIXED_WIN_W, height: int = config.FIXED_WIN_H) -> bool:
        """Returns whether Roblox was actually confirmed detached. Callers that
        are about to destroy the GUI window (main.py's close_window()/
        on_closing()) rely on this being true: Windows cascades WM_DESTROY to
        any window still parented under the one being destroyed, so a
        reparent that silently failed would take Roblox down with it."""
        with self._lock:
            if not self.docked:
                return True  # never docked (or already undocked): leave Roblox exactly as it is
            if not game_hwnd or not wm.is_window(game_hwnd):
                self.docked = False
                return True

            detached = wm.set_parent(game_hwnd, 0) and wm.get_parent(game_hwnd) == 0
            if not detached:
                # SetParent can transiently fail (focus/thread timing); this is
                # the one step that must not be allowed to silently no-op, so
                # retry a few times rather than trusting it on the first try.
                for _ in range(5):
                    time.sleep(0.05)
                    if wm.set_parent(game_hwnd, 0) and wm.get_parent(game_hwnd) == 0:
                        detached = True
                        break

            wm.restore_borders(game_hwnd)
            # Compensate for the restored title bar/border so Roblox's *client*
            # area comes back at the full width/height instead of being a few
            # pixels smaller (and looking clipped/broken): must run after
            # restore_borders(), since it reads the window's current style.
            outer_w, outer_h = wm.client_size_to_window_size(game_hwnd, width, height)
            wm.move_window(game_hwnd, x, y, outer_w, outer_h)
            # The game may currently be HIDDEN (switching to the Settings/
            # Creation screens calls hide_game() -> ShowWindow(SW_HIDE) so the
            # native game window doesn't paint over them). activate_window()
            # only un-minimizes, it does NOT un-hide, so quitting the macro
            # from one of those screens left Roblox detached but permanently
            # invisible. Always show it again on undock.
            wm.show_window(game_hwnd)
            wm.activate_window(game_hwnd)
            self.docked = False
            return detached
