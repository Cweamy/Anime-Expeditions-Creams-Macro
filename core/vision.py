"""Finds a known UI element on screen by matching a reference image against a
live screenshot of the docked Roblox window -- what the macro runner (see
core.runner) uses to click things whose position isn't fixed/known in advance
(a menu that slides in, a button that only exists on certain screens) instead
of a hardcoded coordinate.

Reference images live in Assets/ui/<name>/ -- one FOLDER per searched name,
holding one or more .png crops that are all tried as interchangeable variants
of the same button/text (see template_variant_paths). Matching is done in grayscale,
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
import sys
import threading
import time

import cv2
import numpy as np

from . import config
from . import constants
from . import window as wm

# ---------------------------------------------------------------------------
# Reference space: every coordinate in this codebase -- fixed clicks,
# search regions, reference images, saved Place Unit positions -- is
# defined against a game viewport of exactly config.FIXED_WIN_W x
# FIXED_WIN_H (1152x756). On Windows the docker forces the real window to
# exactly that size, so reference == reality. Everywhere that stops being
# true -- macOS Retina screens where a capture comes back at 2x pixel
# density, a mac window the arranger couldn't resize (Accessibility
# permission missing), any future setup with a differently-sized game
# window -- these helpers keep the whole pipeline in reference space:
# captures get resized INTO it (so templates match at their true size on
# any screen), and click positions get scaled back OUT of it (so a match
# found at reference (x, y) lands on the real screen proportionally --
# effectively %-based coordinates). Scale 1.0 (the Windows norm) skips the
# resize entirely, costing one comparison.
# ---------------------------------------------------------------------------

def _window_geometry(hwnd: int):
    """(left, top, sx, sy): the window's screen origin plus its actual
    size as a fraction of reference size. sx/sy fall back to 1.0 for a
    degenerate rect so a mid-close window can't divide by zero."""
    left, top, right, bottom = wm.get_window_rect_screen(hwnd)
    w, h = right - left, bottom - top
    sx = (w / config.FIXED_WIN_W) if w > 0 else 1.0
    sy = (h / config.FIXED_WIN_H) if h > 0 else 1.0
    return left, top, sx, sy


def ref_to_screen(hwnd: int, x: float, y: float):
    """Reference-space point -> absolute screen point, scaled to the
    window's real size. THE way to turn any stored/fixed coordinate into
    a click position."""
    left, top, sx, sy = _window_geometry(hwnd)
    return int(left + x * sx), int(top + y * sy)

UI_ASSETS_DIR = os.path.join(constants.ASSETS_DIR, "ui")
# Map name-label crops (core.stage_select) -- kept separate from Assets/ui
# since these are keyed by map name (one FOLDER per map, named to match a
# Task's `map` field exactly, holding that map's variant crops) rather
# than by fixed UI-element name. Covers
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

# Per-search-name threshold overrides (Settings > General > Image Manager --
# the sensitivity slider on each name). Lets someone lower the bar for a
# button that renders slightly differently on their setup so it still
# matches, or raise it for one that false-matches, WITHOUT editing code or
# touching the global default. Loaded from settings at startup (see main's
# set_image_thresholds) and consulted by the find_* functions below whenever
# the caller didn't pass an explicit threshold.
_name_thresholds = {}


def set_name_thresholds(mapping: dict) -> None:
    """Replace the per-name threshold overrides (main loads these from
    settings). Values outside a sane 0.1-1.0 band are ignored."""
    global _name_thresholds
    clean = {}
    for name, val in (mapping or {}).items():
        try:
            v = float(val)
        except (TypeError, ValueError):
            continue
        if 0.1 <= v <= 1.0:
            clean[str(name)] = v
    _name_thresholds = clean


def _effective_threshold(name: str, threshold: float) -> float:
    """The threshold to actually use for `name`: a per-name override when the
    caller left threshold at the default, otherwise the caller's explicit
    value (an explicit non-default threshold always wins -- those are
    deliberate, e.g. MAX_PLACEMENT_THRESHOLD)."""
    if threshold == DEFAULT_THRESHOLD:
        return _name_thresholds.get(name, DEFAULT_THRESHOLD)
    return threshold

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
    """No reference image exists for this name yet -- neither a
    <template_dir>/<name>/ folder with images in it nor a loose
    <template_dir>/<name>.png file."""


