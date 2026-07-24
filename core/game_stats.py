"""Reads the "Game Stats" panel on a Victory screen: a fixed 2x2 grid of
labeled values (Clear Time, Total Yen, Total Kills, Total Damage) -- unlike
the reward row (core.rewards), the count and layout here never change, so
this doesn't need the reward reader's icon-count auto-detection.

The label ("Clear Time") and its value ("5m, 55s") share a row but differ in
color: labels render white/gray, values render in a saturated color (cyan or
gold). That's a far more reliable way to isolate just the value than trying
to OCR the whole row and strip a known label string off the front -- it
survives the label being misread entirely, and doesn't care exactly where
the calibrated box's edges land as long as the grid is roughly inside it.
"""
import re

import numpy as np
import cv2

from core.ocr import get_pytesseract, ocr_best

STAT_KEYS = ("clear_time", "total_yen", "total_kills", "total_damage")

# Grid position (row, col) for each stat, matching the in-game panel layout.
_GRID_POSITION = {
    "clear_time": (0, 0),
    "total_yen": (0, 1),
    "total_kills": (1, 0),
    "total_damage": (1, 1),
}

_TIME_PATTERN = re.compile(r"^\d+m,?\s?\d+s$")
_NUMBER_PATTERN = re.compile(r"^[\d,]+$")
_NUMBER_EXTRACT = re.compile(r"[\d,]+")
_TIME_WHITELIST = "0123456789ms, "
_NUMBER_WHITELIST = "0123456789,"


def _isolate_value(quadrant_bgr: np.ndarray, pad: int = 4) -> np.ndarray:
    """Crops a quadrant down to just its saturated-color value text, dropping
    the white/gray label text and any background art around it -- see the
    module docstring. Falls back to the full quadrant if nothing saturated
    enough is found (e.g. a value that happens to render in white/gray)."""
    hsv = cv2.cvtColor(quadrant_bgr, cv2.COLOR_BGR2HSV)
    sat, val = hsv[:, :, 1], hsv[:, :, 2]
    mask = (sat > 80) & (val > 120)
    ys, xs = np.where(mask)
    if len(xs) < 4:
        return quadrant_bgr

    h, w = quadrant_bgr.shape[:2]
    x0, x1 = max(0, xs.min() - pad), min(w, xs.max() + 1 + pad)
    y0, y1 = max(0, ys.min() - pad), min(h, ys.max() + 1 + pad)
    return quadrant_bgr[y0:y1, x0:x1]


def _normalize_clear_time(text: str) -> str:
    """Rebuilds "Xm, Ys" from whatever digit groups OCR actually found,
    instead of trusting the literal characters around them.

    "s" and "5" are two of the most visually confusable glyphs there are,
    and at the *end* of the string there's no following letter to force a
    digit/letter split, so a genuine "47s" commonly comes back fused into a
    single "475" run (or with the 's' relocated entirely, e.g. "5sm, 475").
    Letters elsewhere in the string are unambiguous (a digit run always
    stops cleanly at a real letter), so the fix only has to special-case the
    tail: seconds are always 0-59, i.e. at most 2 digits, so a trailing
    digit run longer than that has an extra digit which can only be a
    misread unit letter -- drop it rather than the whole reading.
    """
    digit_runs = re.findall(r"\d+", text)
    if len(digit_runs) < 2:
        return text  # not enough to reconstruct -- return raw OCR, better than nothing
    minutes, seconds = digit_runs[0], digit_runs[1]
    if len(seconds) > 2:
        seconds = seconds[:2]
    return f"{minutes}m, {seconds}s"


def _read_value(pytesseract, quadrant_bgr: np.ndarray, key: str) -> str:
    crop = _isolate_value(quadrant_bgr)
    if crop.shape[0] < 2 or crop.shape[1] < 2:
        return ""

    if key == "clear_time":
        text = ocr_best(pytesseract, crop, f"--psm 7 -c tessedit_char_whitelist={_TIME_WHITELIST}",
                         valid_pattern=_TIME_PATTERN)
        return _normalize_clear_time(text)

    text = ocr_best(pytesseract, crop, f"--psm 7 -c tessedit_char_whitelist={_NUMBER_WHITELIST}",
                     valid_pattern=_NUMBER_PATTERN)
    match = _NUMBER_EXTRACT.search(text)
    return match.group(0) if match else text


def save_region_preview(region_bgr: np.ndarray, path: str) -> None:
    """Saves exactly what Read Game Stats would capture -- no annotations --
    so a bad calibration (wrong region entirely) is visible at a glance."""
    cv2.imwrite(path, region_bgr)


def read_game_stats(region_bgr: np.ndarray) -> dict:
    """Splits the calibrated region into a 2x2 grid and OCRs each quadrant's
    value. The region should be calibrated tightly around the 4-stat grid
    (Settings > Debug > Game Stats) -- everything below assumes an even
    halving gets close enough to each quadrant, since _isolate_value then
    locates the actual value text precisely within whatever it's handed."""
    # Windows OCR when available means no Tesseract needed at all; only
    # fall back to (and require) Tesseract when Windows OCR isn't there.
    from core import ocr_windows
    pytesseract = None if ocr_windows.is_available() else get_pytesseract()
    h, w = region_bgr.shape[:2]
    mid_y, mid_x = h // 2, w // 2

    results = {}
    for key, (row, col) in _GRID_POSITION.items():
        y0, y1 = (0, mid_y) if row == 0 else (mid_y, h)
        x0, x1 = (0, mid_x) if col == 0 else (mid_x, w)
        quadrant = region_bgr[y0:y1, x0:x1]
        results[key] = _read_value(pytesseract, quadrant, key)
    return results
