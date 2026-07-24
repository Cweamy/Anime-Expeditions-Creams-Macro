"""Shared OCR plumbing used by anything that reads small stylized game text
off a screenshot (core.rewards' reward row, core.game_stats' stat grid):
finding/loading the Tesseract engine, capturing a screen region with mss,
and turning a tiny colorful crop into a handful of binarized candidates so
Tesseract has a real shot at it.
"""
import os
import re
import subprocess
import numpy as np
import cv2

# Winget/the UB-Mannheim installer both drop it here by default. A fresh
# install isn't on PATH until the shell/session restarts, so check this
# explicit path as a fallback instead of making every user restart their
# terminal (or the whole macro's launch environment) just to pick it up.
_FALLBACK_TESSERACT_PATHS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)

# get_pytesseract() runs on every single OCR read (every stat grab -- reward
# reading no longer uses OCR at all, see core.rewards' module docstring),
# so the actual "is tesseract there" probe below is memoized here instead of
# re-run each time -- besides being wasteful, pytesseract.get_tesseract_
# version() is decorated @run_once but that only actually caches when called
# with cached=True (never, here), so left uncached it was re-spawning a real
# `tesseract --version` subprocess on every single OCR read. That subprocess
# call also doesn't hide its console window the way pytesseract's main OCR
# path (run_tesseract) does -- between the two, that's what was flashing a
# cmd window seemingly at random during normal use. Probing it ourselves
# with CREATE_NO_WINDOW instead of going through pytesseract.
# get_tesseract_version() fixes both: one check ever, and it's silent.
_resolved_tesseract_cmd = None  # None = not checked yet, "" = checked and unavailable


def _tesseract_runs(cmd: str) -> bool:
    try:
        subprocess.run(
            [cmd, "--version"], capture_output=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return True
    except Exception:
        return False


class TesseractNotAvailable(Exception):
    """The pytesseract *package* is present but the Tesseract OCR *engine*
    (a separate native binary, not something pip installs) isn't found."""


def reset_tesseract_cache() -> None:
    """Clears the memoized "is tesseract there" result -- called after
    core.tesseract_installer.install_tesseract() succeeds so the very next
    OCR read re-probes and picks up the freshly installed engine instead of
    still raising off the stale "confirmed unavailable" result cached
    before the install ran."""
    global _resolved_tesseract_cmd
    _resolved_tesseract_cmd = None


def get_pytesseract():
    global _resolved_tesseract_cmd

    try:
        import pytesseract
    except ImportError as exc:
        raise TesseractNotAvailable(
            "pytesseract isn't installed (pip install pytesseract)."
        ) from exc

    if _resolved_tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = _resolved_tesseract_cmd
        return pytesseract
    if _resolved_tesseract_cmd == "":  # already checked, confirmed unavailable
        raise TesseractNotAvailable(
            "Tesseract OCR engine not found. Install it from "
            "https://github.com/UB-Mannheim/tesseract/wiki (Windows build), then "
            "make sure tesseract.exe is on PATH, or set "
            "pytesseract.pytesseract.tesseract_cmd to its full path."
        )

    for candidate in (pytesseract.pytesseract.tesseract_cmd, *_FALLBACK_TESSERACT_PATHS):
        if _tesseract_runs(candidate):
            _resolved_tesseract_cmd = candidate
            pytesseract.pytesseract.tesseract_cmd = candidate
            return pytesseract

    _resolved_tesseract_cmd = ""
    raise TesseractNotAvailable(
        "Tesseract OCR engine not found. Install it from "
        "https://github.com/UB-Mannheim/tesseract/wiki (Windows build), then "
        "make sure tesseract.exe is on PATH, or set "
        "pytesseract.pytesseract.tesseract_cmd to its full path."
    )


from . import mss_manager


def capture_region(left: int, top: int, width: int, height: int) -> np.ndarray:
    """Screenshots a screen-space rect, returns it as a BGR numpy array
    (OpenCV's native order) ready for cv2 preprocessing."""
    try:
        sct = mss_manager.get_mss()
        shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
    except Exception:
        mss_manager.close_mss()
        raise
    bgra = np.frombuffer(shot.raw, dtype=np.uint8).reshape(shot.height, shot.width, 4)
    return cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)


def sample_color_matches(left: int, top: int, width: int, height: int,
                          expected_rgb_hex: int, tolerance: int = 20) -> bool:
    """Grabs a small screen-space patch and checks whether its average color
    is close to expected_rgb_hex (e.g. 0x373737) -- used to detect a fixed
    UI element (like a scrollbar track, which only renders when a panel's
    content overflows) by its known color rather than OCRing anything.
    Averaged over the patch instead of a single pixel so antialiasing/
    compression noise at the sampled point doesn't cause a false miss."""
    patch = capture_region(left, top, max(1, width), max(1, height))
    b, g, r = patch.reshape(-1, 3).mean(axis=0)
    expected_r = (expected_rgb_hex >> 16) & 0xFF
    expected_g = (expected_rgb_hex >> 8) & 0xFF
    expected_b = expected_rgb_hex & 0xFF
    return (abs(r - expected_r) <= tolerance and
            abs(g - expected_g) <= tolerance and
            abs(b - expected_b) <= tolerance)