_template_cache = {}


def template_variant_paths(name: str, template_dir: str = UI_ASSETS_DIR) -> list:
    """Every reference image on disk for a searched name, in the order they
    get tried. One search name maps to a FOLDER of interchangeable images,
    not a single file -- so when a button renders differently on someone's
    setup (size drift, a game-update art tweak, a different graphics
    quality), they can drop ADDITIONAL crops of the same button into that
    name's folder (via the Image Manager in Settings > General, or by hand)
    and every one of them gets tried before the search gives up, instead of
    one blessed .png being the single point of failure.

    Lookup order for name X under template_dir:
      1. <template_dir>/X.png            -- loose legacy file, kept working
                                            so an old hand-dropped override
                                            or fresh capture still resolves.
      2. <template_dir>/X/*.png          -- the folder-per-name layout.
                                            X.png (the original/primary
                                            crop) sorts first, the rest
                                            alphabetically, so behavior with
                                            a single image is identical to
                                            the old flat-file layout.
      3. <template_dir>/<sub>/X.png      -- shallow search of the immediate
                                            subfolders. Keeps EXACT filenames
                                            that live inside a related name's
                                            folder resolvable as their own
                                            search name too (e.g.
                                            "priority_upgrade_1" still
                                            resolves even though its file
                                            sits in priority_upgrade/ as one
                                            of that name's variants) -- a
                                            back-compat path for old code or
                                            hand-written searches, not the
                                            primary layout.

    Names stay EXACT -- "continue_2" and "exp_extract_continue" are
    different search names with different meanings in the runner (a
    follow-up screen's button vs the extract checkpoint's decline choice),
    same for "nav_start_game" vs "nav_start_game_confirm" (ready-up vs a
    "Start Anyway"-style second confirm) -- so each has its own folder; a
    folder's images are variants of that one name only, never of a
    similarly-named sibling.

    The resolved list is cached (the runner re-resolves the same name every
    ~0.3s poll of wait_for_image -- hitting the filesystem that often for
    an unchanged folder would be pure waste), so images added/removed on
    disk mid-session need clear_template_cache() (the Image Manager and
    Settings > "Reload Vision Images" both do this) to be picked up.
    """
    cache_key = ("variant_paths", template_dir, name)
    if cache_key in _template_cache:
        return _template_cache[cache_key]

    paths = []
    loose = os.path.join(template_dir, f"{name}.png")
    if os.path.isfile(loose):
        paths.append(loose)

    folder = os.path.join(template_dir, name)
    if os.path.isdir(folder):
        primary = f"{name}.png".lower()
        entries = sorted(
            (e for e in os.listdir(folder) if e.lower().endswith(".png")),
            # The primary crop first, then the extras alphabetically --
            # keeps the single-image case byte-identical to the old flat
            # layout, and makes "which one gets tried first" predictable.
            key=lambda e: (e.lower() != primary, e.lower()),
        )
        paths.extend(os.path.join(folder, e) for e in entries)

    if not paths and os.path.isdir(template_dir):
        # Shallow subfolder fallback -- only reached when the name has no
        # folder/file of its own, i.e. it's a numbered variant filed inside
        # a sibling's folder (see the docstring's point 3).
        for entry in os.listdir(template_dir):
            sub = os.path.join(template_dir, entry)
            if os.path.isdir(sub):
                candidate = os.path.join(sub, f"{name}.png")
                if os.path.isfile(candidate):
                    paths.append(candidate)
                    break

    _template_cache[cache_key] = paths
    return paths


def template_path(name: str, template_dir: str = UI_ASSETS_DIR) -> str:
    """The primary on-disk path for a name -- its first existing variant, or
    (when nothing exists yet) the canonical folder-layout path a new capture
    for it SHOULD be saved to. Kept for error messages and 'where would this
    save' callers; actual matching goes through template_variant_paths and
    tries every variant, not just this one."""
    paths = template_variant_paths(name, template_dir)
    if paths:
        return paths[0]
    return os.path.join(template_dir, name, f"{name}.png")


