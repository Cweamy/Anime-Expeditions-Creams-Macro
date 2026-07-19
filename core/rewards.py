"""Reads the "Gained Rewards" row on a Victory screen: a horizontal strip of
icon cells, each with a "125x"-style quantity badge near the top and an item
name label along the bottom -- capture with mss, auto-detect how many icons
are actually in the row this run (see detect_icon_cells), clean each cell up
with OpenCV so the (small, stylized) text OCRs reliably, read it with
pytesseract.

Only the row's rectangle needs calibrating (Settings > Debug) -- there's no
way to know that up front without seeing the live UI -- the icon count is
detected fresh every capture instead of being a fixed setting, since it
genuinely varies run to run and a wrong count either drops real rewards or
reads empty space as an item.
"""
import difflib
import os
import re
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import cv2

from core.ocr import (  # noqa: F401 -- re-exported for existing callers/tests
    TesseractNotAvailable, get_pytesseract as _get_pytesseract,
    capture_region, ocr_best as _ocr_best,
)

# Reference icon art scraped from the game's wiki (see tools/fetch_item_icons.py) --
# transparent-background PNGs named after the item, one per known reward.
_ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "item_icons")
_icon_histograms = None  # lazily built on first use, cached for the process's lifetime

# Fixed spot (relative to the docked game window's top-left, same convention
# as the calibrated reward region itself) where the Gained Rewards panel's
# scrollbar track renders -- it only shows up there at all when the drop
# overflows the visible grid, so sampling its color is a cheap way to know
# whether a second, scrolled-to-bottom capture is needed. Shared by
# main.Api.read_rewards (on-demand) and core.runner's automatic post-match
# read, so it only needs to be right in one place.
SCROLLBAR_PROBE = (710, 428, 4, 2)  # (x, y, width, height)
SCROLLBAR_COLOR = 0x373737
# Stricter than sample_color_matches' own default (20) -- the reward panel's
# own background sits close enough to this dark gray that the default
# tolerance was reading "there's more to scroll" when there wasn't, which
# triggered a real scroll that landed on a totally different panel/section
# and got its contents blended into the read as if they were real drops.
# Tightened again (was 8) -- still too loose in practice.
SCROLLBAR_TOLERANCE = 1

_QUANTITY_PATTERN = re.compile(r"^\d[\d,]*x$")
# Loose "this already looks like a real name" shape -- letters/spaces only,
# no stray symbols, long enough to not just be OCR noise -- good enough to
# let _ocr_best stop early on the common case instead of always running the
# full mask/psm sweep for the name field too.
_NAME_LOOKS_CLEAN = re.compile(r"^[A-Za-z][A-Za-z ]{2,}$")


_QUANTITY_EXTRACT = re.compile(r"\d[\d,]*x?")


def _clean_quantity_text(text: str) -> str:
    """Trims stray trailing punctuation (a comma or period tesseract
    sometimes tacks on after the "x") down to just the digits/comma/x that
    make up the actual quantity, then drops any thousands-separator commas --
    whether tesseract actually catches the in-game comma is inconsistent
    (same value reads as both "1,000x" and "1000x" depending on the capture),
    so always normalizing to the no-comma form keeps every reading the same
    shape instead of the format silently varying item to item."""
    match = _QUANTITY_EXTRACT.search(text)
    cleaned = match.group(0) if match else text
    return cleaned.replace(",", "")


_NAME_ALLOWED = re.compile(r"[^A-Za-z0-9 '\-]")


def _clean_name_text(text: str) -> str:
    """Strips the stray symbols (pipes, brackets, punctuation) that OCR
    sometimes picks up from the colorful icon art bleeding into the name
    crop -- item names are always plain words, so anything outside letters/
    digits/spaces/apostrophes/hyphens is noise, not part of the label."""
    return re.sub(r"\s+", " ", _NAME_ALLOWED.sub(" ", text)).strip()


def save_region_preview(region_bgr: np.ndarray, path: str) -> None:
    """Saves exactly what Read Rewards would capture -- no annotations --
    so a bad calibration (wrong region entirely) is visible at a glance."""
    cv2.imwrite(path, region_bgr)


