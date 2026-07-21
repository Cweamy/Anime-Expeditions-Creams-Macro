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

from . import constants
from . import window as wm

UI_ASSETS_DIR = os.path.join(constants.ASSETS_DIR, "ui")
# Map name-label crops (core.stage_select) -- kept separate from Assets/ui
# since these are keyed by map name (one file per map, named to match a
# Task's `map` field exactly) rather than by fixed UI-element name. Covers
# every map (Story AND Raid, e.g. "Spirit City"), not just Story ones --
# was Assets/story_maps until Raid map crops started living here too. Not
# the same folder as Assets/map/<Category>/ (the Place Unit picker's full
# map preview thumbnails, a completely different, unrelated asset set).
MAPS_DIR = os.path.join(constants.ASSETS_DIR, "maps")

# Match-score cutoff (see find_in_gray for which method this is on). Was
# 0.74 -- too permissive: nav_start.png matched the Back button (a visually
# generic grey/green pill with bold white text, same as most of this UI's
# buttons) at a 0.74 score and clicked it. A correct match against this
# game's flat, pixel-consistent UI art normally scores well above 0.9; a
# borderline score like that is a different-but-similar-shaped button, not
# noise to tolerate.
DEFAULT_THRESHOLD = 0.90

# Some setups render this game's UI at a slightly different pixel size than
# whatever a reference image was captured at, even at 100% Windows display
# scale (confirmed against a real report: same 100% scale + a restart on
# both ends, still a visible size mismatch when the two screenshots were
# overlaid) -- Roblox's own UI scaling and per-monitor rendering quirks can
# still drift independently of the OS-level DPI setting the app already
# warns about. 1.0 is tried first and returned on a hit (see
# find_in_gray_multiscale), so the common, correctly-scaled case pays
# nothing extra; this list only gets walked further when 1x genuinely
# misses. Kept to a modest +-10% range -- a real mismatch bigger than that
# has never been reported, and a wider range costs more per miss for no
# observed benefit.
SCALE_FACTORS = (1.0, 0.95, 1.05, 0.90, 1.10)


class TemplateNotFound(Exception):
    """The reference image (Assets/ui/<name>.png) doesn't exist on disk yet."""


_template_cache = {}


def _override_dir(template_dir: str) -> str:
    """Maps a bundled template dir (under constants.ASSETS_DIR) to its
    user-override equivalent (under constants.ASSETS_OVERRIDE_DIR) -- e.g.
    <bundled>/Assets/ui -> <app>/Assets/ui. Returns template_dir itself
    (no separate override location) if it isn't actually under ASSETS_DIR
    for some reason, or if the two roots are the same (source/dev runs,
    see constants.ASSETS_OVERRIDE_DIR)."""
    if constants.ASSETS_OVERRIDE_DIR == constants.ASSETS_DIR:
        return template_dir
    try:
        rel = os.path.relpath(template_dir, constants.ASSETS_DIR)
    except ValueError:
        return template_dir  # different drives on Windows -- can't be relative
    if rel.startswith(".."):
        return template_dir
    return os.path.join(constants.ASSETS_OVERRIDE_DIR, rel)


def template_path(name: str, template_dir: str = UI_ASSETS_DIR) -> str:
    """A user-supplied override (see constants.ASSETS_OVERRIDE_DIR) wins
    over the bundled reference image with the same name -- replacing a
    template that isn't matching well on someone's setup is just "drop a
    same-named .png in the Assets folder next to the exe", no
    rebuild/reinstall needed."""
    override_dir = _override_dir(template_dir)
    if override_dir != template_dir:
        override_path = os.path.join(override_dir, f"{name}.png")
        if os.path.isfile(override_path):
            return override_path
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


def _scaled_template(name: str, template_dir: str, scale: float) -> tuple:
    """The same template as load_template_gray, resized -- cached per
    (dir, name, scale) so a template that keeps missing at 1x doesn't get
    re-resized on every single wait_for_image poll (every ~0.3s)."""
    if scale == 1.0:
        return load_template_gray(name, template_dir)
    cache_key = (template_dir, name, scale)
    if cache_key in _template_cache:
        return _template_cache[cache_key]
    gray, mask = load_template_gray(name, template_dir)
    h, w = gray.shape[:2]
    new_w, new_h = max(1, round(w * scale)), max(1, round(h * scale))
    # INTER_AREA is the recommended choice for shrinking (avoids moire/
    # aliasing on fine text/edges); INTER_LINEAR for enlarging.
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    scaled_gray = cv2.resize(gray, (new_w, new_h), interpolation=interp)
    scaled_mask = cv2.resize(mask, (new_w, new_h), interpolation=cv2.INTER_NEAREST) if mask is not None else None
    entry = (scaled_gray, scaled_mask)
    _template_cache[cache_key] = entry
    return entry