def clear_template_cache() -> None:
    """Drops every cached (gray, mask) pair AND cached folder listing --
    call after adding/replacing/deleting a reference image on disk
    mid-session, or the runner keeps matching against the old cached bytes
    until the app is restarted."""
    _template_cache.clear()


def _load_gray_from_path(path: str):
    """Loads + caches one reference image file as (grayscale, mask). Cached
    per file path because the runner hits this on every poll of
    wait_for_image (every ~0.3s) -- re-decoding the same PNG off disk that
    often would be pure waste. Returns None (cached too) if the file can't
    be decoded, so one corrupt image in a variant folder just gets skipped
    instead of failing every search of that name.

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
    cache_key = ("gray", path)
    if cache_key in _template_cache:
        return _template_cache[cache_key]
    raw = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if raw is None:
        _template_cache[cache_key] = None
        return None
    if raw.ndim == 3 and raw.shape[2] == 4:
        gray = cv2.cvtColor(raw[:, :, :3], cv2.COLOR_BGR2GRAY)
    else:
        gray = cv2.cvtColor(raw, cv2.COLOR_BGR2GRAY) if raw.ndim == 3 else raw
    entry = (gray, None)
    _template_cache[cache_key] = entry
    return entry


def load_template_grays(name: str, template_dir: str = UI_ASSETS_DIR) -> list:
    """Every loadable variant of a name as a list of (grayscale, mask)
    pairs, in template_variant_paths order. Raises TemplateNotFound if the
    name has no reference images at all, or has files but none of them
    could actually be decoded -- either way there's nothing to search for."""
    paths = template_variant_paths(name, template_dir)
    if not paths:
        raise TemplateNotFound(
            f'No reference image for "{name}" -- save one to '
            f'{os.path.join(template_dir, name)} (a cropped screenshot of just '
            f'that button/text -- Settings > General > Image Manager can capture '
            f'and crop one for you) and try again.'
        )
    loaded = [entry for entry in (_load_gray_from_path(p) for p in paths) if entry is not None]
    if not loaded:
        raise TemplateNotFound(
            f'Reference image(s) for "{name}" exist but none could be read as an image: {paths}'
        )
    return loaded


def load_template_gray(name: str, template_dir: str = UI_ASSETS_DIR) -> tuple:
    """The name's primary variant only -- first entry of
    load_template_grays. For callers that need ONE representative template
    (e.g. find_bottommost_image's scan uses each variant's own size);
    anything doing a normal search should let find_in_gray_multiscale try
    all of them."""
    return load_template_grays(name, template_dir)[0]


# Sticky switch for the BitBlt-dead-capture fallback below: some NVIDIA
# setups (hardware-accelerated GPU scheduling / fullscreen-optimized DX
# flip-model presentation, per real reports of "every image search fails
# but the game is clearly on screen") return all-black frames from
# BitBlt-style SCREEN grabs (what mss does) while the WINDOW-content
# capture path (PrintWindow with PW_RENDERFULLCONTENT on Windows --
# already this codebase's proven answer to DX-composited windows, see
# window_win.capture_window_rgb -- or CGWindowListCreateImage on mac)
# renders the same window fine. Once one dead screen-grab is confirmed
# genuinely dead (the window capture of the same moment had real pixels),
# every later capture goes straight to the window path instead of paying
# for both on every poll.
#
# macOS starts on the window path outright. CGWindowListCreateImage reads the
# window's own backing store even when something is in front of it, which a
# screen grab fundamentally cannot do -- and on mac Roblox is a separate
# top-level window that anything (including the macro's own panel on the
# non-Dashboard screens, see Api.set_panel_expanded) can sit on top of. The
# all-black tiebreaker below would never catch that: an occluded grab returns
# the *covering window's* pixels, which are not black, so every template match
# would quietly run against whatever is overlapping the game instead of failing
# loudly. Windows keeps the lazy detection -- there the game is a child window
# inside ours and can't be occluded by a foreign window in the first place.
from . import mss_manager

_use_window_capture = sys.platform == "darwin"


def _get_mss():
    return mss_manager.get_mss()