def candidate_masks(cell_bgr: np.ndarray, upscale: int = 6, sharpen_amount: float = 1.5) -> list:
    """Several different binarizations of the same crop, not just one: a
    single global-Otsu threshold falls apart when the text sits on top of
    colorful art (this UI's text is bright/white or a saturated color with a
    dark outline, but what's behind it can be any color/brightness, which
    throws off a plain split-the-histogram-in-half threshold). Trying a few
    and keeping whichever one Tesseract can actually read is far more robust
    than committing to a single strategy blind.

    The upscale + Lanczos + unsharp combination matters specifically because
    this UI's text is only a handful of pixels tall in the raw capture --
    cubic interpolation invents curvature between those few real samples
    that isn't in the source font (a straight "1" starts looking like a
    curved "5"/"S" to Tesseract). Lanczos holds sharper, straighter edges
    through the upscale, and an unsharp mask on top punches the stroke
    edges back up before they get softened again by denoising.
    """
    h, w = cell_bgr.shape[:2]
    big = cv2.resize(cell_bgr, (w * upscale, h * upscale), interpolation=cv2.INTER_LANCZOS4)
    gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
    if sharpen_amount:
        blurred = cv2.GaussianBlur(gray, (0, 0), 3)
        gray = cv2.addWeighted(gray, 1 + sharpen_amount, blurred, -sharpen_amount, 0)
    denoised = cv2.bilateralFilter(gray, 5, 40, 40)  # denoise while keeping glyph edges sharp

    masks = []

    # Otsu: fine when the crop's background is roughly flat/bimodal.
    _, otsu = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if np.mean(otsu) < 127:
        otsu = cv2.bitwise_not(otsu)
    masks.append(otsu)

    # Bright-pixel isolation: keeps only near-white pixels regardless of how
    # colorful/dark the art behind them is, then closes small gaps antialiasing
    # leaves in thin strokes. This is the one that should carry stylized text
    # over busy backgrounds.
    _, bright = cv2.threshold(denoised, 185, 255, cv2.THRESH_BINARY)
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, np.ones((2, 2), np.uint8))
    masks.append(cv2.bitwise_not(bright))  # dark-on-light, what Tesseract wants

    # Adaptive threshold: handles uneven local lighting/gradients across the
    # crop that neither global method above can.
    adaptive = cv2.adaptiveThreshold(
        denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 25, 10
    )
    masks.append(adaptive)

    return masks


def score_text(text: str, valid_pattern) -> tuple:
    """Ranks a candidate OCR result: a string that actually matches the
    expected shape (e.g. "125x") beats any raw character count, since a
    longer garbled string (art noise misread as extra characters) would
    otherwise "win" over a shorter but correct reading just by having more
    characters."""
    alnum = sum(c.isalnum() for c in text)
    if valid_pattern is not None and valid_pattern.fullmatch(text):
        return (1, alnum)
    return (0, alnum)


def _whitelist_from_config(base_config: str) -> str:
    """Pull the tessedit_char_whitelist out of a Tesseract config string --
    Windows OCR has no whitelist option, so its output is filtered to those
    chars instead (same effect, applied after the fact)."""
    m = re.search(r"tessedit_char_whitelist=(\S+)", base_config)
    return m.group(1) if m else ""


def ocr_mask(pytesseract, mask: np.ndarray, base_config: str = "", whitelist: str = None) -> str:
    """One OCR pass over one prepared mask, engine-agnostic: Windows OCR
    when available (core.ocr_windows), else Tesseract. Windows output is
    filtered to the whitelist (explicit arg, or parsed from base_config)
    so both engines yield the same character set; Tesseract uses the
    config as-is. Callers do their own regex/scoring on the result."""
    from core import ocr_windows
    if ocr_windows.is_available():
        text = ocr_windows.ocr_image(mask)
        wl = whitelist if whitelist is not None else _whitelist_from_config(base_config)
        if wl:
            text = "".join(c for c in text if c in wl or c.isspace())
        return text.strip()
    if pytesseract is None:
        return ""
    return pytesseract.image_to_string(mask, config=base_config).strip()


def ocr_best(pytesseract, cell_bgr: np.ndarray, base_config: str,
             psm_modes: tuple = (7, 8), valid_pattern=None) -> str:
    """Runs OCR against every candidate mask for this crop -- and, since the
    text is a single short token/line, against both "one line" (psm 7) and
    "one word" (psm 8) segmentation, which don't always agree -- and keeps
    whichever combination scored best (see score_text).

    Stops as soon as a result actually matches valid_pattern: each mask/psm
    combo is its own Tesseract subprocess (real spawn overhead on Windows),
    so sweeping all of them on every field of every read adds up. A pattern
    match is already the top score tier score_text can give, so nothing
    later in the sweep could beat it anyway.
    """
    # Windows OCR ignores the psm segmentation modes (it has no equivalent),
    # so there's nothing gained by looping them there -- one pass per mask.
    from core import ocr_windows
    use_windows = ocr_windows.is_available()
    effective_psm = (psm_modes[0],) if use_windows else psm_modes

    best = ""
    best_score = (-1, -1)
    for mask in candidate_masks(cell_bgr):
        for psm in effective_psm:
            config = re.sub(r"--psm \d+", f"--psm {psm}", base_config)
            text = ocr_mask(pytesseract, mask, config)
            score = score_text(text, valid_pattern)
            if score > best_score:
                best_score = score
                best = text
            if valid_pattern is not None and score[0] == 1:
                return best
    return best