def detect_icon_cells(region_bgr: np.ndarray, min_width_frac: float = 0.03) -> list:
    """Finds icon cell boundaries by looking for the vertical gaps between
    them instead of assuming a fixed count: a gap column (blank background
    between icons) has very low pixel variance, while a column inside an
    icon's art/border/text has much higher variance. Runs of "busy" columns
    become cells; runs of "gap" columns split them.

    Returns a list of (x0, x1) pixel ranges, left to right -- however many
    the row actually has this capture.
    """
    h, w = region_bgr.shape[:2]
    gray = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    col_std = gray.std(axis=0)
    threshold = max(float(col_std.max()) * 0.12, 3.0)
    is_gap = col_std < threshold

    min_width = max(1, int(w * min_width_frac))
    cells = []
    start = None
    for x in range(w):
        if not is_gap[x]:
            if start is None:
                start = x
        elif start is not None:
            if x - start >= min_width:
                cells.append((start, x))
            start = None
    if start is not None and w - start >= min_width:
        cells.append((start, w))
    return _split_oversized_cells(cells)


def _split_oversized_cells(cells: list, oversize_factor: float = 1.6) -> list:
    """Two icons that butt up against each other with no blank column
    between them (e.g. a colored card background touching the next card's
    background instead of the row's dark backdrop) read as one wide "gap
    free" cell instead of two -- the column-variance split in
    detect_icon_cells has nothing to key off there. Once most cells have
    established a normal width, a cell far wider than that norm is almost
    certainly N icons stuck together rather than one giant icon, so slice
    it into N equal-width pieces instead of feeding one bloated cell (with
    two numbers and two names in it) into OCR.
    """
    if len(cells) < 2:
        return cells
    widths = sorted(x1 - x0 for x0, x1 in cells)
    median_width = widths[len(widths) // 2]
    if median_width <= 0:
        return cells

    result = []
    for x0, x1 in cells:
        width = x1 - x0
        n = round(width / median_width)
        if n >= 2 and width >= oversize_factor * median_width:
            step = width / n
            result.extend(
                (int(x0 + i * step), int(x0 + (i + 1) * step)) for i in range(n)
            )
        else:
            result.append((x0, x1))
    return result


def detect_text_bands(region_bgr: np.ndarray, bright_thresh: int = 200,
                       row_frac: float = 0.02) -> list:
    """Finds the row-ranges (y0, y1) that actually contain bright (white/
    outlined) text, by looking at rows with an unusually high count of
    bright pixels across the *whole* region -- both the quantity badge and
    the item name are rendered in bold white-ish text, so their rows light
    up in this histogram regardless of where they sit vertically.

    This replaces guessing "quantity is the top N%, name is the bottom M%"
    of the cell: those fractions only held for one specific region height,
    and silently pointed at blank space once the calibrated box was
    resized (e.g. widened/heightened to fit a debug capture) -- reading the
    actual pixels instead makes the OCR band track wherever the labels
    really are.
    """
    gray = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    bright_count = (gray >= bright_thresh).sum(axis=1)
    threshold = max(int(w * row_frac), 2)
    active = bright_count >= threshold

    bands = []
    start = None
    for y in range(h):
        if active[y]:
            if start is None:
                start = y
        elif start is not None:
            bands.append((start, y))
            start = None
    if start is not None:
        bands.append((start, h))
    return bands


def _icon_hue_sat_histogram(bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], mask, [30, 32], [0, 180, 0, 256])
    cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
    return hist