def close_mss() -> None:
    """Closes the current thread's MSS instance."""
    mss_manager.close_mss()


def close_all_mss() -> None:
    """Closes all active MSS instances across all threads."""
    mss_manager.close_all_mss()


def _capture_window_gray(hwnd: int, region: tuple = None):
    """The window-content capture path (PrintWindow / CGWindowListCreateImage
    -- see wm.capture_window_rgb), normalized to reference space and
    cropped to `region` exactly like the primary path. Returns None if the
    window couldn't be rendered."""
    result = wm.capture_window_rgb(hwnd)
    if not result:
        return None
    rgb, w, h = result
    img = np.frombuffer(rgb, np.uint8).reshape(h, w, 3)
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    if gray.shape[:2] != (config.FIXED_WIN_H, config.FIXED_WIN_W):
        gray = cv2.resize(gray, (config.FIXED_WIN_W, config.FIXED_WIN_H), interpolation=cv2.INTER_AREA)
    if region is not None:
        rx, ry, rw, rh = (int(v) for v in region)
        gray = gray[max(0, ry):ry + rh, max(0, rx):rx + rw]
    return gray


def capture_game_gray(hwnd: int, region: tuple = None) -> np.ndarray:
    """Grayscale screenshot of the game window, or a sub-rect of it in
    REFERENCE-space coordinates (x, y, w, h) -- e.g. the Nav's Play button
    lives in a small fixed corner, so a caller that already knows roughly
    where to look can pass region to search a much smaller, faster image
    instead of the whole 1152x756 window.

    Returned image is ALWAYS at reference dimensions (region's own w/h, or
    the full 1152x756) regardless of the window's real on-screen size or
    pixel density: the grab rect is scaled out to the actual window
    (see _window_geometry) and the grabbed pixels resized back -- which is
    what lets one set of reference images match on a Retina Mac's 2x
    captures or any non-reference window size at all. At the Windows norm
    (window exactly reference-sized, 1x density) both steps are identity
    and skipped.

    A plain screen-region grab (not the PrintWindow trick get_roblox_snapshot
    uses) is the DEFAULT here: the runner only ever calls this while Roblox
    is the actually-visible, docked/arranged, foreground game (never from a
    screen that hides it), so there's normally nothing else that could be
    captured instead. The exception is setups where BitBlt screen grabs
    come back black entirely (see _use_window_capture above -- reported on
    NVIDIA GPU-scheduling/fullscreen-optimization setups): a dead frame
    triggers a one-time check against the window-content capture path, and
    if THAT has real pixels for the same moment, all future captures
    switch to it for the rest of the session.
    """
    global _use_window_capture
    if _use_window_capture:
        gray = _capture_window_gray(hwnd, region)
        if gray is not None and gray.any():
            return gray
        # Window capture came back black (or failed). Could be a genuine
        # black moment (loading screen) OR a PrintWindow-dead setup. Check a
        # screen grab: if IT has pixels, this setup can't use window capture
        # -- switch back to screen grab permanently (the mirror of the
        # switch below, so the flicker-free default is self-correcting).
        screen_gray = _screen_grab_gray(hwnd, region)
        if screen_gray is not None and screen_gray.any():
            _use_window_capture = False
            print("[vision] Window capture is coming back black but the screen grab renders -- "
                  "this setup can't use window capture, switching to screen capture for this session "
                  "(the screen may flicker; that's the trade-off).")
            return screen_gray
        return screen_gray if screen_gray is not None else gray

    screen_gray = _screen_grab_gray(hwnd, region)
    if screen_gray is not None and not screen_gray.any():
        # Every pixel zero -- either a genuinely black moment (loading
        # screens do that, harmless to double-check) or the dead-BitBlt
        # NVIDIA case (see _use_window_capture). The tiebreaker is whether
        # the window-content capture of this same moment has real pixels:
        # if yes, the screen-grab path is dead on this setup -- adopt the
        # window path for the rest of the session.
        window_gray = _capture_window_gray(hwnd, region)
        if window_gray is not None and window_gray.any():
            _use_window_capture = True
            print("[vision] Screen captures are coming back black but the window itself renders "
                  "(BitBlt-dead setup, common with NVIDIA GPU scheduling/fullscreen optimizations) -- "
                  "switching to window-content capture for this session.")
            return window_gray
    return screen_gray


