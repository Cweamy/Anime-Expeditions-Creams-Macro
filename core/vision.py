"""Finds a known UI element on screen by matching a reference image against a
live screenshot of the docked Roblox window -- what the macro runner (see
core.runner) uses to click things whose position isn't fixed/known in advance
(a menu that slides in, a button that only exists on certain screens) instead
of a hardcoded coordinate.

Reference images live in Assets/ui/<name>.png. Matching is done in grayscale,
not color: this game's UI text sits on a gradient fill (its color shifts
across the glyph) and buttons vary in on-screen brightness with the moment's
lighting/animation, so a 3-channel color match would be comparing exact pixel
color where none is reliably constant. Grayscale keeps the shape/edge
information that *is* consistent, ignores color variation that isn't, and
runs faster (1 channel vs 3) -- OpenCV's matchTemplate normalizes brightness
and contrast per-window anyway (TM_CCOEFF_NORMED), so grayscale doesn't lose
matching power here, it just drops the noisy channel.
"""
import os
import threading
import time

import cv2
import numpy as np

from . import window as wm

UI_ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Assets", "ui")
# Map name-label crops (core.stage_select) -- kept separate from Assets/ui
# since these are keyed by map name (one file per map, named to match a
# Task's `map` field exactly) rather than by fixed UI-element name. Covers
# every map (Story AND Raid, e.g. "Spirit City"), not just Story ones --
# was Assets/story_maps until Raid map crops started living here too. Not
# the same folder as Assets/map/<Category>/ (the Place Unit picker's full
# map preview thumbnails, a completely different, unrelated asset set).
MAPS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Assets", "maps")

# Match-score cutoff (see find_in_gray for which method this is on). Was
# 0.74 -- too permissive: nav_start.png matched the Back button (a visually
# generic grey/green pill with bold white text, same as most of this UI's
# buttons) at a 0.74 score and clicked it. A correct match against this
# game's flat, pixel-consistent UI art normally scores well above 0.9; a
# borderline score like that is a different-but-similar-shaped button, not
# noise to tolerate.
DEFAULT_THRESHOLD = 0.90


class TemplateNotFound(Exception):
    """The reference image (Assets/ui/<name>.png) doesn't exist on disk yet."""


_template_cache = {}


def template_path(name: str, template_dir: str = UI_ASSETS_DIR) -> str:
    return os.path.join(template_dir, f"{name}.png")


def clear_template_cache() -> None:
    """Drops every cached (gray, mask) pair -- call after replacing a
    reference image on disk mid-session, or the runner keeps matching
    against the old cached bytes until the app is restarted."""
    _template_cache.clear()


def load_template_gray(name: str, template_dir: str = UI_ASSETS_DIR) -> tuple:
    """Loads + caches <template_dir>/<name>.png as (grayscale, mask). Cached
    because the runner calls this on every poll of wait_for_image (every
    ~0.3s) -- re-decoding the same PNG off disk that often would be pure
    waste.

    mask is always None right now -- masked/"fuzzy" matching (auto-excluding
    a transparent or flattened-black background from scoring) is disabled.
    It kept producing false positives against completely unrelated art (a
    heavily-masked template matching a random blob on the lobby at a 0.99
    score; a masked search landing on the character/monster art instead of
    an actual button, both seen in testing) -- masking only a PART of a
    template makes the match too permissive about the rest of it, however
    high the reported score looks. Plain, whole-image matching (every pixel
    counts, background included) is less convenient about cropping but
    doesn't have that failure mode. Revisit real masking later if templates
    with transparent/black backgrounds turn out to be worth the risk again.
    """
    cache_key = (template_dir, name)
    if cache_key in _template_cache:
        return _template_cache[cache_key]
    path = template_path(name, template_dir)
    if not os.path.isfile(path):
        raise TemplateNotFound(
            f'No reference image at {path} -- save one there '
            f'(a cropped screenshot of just that button/text) and try again.'
        )
    raw = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if raw is None:
        raise TemplateNotFound(f"{path} exists but couldn't be read as an image.")

    if raw.ndim == 3 and raw.shape[2] == 4:
        gray = cv2.cvtColor(raw[:, :, :3], cv2.COLOR_BGR2GRAY)
    else:
        gray = cv2.cvtColor(raw, cv2.COLOR_BGR2GRAY) if raw.ndim == 3 else raw
    mask = None

    _template_cache[cache_key] = (gray, mask)
    return gray, mask