def _center_oval_mask(h: int, w: int) -> np.ndarray:
    """A centered oval covering roughly the icon art, excluding the card's
    decorative background/corners -- shared by identify_item_name (scoring a
    live query crop against reference histograms) and _ensure_icon_reference
    (deciding what counts as "the icon" when saving a new one), so a
    self-captured reference is built the exact same way a wiki-scraped one
    already is."""
    mask = np.zeros((h, w), np.uint8)
    cv2.ellipse(mask, (w // 2, h // 2), (max(1, int(w * 0.32)), max(1, int(h * 0.42))), 0, 0, 360, 255, -1)
    return mask


_ICON_NAME_ALLOWED = re.compile(r"[^A-Za-z0-9 \-']")


def _ensure_icon_reference(icon_bgr: np.ndarray, name: str) -> None:
    """Bootstraps Assets/item_icons/ from real gameplay captures instead of
    requiring tools/fetch_item_icons.py's full wiki scrape -- the first time
    an item is read with no existing reference icon, the actual captured
    crop is saved under its name (see read_cell, which only calls this with
    a name confirmed either by icon-match or by comparing OCR text against
    the current stage's known possible rewards -- never a raw, unverified
    OCR guess), so every future read of that same item can match it by icon
    color like any wiki-scraped one instead of falling back to OCR forever.

    A no-op if a reference already exists under that name (including one
    saved this same way earlier) -- this never overwrites.
    """
    safe = _ICON_NAME_ALLOWED.sub("", name or "").strip()
    if not safe:
        return
    path = os.path.join(_ICON_DIR, f"{safe}.png")
    if os.path.isfile(path):
        return
    h, w = icon_bgr.shape[:2]
    if h < 8 or w < 8:
        return  # too small a crop to be useful reference art

    # Same oval identify_item_name scores a query crop against -- everything
    # outside it (card border/background) is made fully transparent, so this
    # matches the "centered art, transparent background" shape every wiki-
    # scraped reference icon already has, not a plain opaque rectangle.
    alpha = _center_oval_mask(h, w)
    rgba = cv2.cvtColor(icon_bgr, cv2.COLOR_BGR2BGRA)
    rgba[:, :, 3] = alpha

    os.makedirs(_ICON_DIR, exist_ok=True)
    cv2.imwrite(path, rgba)
    global _icon_histograms
    _icon_histograms = None  # force a reload next call so the new icon is usable immediately


def _load_icon_histograms() -> dict:
    """Loads every reference icon in assets/item_icons once per process and
    reduces each to a hue/saturation histogram over its non-transparent
    pixels, keyed by item name (the filename). Cached at module scope --
    there are only ~100 of these and they never change at runtime, so
    there's no reason to redo this work on every reward-row read."""
    global _icon_histograms
    if _icon_histograms is not None:
        return _icon_histograms

    histograms = {}
    if os.path.isdir(_ICON_DIR):
        for fname in os.listdir(_ICON_DIR):
            name, ext = os.path.splitext(fname)
            if ext.lower() != ".png":
                continue
            img = cv2.imread(os.path.join(_ICON_DIR, fname), cv2.IMREAD_UNCHANGED)
            if img is None or img.ndim != 3 or img.shape[2] != 4:
                continue
            alpha_mask = (img[:, :, 3] > 200).astype(np.uint8) * 255
            if cv2.countNonZero(alpha_mask) < 20:
                continue
            histograms[name] = _icon_hue_sat_histogram(img[:, :, :3], alpha_mask)
    _icon_histograms = histograms
    return histograms


def _best_allowed_match(ocr_text: str, allowed_names: list, min_ratio: float = 0.5):
    """The closest name in allowed_names to ocr_text, by fuzzy ratio -- or
    None if nothing clears min_ratio. Used as a confirmed fallback when icon
    identification finds no existing reference to match against (see
    read_cell): a stage's real possible-reward list is a much safer thing
    to trust a garbled OCR read against than accepting it verbatim."""
    if not ocr_text or not allowed_names:
        return None
    ocr_lower = ocr_text.lower()
    best_name, best_ratio = None, min_ratio
    for name in allowed_names:
        ratio = difflib.SequenceMatcher(None, ocr_lower, name.lower()).ratio()
        if ratio > best_ratio:
            best_ratio, best_name = ratio, name
    return best_name


def _narrow_histograms(histograms: dict, allowed_names: list) -> dict:
    """Restricts the candidate pool to icons plausibly matching a known
    "this stage can only reward these" list (see core.stage_data), instead
    of every one of the ~110 known items. A stage's actual reward pool is
    5-6 items; scoring against the full set is exactly how a bad capture
    ends up "identified" as something the stage could never actually drop
    (a hat, a crown, an unrelated currency) -- restricting the candidates
    up front makes that class of error structurally impossible, not just
    less likely.

    Matching is loose (substring or high fuzzy-ratio, not exact equality)
    since the wiki's own naming doesn't always match the icon filename
    exactly (e.g. wiki "Gems" vs. the icon file "Gem.png"). Falls back to
    the full, unfiltered set if nothing in it matches any allowed name --
    better to fall back to the general-purpose behavior than to match
    against an empty candidate pool and always return "".
    """
    if not allowed_names:
        return histograms
    wanted = [n.lower() for n in allowed_names if n]
    if not wanted:
        return histograms

    def matches(name: str) -> bool:
        name_lower = name.lower()
        for allowed in wanted:
            if name_lower == allowed or name_lower in allowed or allowed in name_lower:
                return True
            if difflib.SequenceMatcher(None, name_lower, allowed).ratio() > 0.8:
                return True
        return False

    narrowed = {name: hist for name, hist in histograms.items() if matches(name)}
    return narrowed or histograms


def identify_item_name(icon_bgr: np.ndarray, ocr_name_text: str = "",
                        candidate_count: int = 8, allowed_names: list = None) -> str:
    """Identifies which known reward item an icon-cell crop shows, by color
    signature rather than reading the (often illegible at this resolution)
    name label.

    Icon art survives being tiny far better than text does -- a handful of
    pixels of "mostly blue" is still recognizably a Gem, where a handful of
    pixels of stylized text is often just noise (see the module docstring's
    OCR path). So this matches the crop's hue/saturation histogram over a
    centered oval (the icon; this skips the card's decorative background
    corners) against every reference icon's histogram, takes the top
    candidates, and re-ranks them by fuzzy string similarity to whatever the
    OCR pass on the name label did manage to read -- color alone confuses
    icons that happen to share a palette (e.g. two mostly-white/gold icons),
    and OCR alone garbles small stylized text, but the two rarely fail on
    the *same* item, so combining them resolved every miss either one made
    alone in testing.

    allowed_names, if given (see core.stage_data.expected_item_names),
    narrows the candidate pool to just what this specific stage can
    actually reward -- see _narrow_histograms.

    Returns "" (falls back to the OCR text upstream) if there's no reference
    set to match against, or the crop is too small/blank to build a
    histogram from.
    """
    histograms = _load_icon_histograms()
    if not histograms:
        return ""
    histograms = _narrow_histograms(histograms, allowed_names)

    h, w = icon_bgr.shape[:2]
    if h < 4 or w < 4:
        return ""

    mask = _center_oval_mask(h, w)
    crop_hist = _icon_hue_sat_histogram(icon_bgr, mask)

    scored = sorted(
        ((cv2.compareHist(crop_hist, rhist, cv2.HISTCMP_CORREL), name) for name, rhist in histograms.items()),
        reverse=True,
    )
    top_candidates = scored[:candidate_count]

    ocr_lower = ocr_name_text.lower()
    best_name, best_score = "", float("-inf")
    for hist_score, name in top_candidates:
        text_score = difflib.SequenceMatcher(None, ocr_lower, name.lower()).ratio()
        combined = hist_score + text_score
        if combined > best_score:
            best_score, best_name = combined, name
    return best_name


def detect_row_bands(region_bgr: np.ndarray, min_height_frac: float = 0.15) -> list:
    """Finds row boundaries the same way detect_icon_cells finds column
    boundaries: a blank horizontal strip between two stacked rows of reward
    cards has very low pixel variance, while a strip crossing through card
    art/badges/text has much higher variance. Needed because a capture tall
    enough to show more than one row (e.g. a big drop before it's scrolled)
    otherwise gets fed whole into read_reward_row, which assumes exactly one
    row and would blend row 1's quantity band with row 2's name band.

    Returns a list of (y0, y1) pixel ranges, top to bottom -- just one
    covering the whole height if no clear gap is found (the single-row case
    this always used to assume).
    """
    h, w = region_bgr.shape[:2]
    gray = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    row_std = gray.std(axis=1)
    threshold = max(float(row_std.max()) * 0.12, 3.0)
    is_gap = row_std < threshold

    min_height = max(1, int(h * min_height_frac))
    rows = []
    start = None
    for y in range(h):
        if not is_gap[y]:
            if start is None:
                start = y
        elif start is not None:
            if y - start >= min_height:
                rows.append((start, y))
            start = None
    if start is not None and h - start >= min_height:
        rows.append((start, h))
    rows = rows or [(0, h)]
    return _drop_cutoff_row(rows, h)


def _drop_cutoff_row(rows: list, h: int, edge_tolerance: int = 2) -> list:
    """Drops a leading or trailing row band that's clearly a partial row
    sliced off by the calibrated box's top/bottom edge, not a real complete
    row.

    A drop that overflows the visible grid shows a sliver of the next row
    peeking in at the bottom before scrolling (see the module docstring on
    scrolling) -- and after scrolling, the *previous* row is just as likely
    to end up the partial one instead, trailing off at the *top* of the new
    capture if the scroll only shifted the list a little rather than
    clearing that row out of view entirely. Either way, if that sliver sits
    close enough to the row next to it, there's no blank gap for the loop
    above to split on, so the sliver ends up fused onto that row's band
    instead of forming its own. Left in, that extra content shifts
    read_reward_row's internal text-band detection for the *whole* fused
    slice, corrupting every real item in that row -- not just the cut-off
    one. A band that touches a region edge exactly and is much shorter than
    a normal row is either that fused sliver or a lone cut-off row on its
    own; either way it's unreliable and gets read properly anyway from
    whichever capture (pre- or post-scroll) actually shows it in full, so
    it's dropped here rather than trusted.
    """
    if len(rows) < 2:
        return rows

    def is_cutoff(band, others):
        y0, y1 = band
        touches_edge = y0 <= edge_tolerance or y1 >= h - edge_tolerance
        if not touches_edge:
            return False
        other_heights = [oy1 - oy0 for oy0, oy1 in others]
        median_height = sorted(other_heights)[len(other_heights) // 2]
        return (y1 - y0) < median_height * 0.7

    if is_cutoff(rows[-1], rows[:-1]):
        rows = rows[:-1]
    if len(rows) >= 2 and is_cutoff(rows[0], rows[1:]):
        rows = rows[1:]
    return rows


def read_reward_grid(region_bgr: np.ndarray, slice_pad: int = 3, **kwargs) -> list:
    """Reads every row of reward cards in region_bgr, not just one -- splits
    into row bands first (see detect_row_bands), then runs the existing
    single-row read_reward_row on each slice independently, since each row
    has its own quantity/name text bands and icon-cell columns.

    Each row slice gets a few pixels of padding beyond its tightly-detected
    content boundary (clamped so it never crosses into a neighboring row's
    territory) -- detect_row_bands finds the exact rows where content starts/
    stops, and if a quantity badge's top edge happens to sit right at that
    boundary, slicing exactly there crops the top off the leading digit
    instead of just making it blurry. That doesn't garble the character the
    way the earlier cross-row bleed did -- it just erases it outright (e.g.
    "1000x" -> "000x"), which no amount of upscaling/sharpening downstream
    can recover since the pixels are simply gone from the crop.
    """
    h = region_bgr.shape[0]
    row_bands = detect_row_bands(region_bgr)
    results = []
    for i, (y0, y1) in enumerate(row_bands):
        prev_y1 = row_bands[i - 1][1] if i > 0 else 0
        next_y0 = row_bands[i + 1][0] if i + 1 < len(row_bands) else h
        pad_y0 = max(prev_y1, y0 - slice_pad, 0)
        pad_y1 = min(next_y0, y1 + slice_pad, h)
        row_slice = region_bgr[pad_y0:pad_y1, :]
        if row_slice.shape[0] < 4:
            continue
        results.extend(read_reward_row(row_slice, **kwargs))
    return results


def merge_reward_pages(*pages: list) -> list:
    """Concatenates multiple read_reward_grid results (e.g. one capture
    before scrolling, one after) into a single list, dropping exact
    (quantity, name) duplicates -- a row visible in both the pre-scroll and
    post-scroll capture (the viewport didn't move a full row's worth) would
    otherwise get counted twice. Real distinct reward slots sharing the same
    quantity and name are vanishingly unlikely (the game sums stacks of the
    same item into one slot), so this dedup rule is safe in practice."""
    seen = set()
    merged = []
    for page in pages:
        for item in page:
            key = (item.get("quantity", ""), item.get("name", ""))
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


def read_reward_row(region_bgr: np.ndarray,
                     quantity_band: float = 0.28, name_band: float = 0.22,
                     row_pad: int = 3, side_pad_frac: float = 0.08,
                     allowed_names: list = None) -> list:
    """Auto-detects icon cells in region_bgr (see detect_icon_cells) and OCRs
    each one's quantity badge and item name.

    Reads two areas per cell: the quantity number, which sits top-left of
    the cell, and the item name, which sits lower and is more likely to run
    past the icon's right edge on long names. side_pad_frac adds a small
    margin to the left of the quantity box and the right of the name box so
    a badge/label that's a few pixels wider than the auto-detected icon-art
    boundary doesn't get clipped -- kept small since padding too far in
    either direction starts pulling in the *neighboring* icon's badge/art
    instead when cells sit close together. Vertically, the two text rows are
    located dynamically via detect_text_bands rather than assumed to be a
    fixed top/bottom fraction of the cell -- quantity_band/name_band are
    only used as a fallback if that detection doesn't find two distinct
    bands (e.g. very low-contrast text).

    allowed_names (see core.stage_data.expected_item_names) narrows icon
    identification to just what the current stage can actually reward --
    passed straight through to identify_item_name.
    """
    pytesseract = _get_pytesseract()  # raises TesseractNotAvailable early, before any work
    h, w = region_bgr.shape[:2]

    bands = detect_text_bands(region_bgr)
    # A band that hugs this slice's own bottom edge is very likely bleed from
    # a partially cut-off next row fused onto this one (see detect_row_bands
    # / _drop_cutoff_row) rather than this row's actual name label, which
    # normally leaves a little blank buffer below it before the calibrated
    # crop ends. Prefer an earlier band over trusting an edge-hugging one as
    # "the name" -- picking the wrong one here corrupts every cell in the
    # row, not just the cut-off item.
    while len(bands) > 2 and bands[-1][1] >= h - 2:
        bands = bands[:-1]
    if len(bands) >= 2:
        qty_y0, qty_y1 = bands[0]
        name_y0, name_y1 = bands[-1]
    else:
        qty_y0, qty_y1 = 0, max(1, int(h * quantity_band))
        name_y0, name_y1 = h - max(1, int(h * name_band)), h
    # The icon-art crop (used for color-based identification, below) wants
    # the *unpadded* band edges -- it should exclude as much of the text
    # rows as possible, the opposite of what the OCR crops below want extra
    # margin for.
    icon_y0, icon_y1 = qty_y1, name_y0
    qty_y0 = max(0, qty_y0 - row_pad)
    qty_y1 = min(h, qty_y1 + row_pad)
    name_y0 = max(0, name_y0 - row_pad)
    name_y1 = min(h, name_y1 + row_pad)

    def read_cell(cell_bounds):
        x0, x1 = cell_bounds
        cw = x1 - x0
        if cw < 2:
            return None
        pad = max(2, int(cw * side_pad_frac))

        qty_x0, qty_x1 = max(0, x0 - pad), x1
        name_x0, name_x1 = x0, min(w, x1 + pad)

        qty_cell = region_bgr[qty_y0:qty_y1, qty_x0:qty_x1]
        name_cell = region_bgr[name_y0:name_y1, name_x0:name_x1]
        if qty_cell.shape[0] < 2 or qty_cell.shape[1] < 2 or \
           name_cell.shape[0] < 2 or name_cell.shape[1] < 2:
            return None

        qty_text = _clean_quantity_text(_ocr_best(
            pytesseract, qty_cell, "--psm 7 -c tessedit_char_whitelist=0123456789x,.",
            valid_pattern=_QUANTITY_PATTERN))
        ocr_name_text = _clean_name_text(_ocr_best(
            pytesseract, name_cell, "--psm 7", valid_pattern=_NAME_LOOKS_CLEAN))

        # The icon art itself -- between the quantity badge and the name
        # label -- identifies the item far more reliably than OCRing the
        # label does at this resolution (see identify_item_name).
        icon_cell = region_bgr[icon_y0:icon_y1, x0:x1]
        name_text = identify_item_name(icon_cell, ocr_name_text, allowed_names=allowed_names)

        if not name_text:
            # No existing reference icon matched -- this is either a
            # genuinely new item, or a stage effect never captured before.
            # If it's close to one of this stage's *known* possible rewards
            # (see core.stage_data), trust that confirmed name over the raw
            # OCR guess -- both for the returned reading, and for what gets
            # saved as a new reference icon below (never save under an
            # unverified OCR misread).
            name_text = _best_allowed_match(ocr_name_text, allowed_names) or ocr_name_text

        if name_text:
            _ensure_icon_reference(icon_cell, name_text)

        if not qty_text and not name_text:
            return None  # a detected cell that turned out blank after all
        return {"quantity": qty_text, "name": name_text}

    cells = detect_icon_cells(region_bgr)
    if not cells:
        return []
    # Each cell's OCR is several independent Tesseract *subprocess* calls --
    # real OS processes, not just Python bytecode -- so a thread per cell
    # actually runs them concurrently instead of fighting the GIL, and a
    # row's cells have zero data dependency on each other. executor.map
    # keeps results in the same left-to-right order the cells were submitted
    # in regardless of which subprocess finishes first.
    with ThreadPoolExecutor(max_workers=min(len(cells), 8)) as executor:
        return [r for r in executor.map(read_cell, cells) if r is not None]