def find_in_gray_multiscale(haystack_gray: np.ndarray, name: str, template_dir: str = UI_ASSETS_DIR,
                             threshold: float = DEFAULT_THRESHOLD) -> dict:
    """find_in_gray, but tries the reference image at a handful of scale
    factors around 1x (see SCALE_FACTORS) instead of only its exact
    captured size -- absorbs a UI that renders slightly bigger/smaller on
    someone else's setup. 1x is tried first and returned immediately on a
    hit, so the common (correctly-scaled) case costs nothing extra; the
    other scales only run when 1x genuinely misses."""
    for scale in SCALE_FACTORS:
        gray, mask = _scaled_template(name, template_dir, scale)
        match = find_in_gray(haystack_gray, gray, threshold, mask)
        if match is not None:
            return match
    return None


DEBUG_DIR = os.path.join(constants.APP_DIR, "debug")


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
    load_template_gray(name, template_dir)  # validates the file exists before capturing anything
    haystack = capture_game_gray(hwnd, region)
    if haystack is None:
        return None
    match = find_in_gray_multiscale(haystack, name, template_dir, threshold)
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


def find_image_any(hwnd: int, names: tuple, region: tuple = None, threshold: float = DEFAULT_THRESHOLD,
                    template_dir: str = UI_ASSETS_DIR):
    """find_image, but for a UI element that renders as one of a few visually
    distinct variants (e.g. nav_play.png vs nav_play_2.png -- same button,
    different art on different setups/game updates) -- same idea as the
    RECONNECT_IMAGE_NAMES / click_anywhere_to_close(_2) variant lists
    core.runner already tries by hand, just reusable and region/threshold-
    aware. Tries each name in `names` in order and returns (match, name) for
    the first one actually found on screen -- NOT the first one that merely
    has a reference image on disk. A name with no reference image yet is
    skipped (same as a caller manually looping and catching TemplateNotFound
    per name), unless every single name in `names` is missing, in which case
    there's nothing to search for at all and the first TemplateNotFound
    propagates. Returns (None, None) if every present template was searched
    for and none matched."""
    first_missing = None
    found_any_template = False
    for name in names:
        try:
            match = find_image(hwnd, name, region, threshold, template_dir)
        except TemplateNotFound as exc:
            if first_missing is None:
                first_missing = exc
            continue
        found_any_template = True
        if match is not None:
            return match, name
    if not found_any_template and first_missing is not None:
        raise first_missing
    return None, None


def wait_for_image_any(hwnd: int, names: tuple, region: tuple = None, threshold: float = DEFAULT_THRESHOLD,
                        timeout: float = 8.0, interval: float = 0.3, stop_event: threading.Event = None,
                        template_dir: str = UI_ASSETS_DIR):
    """wait_for_image, but tries every name in `names` on each poll instead of
    just one -- see find_image_any. Returns (match, name) of whichever
    variant hit first, or (None, None) on timeout/stop."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if stop_event is not None and stop_event.is_set():
            return None, None
        match, name = find_image_any(hwnd, names, region, threshold, template_dir)
        if match is not None:
            return match, name
        time.sleep(interval)
    return None, None


def click_match(mouse, hwnd: int, match: dict) -> None:
    """Clicks a match's center -- match coords are window-client-space (what
    find_image/wait_for_image return), so they're offset by the game
    window's own screen position (borderless + docked, so window rect ==
    client rect, same convention core.window.get_roblox_snapshot uses)."""
    left, top, _, _ = wm.get_window_rect_screen(hwnd)
    mouse.click(left + match["cx"], top + match["cy"])


def double_click_match(mouse, hwnd: int, match: dict) -> None:
    """Same as click_match, but double-clicks -- for buttons that only
    sometimes register a single click reliably (see exp_extract's own
    caller)."""
    left, top, _, _ = wm.get_window_rect_screen(hwnd)
    mouse.double_click(left + match["cx"], top + match["cy"])


def shuffle_click_match(mouse, hwnd: int, match: dict) -> None:
    """Same as click_match, but hovers in with a few small moves first (see
    Mouse.shuffle_click) -- for a button reported not to reliably register
    a click game-side even when the click itself visually lands on it."""
    left, top, _, _ = wm.get_window_rect_screen(hwnd)
    mouse.shuffle_click(left + match["cx"], top + match["cy"])