def capture_game_gray(hwnd: int, region: tuple = None) -> np.ndarray:
    """Grayscale screenshot of the docked Roblox window, or a sub-rect of it
    in client-space coordinates (x, y, w, h) -- e.g. the Nav's Play button
    lives in a small fixed corner, so a caller that already knows roughly
    where to look can pass region to search a much smaller, faster image
    instead of the whole 1152x756 window.

    A plain screen-region grab (not the PrintWindow trick get_roblox_snapshot
    uses) is fine here: the runner only ever calls this while Roblox is the
    actually-visible, docked, foreground game (never from a screen that
    hides it), so there's nothing else that could be captured instead.
    """
    import mss
    left, top, right, bottom = wm.get_window_rect_screen(hwnd)
    if region is not None:
        rx, ry, rw, rh = region
        left, top = left + rx, top + ry
        right, bottom = left + rw, top + rh
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return None
    with mss.MSS() as sct:
        shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
        bgr = np.array(shot)[:, :, :3]
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)


def find_in_gray(haystack_gray: np.ndarray, template_gray: np.ndarray, threshold: float = DEFAULT_THRESHOLD,
                  mask: np.ndarray = None) -> dict:
    """One-shot match of template_gray against haystack_gray. Returns the
    best match's box + center in haystack-local pixel coords, or None if its
    score doesn't clear threshold. haystack smaller than template can't be
    matched (matchTemplate would raise) so that's treated as a clean miss.

    Method depends on whether there's a mask (a background to ignore, see
    load_template_gray): OpenCV only allows a mask with TM_SQDIFF or
    TM_CCORR_NORMED, not the otherwise-preferred TM_CCOEFF_NORMED (which
    additionally normalizes out flat brightness/contrast offsets) -- so
    masked templates use TM_CCORR_NORMED and only score the pixels the mask
    keeps; a plain rectangular template still gets TM_CCOEFF_NORMED.
    """
    th, tw = template_gray.shape[:2]
    hh, hw = haystack_gray.shape[:2]
    if th > hh or tw > hw:
        return None
    if mask is not None:
        result = cv2.matchTemplate(haystack_gray, template_gray, cv2.TM_CCORR_NORMED, mask=mask)
    else:
        result = cv2.matchTemplate(haystack_gray, template_gray, cv2.TM_CCOEFF_NORMED)
    # The normalized methods divide by the local window's own variance --
    # a flat/solid-color patch in the haystack (a loading screen, a frame
    # mid-transition) has zero variance there, so that division is a literal
    # 0/0 or x/0. OpenCV doesn't clamp this, so it surfaces as a genuine inf
    # or NaN "score" that then sails past any real threshold check (inf is
    # >= anything) and gets reported as a confident match against a screen
    # that was never actually showing the target at all. Reject those
    # outright rather than trusting them.
    result[~np.isfinite(result)] = -1
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if max_val < threshold:
        return None
    x, y = max_loc
    return {"x": x, "y": y, "w": tw, "h": th, "cx": x + tw // 2, "cy": y + th // 2, "score": float(max_val)}


DEBUG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "debug")


