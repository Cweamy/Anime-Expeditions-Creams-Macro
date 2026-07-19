"""Picks a Story map card by image-matching a small reference crop of its
name label -- OCR was tried first but was too slow in practice (a multi-
mask/multi-psm Tesseract sweep per card, repeated on every scroll nudge,
added up fast). Each map gets its own reference image instead
(Assets/maps/<map name>.png, named to match a Task's `map` field exactly --
this folder covers Raid maps too, e.g. "Spirit City", not just Story ones)
and is found the same way nav_play/nav_back are: grayscale template
matching against a live capture (see core.vision).
"""
import time

from . import vision
from . import window as wm

# Spans the whole card row's name-label strip at once (measured off the
# Story screen, debug/debug_screenshot.png) -- whichever of the 3 visible
# cards holds the target map, its label falls somewhere in this one wide
# band, so there's no need to track each card's x position separately.
NAME_BAND_REGION = (0, 463, 1152, 30)

MATCH_THRESHOLD = 0.78

SCROLL_CENTER = (576, 390)        # middle of the card row -- where the wheel-scroll is aimed
DEFAULT_SCROLL_POWER = 3          # multiplier on one wheel notch -- the carousel barely moved at 1x
SCROLL_NUDGES_PER_PASS = 8        # how many forward nudges before giving up on this pass and resetting
SCROLL_RESET_NOTCHES = 20         # scrolled back this many (power-scaled) notches -- far more than any real list needs, to guarantee hitting the start
SETTLE_DELAY = 0.35               # lets the carousel's scroll animation actually finish before the next capture
MAX_PASSES = 3


def find_and_click_map(mouse, hwnd, map_name: str, log, stop_event=None, scroll_power: int = DEFAULT_SCROLL_POWER,
                        scroll_nudges: int = SCROLL_NUDGES_PER_PASS, debug_screenshots: bool = False) -> bool:
    """Scans the Story map carousel for map_name (image-matched against
    Assets/maps/<map_name>.png over the whole name-label band) and
    clicks it once found.

    If it's not among the 3 currently-visible cards, nudges the carousel
    forward with the scroll wheel and re-checks -- up to `scroll_nudges`
    times -- before scrolling all the way back to the start and running the
    whole pass again, up to MAX_PASSES times total. Resetting to a known
    position (the very start) rather than just scrolling further is what
    makes a pass recoverable if a nudge ever lands mid-animation and a check
    gets missed -- forever scrolling forward has no way to correct for that.
    """
    scroll_step = -120 * max(1, scroll_power)
    scroll_nudges = max(0, scroll_nudges)
    left, top, _, _ = wm.get_window_rect_screen(hwnd)

    def to_screen(pt):
        return left + pt[0], top + pt[1]

    for attempt in range(1, MAX_PASSES + 1):
        if stop_event is not None and stop_event.is_set():
            return False
        log(f'[Macro] Looking for map "{map_name}" (pass {attempt}/{MAX_PASSES}, up to {scroll_nudges} scrolls)...')
        for nudge in range(scroll_nudges + 1):
            if stop_event is not None and stop_event.is_set():
                return False
            try:
                match = vision.find_image(
                    hwnd, map_name, region=NAME_BAND_REGION, threshold=MATCH_THRESHOLD,
                    template_dir=vision.MAPS_DIR)
            except vision.TemplateNotFound as exc:
                log(f"[Macro] {exc}")
                return False
            if match is not None:
                debug_path = vision.save_match_debug(hwnd, map_name, match) if debug_screenshots else None
                suffix = f" Debug: {debug_path}" if debug_path else ""
                log(f'[Macro] Found "{map_name}" (score {match["score"]:.2f}) -- clicking it.{suffix}')
                vision.click_match(mouse, hwnd, match)
                return True
            if nudge < scroll_nudges:
                mouse.move_to(*to_screen(SCROLL_CENTER))
                mouse.scroll(scroll_step)
                time.sleep(SETTLE_DELAY)
        log("[Macro] Not found in this pass -- scrolling back to the start.")
        mouse.move_to(*to_screen(SCROLL_CENTER))
        for _ in range(SCROLL_RESET_NOTCHES):
            mouse.scroll(-scroll_step)
        time.sleep(SETTLE_DELAY)

    log(f'[Macro] Couldn\'t find map "{map_name}" after {MAX_PASSES} passes -- stopping.')
    return False