def _screen_grab_gray(hwnd: int, region: tuple = None):
    """The mss screen-region grab (BitBlt), normalized to reference space.
    The DEFAULT capture path historically, but the screen BitBlt is what
    flashes the display white on some GPU/fullscreen-optimization setups --
    which is why window-content capture (see capture_game_gray) is preferred
    now. Kept as the fallback for setups where PrintWindow renders black."""
    left, top, sx, sy = _window_geometry(hwnd)
    if region is not None:
        rx, ry, rw, rh = region
        grab_left, grab_top = left + rx * sx, top + ry * sy
        grab_w, grab_h = rw * sx, rh * sy
        out_w, out_h = int(rw), int(rh)
    else:
        grab_left, grab_top = left, top
        grab_w, grab_h = config.FIXED_WIN_W * sx, config.FIXED_WIN_H * sy
        out_w, out_h = config.FIXED_WIN_W, config.FIXED_WIN_H
    if grab_w <= 0 or grab_h <= 0:
        return None
    try:
        sct = _get_mss()
        shot = sct.grab({"left": int(grab_left), "top": int(grab_top),
                          "width": int(round(grab_w)), "height": int(round(grab_h))})
    except Exception:
        close_mss()
        raise
    bgra = np.frombuffer(shot.raw, dtype=np.uint8).reshape(shot.height, shot.width, 4)
    gray = cv2.cvtColor(bgra, cv2.COLOR_BGRA2GRAY)
    if gray.shape[:2] != (out_h, out_w):
        # Non-reference capture (Retina 2x, off-size window) -- normalize.
        # INTER_AREA: this is almost always a shrink, where it's the
        # recommended anti-aliasing choice (see _scaled_templates).
        gray = cv2.resize(gray, (out_w, out_h), interpolation=cv2.INTER_AREA)
    return gray


def force_window_capture() -> None:
    """Opt into the window-content capture path unconditionally (PrintWindow
    / CGWindowListCreateImage) -- used by cutout dock mode, where the game
    sits BEHIND the GUI and a screen grab of its rect would read the GUI's
    own solid surface whenever the hole is closed. Same switch the
    dead-BitBlt tiebreaker in capture_game_gray flips lazily; this just
    sets it up front when the mode makes screen grabs categorically wrong."""
    global _use_window_capture
    _use_window_capture = True


def _capture_window_bgr(hwnd: int, region: tuple = None):
    """Color twin of _capture_window_gray -- same window-content capture and
    reference-space normalization, minus the grayscale conversion."""
    result = wm.capture_window_rgb(hwnd)
    if not result:
        return None
    rgb, w, h = result
    img = np.frombuffer(rgb, np.uint8).reshape(h, w, 3)
    bgr = img[:, :, ::-1]
    if bgr.shape[:2] != (config.FIXED_WIN_H, config.FIXED_WIN_W):
        bgr = cv2.resize(bgr, (config.FIXED_WIN_W, config.FIXED_WIN_H), interpolation=cv2.INTER_AREA)
    if region is not None:
        rx, ry, rw, rh = (int(v) for v in region)
        bgr = bgr[max(0, ry):ry + rh, max(0, rx):rx + rw]
    return bgr


def capture_window_region_bgr(hwnd: int, region: tuple = None):
    """Window-CONTENT capture (PrintWindow / CGWindowListCreateImage) of a
    reference-space region, cropped and normalized -- works even when the
    window is covered or unfocused (unlike a screen grab). The wave monitor
    uses this so it can keep reading the HUD while you're tabbed out of
    Roblox. Returns None if the window can't be rendered."""
    return _capture_window_bgr(hwnd, region)


