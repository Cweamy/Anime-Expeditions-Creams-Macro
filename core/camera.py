"""Puts the Roblox camera into the standard macro viewpoint: right-click drag
straight down until the pitch pins at its floor (top-down view), then hold O
for 2s so the scroll-out zoom reaches max.

Shared by Settings > Debug > "Camera Setup" (main.Api.debug_camera_setup,
on demand) and the macro run's Pre Start step (core.runner, automatically
before every match) -- both need the exact same sequence, so it lives here
once instead of twice.
"""
import time

from . import window as wm


def run_camera_setup(mouse, keyboard, hwnd, hold_ms: float = 2000) -> None:
    """Blocking -- takes ~(1s drag + hold_ms). Caller is responsible for the
    focus dance (wm.show_window/activate_window) beforehand; this only does
    the actual drag + zoom-hold, same as every other input-sending routine
    in core/. hold_ms is how long O is held for the zoom-out -- 2000 by
    default (the standard macro viewpoint), overridable for Settings >
    Debug > "Camera Setup 2" to test other hold times.

    The drag uses *relative* SendInput moves (Mouse.nudge), not absolute
    repositioning: with right-click held, Roblox rotates the camera from raw
    mouse deltas and recenters the (locked, hidden) cursor every frame, so an
    absolute move_to jump wouldn't register as rotation at all.
    """
    left, top, right, bottom = wm.get_window_rect_screen(hwnd)
    cx, cy = (left + right) // 2, (top + bottom) // 2
    mouse.move_to(cx, cy)
    time.sleep(0.15)
    mouse.nudge()  # force a real hover event before the click lands
    time.sleep(0.05)

    mouse.down("right")
    time.sleep(0.08)
    # Far more total downward travel than any camera needs to pin fully
    # down -- past the floor the extra deltas are no-ops, so overshooting is
    # free and saves needing to know the exact sensitivity/pitch-range.
    for _ in range(40):
        mouse.nudge(0, 80)
        time.sleep(0.012)
    time.sleep(0.08)
    mouse.up("right")
    time.sleep(0.15)

    keyboard.key_down(ord("O"))
    time.sleep(max(0.0, hold_ms) / 1000)
    keyboard.key_up(ord("O"))
