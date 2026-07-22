"""Global action pacing -- the user-adjustable "slow the macro down" knob
(Settings > General > Macro Speed).

The macro's default click/keypress cadence was tuned on fast machines and
reported as too fast for some setups (clicks landing before the game UI
had actually processed the previous one, especially on lower-end PCs and
laptops). Rather than sprinkle per-site tweakable sleeps through the
runner (dozens of sites, each its own bikeshed), ONE extra delay is
applied at the input choke points every action already flows through:
Mouse.click/double_click/shuffle_click and Keyboard.tap/combo (see those
modules). 0ms (the default) is exactly the old behavior.

Module-level, not per-instance: the Mouse/Keyboard objects are created in
several places (runner, main's diagnostics) and the setting should govern
all of them without threading a value through every constructor. Reads and
writes are a single float assignment -- atomic under the GIL, so no lock
is needed for the runner thread reading while the UI thread updates it.
"""
import time

_action_delay_s = 0.0


def set_action_delay_ms(ms) -> None:
    """Called from main.Api when the setting changes (and once at startup
    with the persisted value). Clamped so a corrupt/hand-edited settings
    value can't freeze every click behind a multi-minute sleep."""
    global _action_delay_s
    try:
        _action_delay_s = min(2000, max(0, int(ms))) / 1000.0
    except (TypeError, ValueError):
        _action_delay_s = 0.0


def get_action_delay_ms() -> int:
    return int(_action_delay_s * 1000)


def action_pause() -> None:
    """The extra post-action breather -- a no-op at the default 0ms, so the
    fast path costs one float compare."""
    if _action_delay_s > 0:
        time.sleep(_action_delay_s)
