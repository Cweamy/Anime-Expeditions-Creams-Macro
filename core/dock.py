import sys
import threading
import time

from . import window as wm
from . import config


if sys.platform == "darwin":

    class GameDocker:
        """macOS 'docking': arrange, don't embed. macOS flatly cannot
        reparent another app's window (no SetParent equivalent exists), so
        instead of Roblox rendering INSIDE the app like on Windows, the
        game window is resized to the exact same fixed content size
        (config.FIXED_WIN_W/H, in points -- so every fixed coordinate and
        reference image works identically) and parked at the screen
        position main's watchdog asks for, right beside the control
        panel. The watchdog re-calls dock() periodically, which re-asserts
        position/size if the user dragged or resized the game -- the
        arrangement equivalent of a child window that can't drift.

        Same public surface as the Windows docker (dock/undock/docked/
        lock) so main.py's call sites stay platform-blind; x/y here are
        SCREEN coordinates for the game window rather than client offsets
        inside a parent, which is what main's darwin branch passes.
        """

        def __init__(self):
            self.docked = False
            self._lock = threading.Lock()

        def dock(self, game_hwnd: int, gui_hwnd: int, x: int = 0, y: int = 0,
                  width: int = config.FIXED_WIN_W, height: int = config.FIXED_WIN_H) -> None:
            if not game_hwnd or not wm.is_window(game_hwnd):
                return
            with self._lock:
                outer_w, outer_h = wm.client_size_to_window_size(game_hwnd, width, height)
                wm.move_window(game_hwnd, x, y, outer_w, outer_h)
                self.docked = True

        def undock(self, game_hwnd: int, x: int = 100, y: int = 100,
                    width: int = config.FIXED_WIN_W, height: int = config.FIXED_WIN_H) -> bool:
            # Nothing to detach -- the game was never parented to anything.
            # Leave it exactly where it is (moving it away on quit would
            # just be surprising) and report success so close paths never
            # warn about a detach that has no meaning here.
            with self._lock:
                self.docked = False
                return True

else:

    class GameDocker:
        """Embeds the Roblox window as a borderless child inside our GUI window
        (same technique used in the Anime Squadron macro): Roblox renders
        directly inside the app instead of sitting in a separate window.

        CUTOUT mode (self.cutout, the experimental Settings toggle): don't
        reparent at all -- Roblox stays its own top-level window, borderless,
        glued at the game slot's screen position DIRECTLY BELOW the GUI in
        z-order, and the GUI cuts a literal hole in itself over the slot
        (wm.set_window_cutout, driven by main's show_game/hide_game). The
        game shows through the hole and clicks there land on it natively;
        everywhere else the GUI's solid surface occludes it -- which is why
        modals can never be painted over in this mode, and why quitting can
        never take Roblox down with the GUI (nothing is ever parented).

        dock()/undock() share a lock: Windows destroys child windows when their
        parent is destroyed, so if the app quits while a background thread is
        mid-dock, undock() must wait for that in-flight dock() to finish (not
        race it) before it can safely cut Roblox loose.
        """

        def __init__(self):
            self.docked = False
            self.cutout = False  # set by main from the game_cutout setting before first dock
            self._lock = threading.Lock()

        def dock(self, game_hwnd: int, gui_hwnd: int, x: int = 0, y: int = 0,
                  width: int = config.FIXED_WIN_W, height: int = config.FIXED_WIN_H) -> None:
            if not game_hwnd or not wm.is_window(game_hwnd):
                return

            with self._lock:
                if self.cutout:
                    if not self.docked:
                        wm.remove_borders(game_hwnd)
                        time.sleep(0.05)
                        self.docked = True
                    # NOT a literal hole: SetWindowRgn was tried first and the
                    # WebView2 GUI composites via DirectComposition, which
                    # ignores GDI window regions entirely -- the "hole" showed
                    # the page's own background, verified live. Inverted
                    # layering instead, which DComp can't opt out of: the GAME
                    # rides the TOPMOST band, positioned exactly over the game
                    # slot, floating above the (normal-band) GUI -- visually
                    # identical to being embedded. main's show_game/hide_game
                    # promote/demote it; this dock (re-called every watchdog
                    # tick while visible) re-glues position after the GUI is
                    # dragged, NOACTIVATE throughout so ticks never steal
                    # focus.
                    self._gui_hwnd = gui_hwnd
                    gl, gt, _, _ = wm.get_window_rect_screen(gui_hwnd)
                    wm.place_topmost(game_hwnd, gl + x, gt + y, width, height)
                    return

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
                if not game_hwnd or not wm.is_window(game_hwnd):
                    self.docked = False
                    return True
                if self.cutout:
                    # Never parented, so there's nothing whose destruction
                    # could cascade -- drop the game out of the topmost band
                    # and give the window its frame back where it stands.
                    wm.set_always_on_top(game_hwnd, False)
                    wm.restore_borders(game_hwnd)
                    outer_w, outer_h = wm.client_size_to_window_size(game_hwnd, width, height)
                    wm.move_window(game_hwnd, x, y, outer_w, outer_h)
                    wm.show_window(game_hwnd)
                    wm.activate_window(game_hwnd)
                    self.docked = False
                    return True
                if not self.docked and wm.get_parent(game_hwnd) == 0:
                    # Genuinely already standalone -- nothing to do. Checked
                    # against the real OS parent, not just self.docked: a
                    # dock() racing on another thread (e.g. the watchdog mid-
                    # settle) can leave the window actually reparented before
                    # this flag catches up, and trusting the flag alone here
                    # used to leave it silently still parented/hidden with
                    # nothing left tracking it.
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