def capture_game_bgr(hwnd: int, region: tuple = None) -> np.ndarray:
    """Color twin of capture_game_gray: a BGR capture normalized to
    reference-space dimensions, for detection that keys off a button's
    COLOR rather than its art (see find_color_run). Same capture-path
    rules as the grayscale version -- honors the window-capture switch,
    and a region is reference-space (x, y, w, h)."""
    if _use_window_capture:
        bgr = _capture_window_bgr(hwnd, region)
        if bgr is not None:
            return bgr

    left, top, sx, sy = _window_geometry(hwnd)
    if region is not None:
        rx, ry, rw, rh = region
        grab_left, grab_top = left + rx * sx, top + ry * sy
        grab_w, grab_h = rw * sx, rh * sy
        out_w, out_h = int(rw), int(rh)
    else:
        grab_left, grab_top = left, top
        grab_w, grab_h = config.FIXED_WIN_W * sx, config.FIXED_WIN_H * sy
        out_w, out_h = config.FIXED_WIN_W, config.FIXED_WIN_H
    if grab_w <= 0 or grab_h <= 0:
        return None
    try:
        sct = _get_mss()
        shot = sct.grab({"left": int(grab_left), "top": int(grab_top),
                          "width": int(round(grab_w)), "height": int(round(grab_h))})
    except Exception:
        close_mss()
        raise
    bgra = np.frombuffer(shot.raw, dtype=np.uint8).reshape(shot.height, shot.width, 4)
    bgr = cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)
    if bgr.shape[:2] != (out_h, out_w):
        bgr = cv2.resize(bgr, (out_w, out_h), interpolation=cv2.INTER_AREA)
    if not bgr.any():
        # Same dead-BitBlt tiebreaker as capture_game_gray -- but the
        # switch itself is left to the grayscale path (it runs every tick
        # regardless); here it's enough to just serve the window capture.
        window_bgr = _capture_window_bgr(hwnd, region)
        if window_bgr is not None and window_bgr.any():
            return window_bgr
    return bgr


