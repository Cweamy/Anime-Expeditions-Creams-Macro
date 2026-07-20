"""Reads the "Wait for Wave" badge (Battle > Wait for Wave block): a small
"<current> / <max> wave" readout (e.g. "0 / 15 wave") in the top HUD.

The icon and "wave" label share the crop with the two numbers -- a
digit+slash-only Tesseract whitelist drops the letters instead of needing
to isolate the numbers by color/position first (see core.game_stats for
that alternative approach, used there because its value/label text is the
same color and needs isolating; here the whitelist alone is enough).

Deliberately does NOT use core.ocr.ocr_best's usual "stop at the first
mask/psm combo that matches the valid pattern" shortcut: that shortcut
assumes a clean pattern match is itself good evidence of a correct read,
which holds for e.g. a quantity ("^[\\d,]+$") but not here -- almost any
mask produces SOME "<digits> / <digits>"-shaped text regardless of
whether the digits themselves are right, so the first candidate tried can
"win" on a misread (confirmed against a real capture: a clearly legible
"0 / 15" mask lost to an earlier, uglier mask's "0 / 18"). Instead this
runs the full candidate sweep and majority-votes across every reading
that at least parsed cleanly -- a wrong digit in one mask gets outvoted
by the other masks agreeing on the right one, rather than deciding on
whichever came first.
"""
import re
from collections import Counter

from core.ocr import get_pytesseract, candidate_masks

_WAVE_PATTERN = re.compile(r"^\d{1,4}\s?/\s?\d{1,4}$")
_WHITELIST = "0123456789/"
# psm 8 ("single word") never once produced a clean "<digits> / <digits>"
# match in testing -- it fuses the two numbers together without the "/"
# every time ("0/15" -> "015") -- so it's dropped rather than spending a
# third of this sweep's subprocess calls on a mode that's never actually
# contributed a vote.
_PSM_MODES = (7, 11)
# candidate_masks' default sharpen_amount (1.5, tuned for the reward/stat
# text elsewhere in this codebase) over-sharpens THIS badge's font at this
# size -- confirmed against a real capture, it turns "5" into "8" on most
# mask/psm combos ("0 / 15" -> "0 / 18"). Lower/no sharpening reads it
# correctly instead, so every level gets tried and voted across rather
# than trusting one fixed amount.
_SHARPEN_LEVELS = (0, 0.5, 1.5)


def read_wave(region_bgr):
    """Returns (current, max) ints, or (None, None) if nothing in the OCR
    sweep produced a clean "<digits> / <digits>" reading at all. Votes
    across every mask/psm/sharpen-level combination instead of stopping at
    the first pattern match (core.ocr.ocr_best's usual shortcut) -- that
    shortcut assumes a clean pattern match is itself good evidence of a
    correct read, which doesn't hold here: almost any mask produces SOME
    "<digits> / <digits>"-shaped text regardless of whether the digits
    themselves are right, so the first candidate tried can "win" on a
    misread. A wrong digit from one combination gets outvoted by the
    others agreeing on the right one instead.
    """
    pytesseract = get_pytesseract()
    config_base = f"--psm 7 -c tessedit_char_whitelist={_WHITELIST}"
    votes = Counter()
    for sharpen in _SHARPEN_LEVELS:
        for mask in candidate_masks(region_bgr, sharpen_amount=sharpen):
            for psm in _PSM_MODES:
                config = re.sub(r"--psm \d+", f"--psm {psm}", config_base)
                text = pytesseract.image_to_string(mask, config=config).strip()
                text = re.sub(r"\s+", " ", text)
                if not _WAVE_PATTERN.match(text):
                    continue
                nums = re.findall(r"\d+", text)
                if len(nums) == 2:
                    votes[(int(nums[0]), int(nums[1]))] += 1
    if not votes:
        return None, None
    (current, maximum), _count = votes.most_common(1)[0]
    return current, maximum


def save_region_preview(region_bgr, path: str) -> None:
    """Saves exactly what read_wave would see -- no annotations -- so a bad
    calibration (wrong region entirely) is visible at a glance, same
    convention as core.game_stats.save_region_preview."""
    import cv2
    cv2.imwrite(path, region_bgr)
