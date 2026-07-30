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


def tilt_camera_top_down(mouse, hwnd) -> None:
    """Pin the Roblox camera pitch without changing its zoom.

    Caller owns the focus dance. Relative moves are required because Roblox
    consumes raw mouse deltas while the right button is held and recenters the
    hidden cursor every frame.
    """
    left, top, right, bottom = wm.get_window_rect_screen(hwnd)
    cx, cy = (left + right) // 2, (top + bottom) // 2
    mouse.move_to(cx, cy)
    time.sleep(0.15)
    mouse.nudge()  # force a real hover event before the click lands
    time.sleep(0.05)

    mouse.down("right")
    try:
        time.sleep(0.08)
        # Far more total downward travel than any camera needs to pin fully
        # down -- past the floor the extra deltas are no-ops, so overshooting
        # is free and saves needing to know the exact sensitivity/pitch-range.
        for _ in range(40):
            mouse.nudge(0, 80)
            time.sleep(0.012)
        time.sleep(0.08)
    finally:
        # If a nudge() ever raises mid-drag, an unguarded mouse.up("right")
        # below it would never run and leave the right button physically
        # held down for the rest of the run -- every later mouse move would
        # then read to Roblox as an active camera-rotate drag instead of a
        # normal, unlocked cursor move (the same "holding right click keeps
        # the mouse from locking" symptom this helper exists to produce).
        mouse.up("right")
    time.sleep(0.15)


def run_camera_setup(mouse, keyboard, hwnd, hold_ms: float = 2000) -> None:
    """Pin the pitch, then hold O for the standard maximum zoom-out."""
    tilt_camera_top_down(mouse, hwnd)

    keyboard.key_down(ord("O"))
    try:
        time.sleep(max(0.0, hold_ms) / 1000)
    finally:
        keyboard.key_up(ord("O"))


def run_camera_drag_hold(mouse, keyboard, hwnd, hold_ms: float = 2500, o_tap_ms: float = 0) -> None:
    """The same right-click drag-straight-down pitch pin as
    run_camera_setup, but followed by holding the LEFT ARROW key for
    hold_ms (a camera rotate) instead of the O zoom-hold -- then, if
    o_tap_ms > 0, a short O press for that long (a small zoom step, not
    the full 2s zoom-out). This is EXPEDITION's Pre Start camera setup
    (730ms rotate + 100ms O -- the standard sequence doesn't frame
    Expedition maps right, see core.runner's _run_prestart); Settings >
    Debug > "Camera Setup 3" runs the rotate part on demand with any hold
    time for tuning. Same relative-move drag mechanics and same
    held-input-released-in-finally safety as run_camera_setup above."""
    from . import keys

    tilt_camera_top_down(mouse, hwnd)

    keyboard.key_down(keys.VK_LEFT)
    try:
        time.sleep(max(0.0, hold_ms) / 1000)
    finally:
        keyboard.key_up(keys.VK_LEFT)

    if o_tap_ms > 0:
        time.sleep(0.1)
        keyboard.key_down(ord("O"))
        try:
            time.sleep(max(0.0, o_tap_ms) / 1000)
        finally:
            keyboard.key_up(ord("O"))