def find_color_run(hwnd: int, region: tuple, mask_fn, min_run: int, min_height: int = 3) -> dict:
    """Finds a solid-colored UI element (a button face) by COLOR inside a
    reference-space band, without any template: capture the band in color,
    mark every pixel mask_fn accepts, and take the widest horizontal run of
    accepted pixels. A run at least min_run px wide (and min_height tall
    through its center column) is a hit; its center comes back as
    cx/cy in full-window reference coords, like find_image's.

    Complements template matching rather than replacing it: a template
    keys off ART (exact glyphs, so it distinguishes two same-colored
    buttons but pays a full matchTemplate sweep per variant per scale),
    while this keys off a COLOR BLOB (milliseconds per check, immune to
    text/art changes inside the button, but only usable where a color +
    location band is unambiguous -- e.g. the Expedition checkpoint's
    green Continue, see core.runner's color checkpoint path).

    mask_fn receives the band's (b, g, r) int16 ndarrays and returns a
    bool mask -- vectorized, so predicates stay cheap. Gaps up to 3px
    inside a run are bridged (anti-aliased button text punches thin holes
    in the face color)."""
    img = capture_game_bgr(hwnd, region)
    if img is None or img.size == 0:
        return None
    arr = img.astype(np.int16)
    mask = mask_fn(arr[:, :, 0], arr[:, :, 1], arr[:, :, 2])
    if not mask.any():
        return None
    best = None  # (width, y, x0, x1)
    for y in range(mask.shape[0]):
        xs = np.flatnonzero(mask[y])
        if xs.size < 2:
            continue
        for run in np.split(xs, np.where(np.diff(xs) > 3)[0] + 1):
            width = int(run[-1] - run[0])
            if best is None or width > best[0]:
                best = (width, y, int(run[0]), int(run[-1]))
    if best is None or best[0] < min_run:
        return None
    width, y, x0, x1 = best
    cx = (x0 + x1) // 2
    ys = np.flatnonzero(mask[:, cx])
    band = None
    for run in np.split(ys, np.where(np.diff(ys) > 3)[0] + 1):
        if run[0] <= y <= run[-1]:
            band = run
            break
    height = int(band[-1] - band[0]) + 1 if band is not None else 1
    if height < min_height:
        return None
    cy = int((band[0] + band[-1]) // 2) if band is not None else y
    return {"cx": cx + int(region[0]), "cy": cy + int(region[1]), "w": width, "h": height}


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


def _scaled_templates(name: str, template_dir: str, scale: float) -> list:
    """Every variant of a name (see load_template_grays), resized to one
    scale factor -- cached per (dir, name, scale) so templates that keep
    missing at 1x don't get re-resized on every single wait_for_image poll
    (every ~0.3s)."""
    if scale == 1.0:
        return load_template_grays(name, template_dir)
    cache_key = ("scaled", template_dir, name, scale)
    if cache_key in _template_cache:
        return _template_cache[cache_key]
    entries = []
    for gray, mask in load_template_grays(name, template_dir):
        h, w = gray.shape[:2]
        new_w, new_h = max(1, round(w * scale)), max(1, round(h * scale))
        # INTER_AREA is the recommended choice for shrinking (avoids moire/
        # aliasing on fine text/edges); INTER_LINEAR for enlarging.
        interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
        scaled_gray = cv2.resize(gray, (new_w, new_h), interpolation=interp)
        scaled_mask = cv2.resize(mask, (new_w, new_h), interpolation=cv2.INTER_NEAREST) if mask is not None else None
        entries.append((scaled_gray, scaled_mask))
    _template_cache[cache_key] = entries
    return entries


def find_in_gray_multiscale(haystack_gray: np.ndarray, name: str, template_dir: str = UI_ASSETS_DIR,
                             threshold: float = DEFAULT_THRESHOLD) -> dict:
    """find_in_gray, but tries EVERY variant image in the name's folder (see
    template_variant_paths) and each of them at a handful of scale factors
    around 1x (see SCALE_FACTORS) instead of one image at its exact captured
    size -- absorbs both a UI that renders slightly bigger/smaller on
    someone else's setup AND a button whose art genuinely varies (which is
    exactly why a name can have multiple images at all). Scale is the OUTER
    loop on purpose: all variants get tried at 1x (their true captured
    size, the overwhelmingly common hit) before any rescaling starts, so
    the fallback images cost nothing when the primary one matches and the
    scale sweep only runs when every variant genuinely missed at 1x."""
    for scale in SCALE_FACTORS:
        for gray, mask in _scaled_templates(name, template_dir, scale):
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
    # match coords are reference-space -- normalize the screenshot to the
    # same space before drawing on it (identity at the Windows norm, see
    # capture_game_gray).
    if bgr.shape[:2] != (config.FIXED_WIN_H, config.FIXED_WIN_W):
        bgr = cv2.resize(bgr, (config.FIXED_WIN_W, config.FIXED_WIN_H), interpolation=cv2.INTER_AREA)
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
    left, top, sx, sy = _window_geometry(hwnd)
    rx, ry, rw, rh = region
    with mss.MSS() as sct:
        shot = sct.grab({"left": int(left + rx * sx), "top": int(top + ry * sy),
                          "width": int(round(rw * sx)), "height": int(round(rh * sy))})
        bgr = np.array(shot)[:, :, :3]
    path = os.path.join(DEBUG_DIR, f"region_{name}.png")
    cv2.imwrite(path, bgr)
    return path


def find_image(hwnd: int, name: str, region: tuple = None, threshold: float = DEFAULT_THRESHOLD,
                template_dir: str = UI_ASSETS_DIR) -> dict:
    """One-shot: capture + match. Returned x/y/cx/cy are in the SAME space as
    `region` -- region-local if a region was passed, full-window client
    coords otherwise. See click_match to turn that into an actual click."""
    load_template_grays(name, template_dir)  # validates at least one image exists before capturing anything
    haystack = capture_game_gray(hwnd, region)
    if haystack is None:
        return None
    match = find_in_gray_multiscale(haystack, name, template_dir, _effective_threshold(name, threshold))
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

    Tries each of the name's variant images in order (same folder-per-name
    set find_in_gray_multiscale searches, though at 1x only -- the warning
    text this exists for has never needed the scale sweep) and returns the
    first variant's bottommost hit; a later variant is only consulted when
    the earlier ones found nothing at all.
    """
    templates = load_template_grays(name, template_dir)
    haystack = capture_game_gray(hwnd, region)
    if haystack is None:
        return None
    threshold = _effective_threshold(name, threshold)
    hh, hw = haystack.shape[:2]
    for template_gray, mask in templates:
        th, tw = template_gray.shape[:2]
        if th > hh or tw > hw:
            continue
        if mask is not None:
            result = cv2.matchTemplate(haystack, template_gray, cv2.TM_CCORR_NORMED, mask=mask)
        else:
            result = cv2.matchTemplate(haystack, template_gray, cv2.TM_CCOEFF_NORMED)
        result[~np.isfinite(result)] = -1

        ys, xs = np.where(result >= threshold)
        if len(ys) == 0:
            continue
        bottom_i = int(np.argmax(ys))
        y, x = int(ys[bottom_i]), int(xs[bottom_i])
        match = {"x": x, "y": y, "w": tw, "h": th, "cx": x + tw // 2, "cy": y + th // 2, "score": float(result[y, x])}
        if region is not None:
            match["x"] += region[0]
            match["y"] += region[1]
            match["cx"] += region[0]
            match["cy"] += region[1]
        return match
    return None


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
    """find_image, but over several DIFFERENTLY-NAMED templates in one call
    -- for screens where more than one distinct thing counts as a hit (e.g.
    the wave flow accepting either "continue_2" or "exp_extract_continue"
    as the follow-up, see core.runner). NOT for visual variants of one
    button -- those live together in that one name's folder and find_image
    already tries them all (see template_variant_paths). Tries each name in
    `names` in order and returns (match, name) for
    the first one actually found on screen -- NOT the first one that merely
    has a reference image on disk. A name with no reference image yet is
    skipped (same as a caller manually looping and catching TemplateNotFound
    per name), unless every single name in `names` is missing, in which case
    there's nothing to search for at all and the first TemplateNotFound
    propagates. Returns (None, None) if every present template was searched
    for and none matched."""
    first_missing = None
    found_any_template = False
    available_names = []
    for name in names:
        try:
            # Validate every candidate before taking a screenshot. The image
            # data is cached, so the later matcher does not decode it again.
            load_template_grays(name, template_dir)
        except TemplateNotFound as exc:
            if first_missing is None:
                first_missing = exc
            continue
        found_any_template = True

        available_names.append(name)

    if not found_any_template and first_missing is not None:
        raise first_missing
    if not available_names:
        return None, None

    # All candidate names describe alternatives visible in the same UI state.
    # Reusing one frame keeps their priority order intact while avoiding an
    # extra screen capture and grayscale conversion for every missed option.
    haystack = capture_game_gray(hwnd, region)
    if haystack is None:
        return None, None

    for name in available_names:
        match = find_in_gray_multiscale(haystack, name, template_dir, _effective_threshold(name, threshold))
        if match is not None:
            if region is not None:
                match["x"] += region[0]
                match["y"] += region[1]
                match["cx"] += region[0]
                match["cy"] += region[1]
            return match, name
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
    """Clicks a match's center -- match coords are reference-space (what
    find_image/wait_for_image return, since captures are normalized to
    reference dimensions), converted to a real screen point through
    ref_to_screen so the click lands proportionally whatever the window's
    actual on-screen size is."""
    mouse.click(*ref_to_screen(hwnd, match["cx"], match["cy"]))


def double_click_match(mouse, hwnd: int, match: dict) -> None:
    """Same as click_match, but double-clicks -- for buttons that only
    sometimes register a single click reliably (see exp_extract's own
    caller)."""
    mouse.double_click(*ref_to_screen(hwnd, match["cx"], match["cy"]))


def right_click_match(mouse, hwnd: int, match: dict) -> None:
    """Same as click_match, but right-clicks -- for a match that's itself
    what opens a context menu (see Auto Upgrade Unit's own caller)."""
    x, y = ref_to_screen(hwnd, match["cx"], match["cy"])
    mouse.click(x, y, button="right")


def shuffle_click_match(mouse, hwnd: int, match: dict) -> None:
    """Same as click_match, but hovers in with a few small moves first (see
    Mouse.shuffle_click) -- for a button reported not to reliably register
    a click game-side even when the click itself visually lands on it."""
    mouse.shuffle_click(*ref_to_screen(hwnd, match["cx"], match["cy"]))
