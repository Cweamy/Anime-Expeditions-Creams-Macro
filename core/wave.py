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

import cv2

from core.ocr import get_pytesseract, candidate_masks, ocr_mask
from core import ocr_windows

_WAVE_PATTERN = re.compile(r"^\d{1,4}\s?/\s?\d{1,4}$")
_INFINITE_WAVE_PATTERN = re.compile(
    r"(\d{1,4})\s*/\s*(?:∞|inf(?:inite|inity)?)",
    re.IGNORECASE,
)
_CURRENT_ONLY_WAVE_PATTERN = re.compile(
    r"\b([0-9OS]{1,4})\s+wave\b",
    re.IGNORECASE,
)
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
    # Windows OCR when available (no Tesseract install needed), else
    # Tesseract. Windows OCR has no psm modes, so that loop collapses to a
    # single pass per mask there -- verified to match Tesseract's readings
    # on every test frame at ~16x the speed.
    use_windows = ocr_windows.is_available()
    pytesseract = None if use_windows else get_pytesseract()
    psm_modes = (7,) if use_windows else _PSM_MODES
    config_base = f"--psm 7 -c tessedit_char_whitelist={_WHITELIST}"
    votes = Counter()

    def add_vote(text, weight=1):
        # Windows OCR reads the vertical bar as '|' or letter 'l' about as
        # often as '/'; normalize before matching.
        text = re.sub(r"\s+", " ", text.replace("|", "/").replace("l", "/"))
        # Infinite mode does not render a slash or maximum at all; its HUD is
        # simply "<current> wave" (confirmed live as "6 wave"). Requiring the
        # word "wave" keeps an unrelated lone HUD number from being accepted.
        if "/" not in text:
            if "-" in text:
                # Stylized 40 has been observed as "1-10 wave". Treat the
                # inserted dash as corruption and let another preprocessing
                # vote read it, rather than silently collapsing it to 10.
                return
            current_only = _CURRENT_ONLY_WAVE_PATTERN.search(text)
            if current_only:
                # The live two-digit "25" render is commonly returned as
                # "2S wave". Correct only the tightly bounded numeric token;
                # never rewrite arbitrary OCR text.
                raw_token = current_only.group(1)
                # A lone OCR "O wave" is ambiguous (the icon/outline is
                # often hallucinated as O). Require at least one real digit;
                # "2S wave" remains valid and normalizes to 25.
                if not any(char.isdigit() for char in raw_token):
                    return
                token = raw_token.upper().translate(
                    str.maketrans({"O": "0", "S": "5"})
                )
                votes[(int(token), None)] += weight
            return
        infinite = _INFINITE_WAVE_PATTERN.search(text)
        if infinite:
            votes[(int(infinite.group(1)), "∞")] += weight
            return
        nums = re.findall(r"\d+", text)
        if not (_WAVE_PATTERN.match(text) or (len(nums) == 2 and "/" in text)):
            return
        if len(nums) != 2:
            return
        current, maximum = int(nums[0]), int(nums[1])
        # The blue wave icon sits immediately before the current number. On
        # a noisy mask it can look like an extra leading digit (reported:
        # 4/15 -> 24/15). A finite stage cannot be beyond its own maximum.
        if maximum <= 0 or current > maximum:
            return
        votes[(current, maximum)] += weight

    if use_windows:
        # Windows OCR preserves this tiny outlined font much better in color
        # than after binarization. Across 160 live captures spanning several
        # wave transitions, a 3x Lanczos color pass read 19/20 sampled frames
        # versus 11/20 for the existing mask sweep. Give that clean pass the
        # deciding weight, while keeping every mask below as fallback.
        color_3x = cv2.resize(region_bgr, None, fx=3, fy=3, interpolation=cv2.INTER_LANCZOS4)
        color_text = ocr_windows.ocr_image(color_3x)
        add_vote(color_text, weight=3)
        # Current-only two-digit counters can lose either all digits (wave
        # 24) or just the leading digit (52 -> 2) in the full crop. A tighter
        # high-resolution pass consistently retained the complete token, so
        # run it every time and let finite slash/max votes retain structural
        # priority at selection below.
        badge_top = region_bgr[:28, :90]
        badge_10x = cv2.resize(
            badge_top, None, fx=10, fy=10, interpolation=cv2.INTER_LANCZOS4
        )
        add_vote(ocr_windows.ocr_image(badge_10x), weight=4)

    for sharpen in _SHARPEN_LEVELS:
        for mask in candidate_masks(region_bgr, sharpen_amount=sharpen):
            for psm in psm_modes:
                config = re.sub(r"--psm \d+", f"--psm {psm}", config_base)
                # Current-only infinite counters need the word "wave" as
                # structural proof. ocr_mask applies the numeric whitelist
                # after Windows OCR and used to erase that proof even when
                # the engine had correctly read "25 wave" or "40 wave".
                text = (
                    ocr_windows.ocr_image(mask)
                    if use_windows
                    else ocr_mask(pytesseract, mask, config, whitelist=_WHITELIST)
                )
                add_vote(text)
    if not votes:
        return None, None
    # A recognized finite slash/max counter is stronger structural evidence
    # than the current-only fallback. This prevents a tight crop that lost
    # the left half of "10 / 15 wave" from turning its trailing "15 wave"
    # into an unlimited-wave reading.
    finite_votes = Counter({
        pair: count
        for pair, count in votes.items()
        if isinstance(pair[1], int)
    })
    selected = finite_votes or votes
    (current, maximum), _count = selected.most_common(1)[0]
    return current, maximum


def save_region_preview(region_bgr, path: str) -> None:
    """Saves exactly what read_wave would see -- no annotations -- so a bad
    calibration (wrong region entirely) is visible at a glance, same
    convention as core.game_stats.save_region_preview."""
    import cv2
    cv2.imwrite(path, region_bgr)