def save_match_debug(hwnd: int, name: str, match: dict) -> str:
    """Saves a full-window screenshot with the matched box drawn on it (green
    rect + name/score label) to debug/vision_<name>.png -- lets you actually
    SEE what got clicked instead of guessing from the log alone. Especially
    useful when two buttons share the same panel art (e.g. Story vs Raid)
    and a template cropped too loosely around the shared shape can match
    either one with a similar score; the drawn box makes that immediately
    obvious. match must be in full-window coords (what find_image/
    wait_for_image return -- already offset if a region was used)."""
    import mss
    os.makedirs(DEBUG_DIR, exist_ok=True)
    left, top, right, bottom = wm.get_window_rect_screen(hwnd)
    with mss.MSS() as sct:
        shot = sct.grab({"left": left, "top": top, "width": right - left, "height": bottom - top})
        bgr = np.array(shot)[:, :, :3].copy()
    x, y, w, h = match["x"], match["y"], match["w"], match["h"]
    cv2.rectangle(bgr, (x, y), (x + w, y + h), (0, 255, 0), 2)
    cv2.putText(bgr, f"{name} {match['score']:.2f}", (x, max(12, y - 6)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)
    path = os.path.join(DEBUG_DIR, f"vision_{name}.png")
    cv2.imwrite(path, bgr)
    return path


def save_region_debug(hwnd: int, name: str, region: tuple) -> str:
    """Saves a plain screenshot of exactly `region` (no match/box drawn -- see
    save_match_debug for that) to debug/region_<name>.png, so a fixed search
    region can be visually checked/tuned without needing a match to trigger
    first. region is in the game window's own client coords (x, y, w, h)."""
    import mss
    os.makedirs(DEBUG_DIR, exist_ok=True)
    left, top, right, bottom = wm.get_window_rect_screen(hwnd)
    rx, ry, rw, rh = region
    with mss.MSS() as sct:
        shot = sct.grab({"left": left + rx, "top": top + ry, "width": rw, "height": rh})
        bgr = np.array(shot)[:, :, :3]
    path = os.path.join(DEBUG_DIR, f"region_{name}.png")
    cv2.imwrite(path, bgr)
    return path


def find_image(hwnd: int, name: str, region: tuple = None, threshold: float = DEFAULT_THRESHOLD,
                template_dir: str = UI_ASSETS_DIR) -> dict:
    """One-shot: capture + match. Returned x/y/cx/cy are in the SAME space as
    `region` -- region-local if a region was passed, full-window client
    coords otherwise. See click_match to turn that into an actual click."""
    template_gray, mask = load_template_gray(name, template_dir)
    haystack = capture_game_gray(hwnd, region)
    if haystack is None:
        return None
    match = find_in_gray(haystack, template_gray, threshold, mask)
    if match is None:
        return None
    if region is not None:
        match["x"] += region[0]
        match["y"] += region[1]
        match["cx"] += region[0]
        match["cy"] += region[1]
    return match


def find_bottommost_image(hwnd: int, name: str, region: tuple = None, threshold: float = DEFAULT_THRESHOLD,
                           template_dir: str = UI_ASSETS_DIR) -> dict:
    """Like find_image, but instead of the single global-best match, scans
    EVERY location scoring at/above threshold and returns whichever sits
    lowest on screen (largest y).

    Built for warning text (e.g. "You cannot place a unit there!") matched
    over busy, inconsistent gameplay art -- a background that bad can throw
    up more than one coincidental high-scoring spot, and picking the best
    RAW SCORE among those is a coin flip as to whether it's the real message
    or a lookalike patch of art. The real message reliably renders in the
    same lower band of the screen, so "lowest" is a more reliable tie-
    breaker here than "highest score" is.
    """
    template_gray, mask = load_template_gray(name, template_dir)
    haystack = capture_game_gray(hwnd, region)
    if haystack is None:
        return None
    th, tw = template_gray.shape[:2]
    hh, hw = haystack.shape[:2]
    if th > hh or tw > hw:
        return None
    if mask is not None:
        result = cv2.matchTemplate(haystack, template_gray, cv2.TM_CCORR_NORMED, mask=mask)
    else:
        result = cv2.matchTemplate(haystack, template_gray, cv2.TM_CCOEFF_NORMED)
    result[~np.isfinite(result)] = -1

    ys, xs = np.where(result >= threshold)
    if len(ys) == 0:
        return None
    bottom_i = int(np.argmax(ys))
    y, x = int(ys[bottom_i]), int(xs[bottom_i])
    match = {"x": x, "y": y, "w": tw, "h": th, "cx": x + tw // 2, "cy": y + th // 2, "score": float(result[y, x])}
    if region is not None:
        match["x"] += region[0]
        match["y"] += region[1]
        match["cx"] += region[0]
        match["cy"] += region[1]
    return match


def wait_for_image(hwnd: int, name: str, region: tuple = None, threshold: float = DEFAULT_THRESHOLD,
                    timeout: float = 8.0, interval: float = 0.3, stop_event: threading.Event = None,
                    template_dir: str = UI_ASSETS_DIR) -> dict:
    """Polls find_image until it hits, timeout elapses, or stop_event fires
    (checked between polls so a Stop click during a long wait cuts in
    promptly instead of running the full timeout out). Returns the match
    dict (window-space, already offset if region was given) or None.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if stop_event is not None and stop_event.is_set():
            return None
        match = find_image(hwnd, name, region, threshold, template_dir)
        if match is not None:
            return match
        time.sleep(interval)
    return None


def click_match(mouse, hwnd: int, match: dict) -> None:
    """Clicks a match's center -- match coords are window-client-space (what
    find_image/wait_for_image return), so they're offset by the game
    window's own screen position (borderless + docked, so window rect ==
    client rect, same convention core.window.get_roblox_snapshot uses)."""
    left, top, _, _ = wm.get_window_rect_screen(hwnd)
    mouse.click(left + match["cx"], top + match["cy"])
