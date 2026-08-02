"""Shared OCR plumbing used by anything that reads small stylized game text
off a screenshot (core.rewards' reward row, core.game_stats' stat grid):
finding/loading the Tesseract engine, capturing a screen region with mss,
and turning a tiny colorful crop into a handful of binarized candidates so
Tesseract has a real shot at it.
"""
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
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
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

_rapid_ocr_engine = None

def get_rapidocr():
    """Returns a configured RapidOCR engine instance, or raises ImportError.

    RapidOCR is the preferred OCR engine (faster and more accurate than both
    Windows OCR and Tesseract for small stylized game text). Configured with
    English language only to avoid loading 6k+ Chinese character models
    (significant performance impact).
    """
    global _rapid_ocr_engine

    if _rapid_ocr_engine:
        return _rapid_ocr_engine
    if _rapid_ocr_engine == "":
        raise ImportError("RapidOCR is not available")

    try:
        from rapidocr_onnxruntime import RapidOCR
        _rapid_ocr_engine = RapidOCR(lang=["en"])
        return _rapid_ocr_engine
    except Exception:
        _rapid_ocr_engine = ""
        raise ImportError("RapidOCR is not available")

def is_rapidocr_available() -> bool:
    """Whether RapidOCR can be used. Cached: only checked once."""
    try:
        get_rapidocr()
        return True
    except ImportError:
        return False

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


def enhanced_candidate_masks(cell_bgr: np.ndarray) -> list:
    """Enhanced candidate generation with multiple scales, interpolations, and
    preprocessing variations. Used for RapidOCR which benefits from diverse
    preprocessing more than Windows OCR/Tesseract.

    Generates candidates with:
    - Multiple upscale factors (8x, 12x, 16x)
    - Multiple interpolation methods (CUBIC, LANCZOS4, LINEAR)
    - Sharpening variants (unsharp mask)
    - Contrast enhancement (CLAHE on LAB color space)
    - Original candidate_masks() binarizations

    This broader sweep significantly improves accuracy for small stylized text
    at the cost of more OCR calls -- mitigated by early-exit pattern in
    ocr_best() (stop as soon as a candidate matches the expected pattern).
    """
    candidates = []

    # Multiple scales and interpolations with preprocessing variants
    for scale in [8, 12, 16]:
        for interp in [cv2.INTER_CUBIC, cv2.INTER_LANCZOS4, cv2.INTER_LINEAR]:
            upscaled = cv2.resize(cell_bgr, None, fx=scale, fy=scale, interpolation=interp)
            candidates.append(upscaled)

            # Sharpened version - unsharp mask
            blurred = cv2.GaussianBlur(upscaled, (0, 0), 3)
            sharpened = cv2.addWeighted(upscaled, 1.5, blurred, -0.5, 0)
            candidates.append(sharpened)

            # Contrast-enhanced version - CLAHE on LAB color space
            lab = cv2.cvtColor(upscaled, cv2.COLOR_BGR2LAB)
            clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
            lab[:, :, 0] = clahe.apply(lab[:, :, 0])
            enhanced = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
            candidates.append(enhanced)

    # Include original binarized masks (still useful for Windows OCR/Tesseract)
    candidates.extend(candidate_masks(cell_bgr, upscale=8))

    return candidates


def ocr_image(img: np.ndarray) -> str:
    """Simple image-to-text OCR, engine-agnostic: RapidOCR → Windows OCR → Tesseract.

    This is a convenience wrapper around ocr_mask that matches the Windows OCR
    ocr_image() API signature - just takes an image and returns text. Used by
    bounty.py and other code that doesn't need fine-grained control over the
    OCR process.

    For more control (whitelist, config, etc.), use ocr_mask() directly.
    """
    try:
        pytesseract = get_pytesseract()
    except TesseractNotAvailable:
        pytesseract = None

    # ocr_mask handles RapidOCR → Windows OCR → Tesseract fallback
    return ocr_mask(pytesseract, img, base_config="--psm 7")

def ocr_lines(img: np.ndarray) -> list:
    """Recognize text while preserving each line's image-space bounds.
    Engine-agnostic: tries RapidOCR first (if available), then Windows OCR.

    Returns a list of dicts, each with:
    - "text": recognized text
    - "x", "y", "w", "h": bounding box (axis-aligned)
    - "cx", "cy": center point

    RapidOCR's rotated bounding boxes are converted to axis-aligned boxes
    for compatibility with existing code.
    """

    if img is None or img.size == 0:
        return []

    if is_rapidocr_available():
        try:
            rapid_engine = get_rapidocr()

            if not img.flags['C_CONTIGUOUS']:
                img = np.ascontiguousarray(img)

            if img.ndim == 2:
                rgb_img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            else:
                rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

            result, _elapse = rapid_engine(rgb_img)
            if result:
                lines = []
                for item in result:
                    bbox, text, _confidence = item

                    xs = [int(p[0]) for p in bbox]
                    ys = [int(p[1]) for p in bbox]
                    x1, x2 = min(xs), max(xs)
                    y1, y2 = min(ys), max(ys)

                    line_dict = {
                        "text": text,
                        "x": x1,
                        "y": y1,
                        "w": x2 - x1,
                        "h": y2 - y1
                    }
                    lines.append(line_dict)
                return lines
        except Exception:
            # Fall through to Windows OCR on any RapidOCR error
            pass

    from core import ocr_windows
    return ocr_windows.ocr_lines(img)

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
    """One OCR pass over one prepared mask, engine-agnostic: RapidOCR when
    available (fastest, most accurate for stylized text), then Windows OCR,
    then Tesseract.

    Whitelist handling:
    - RapidOCR/Windows OCR: only apply if explicitly passed via whitelist param.
      Whitelist from config is Tesseract-specific and not extracted for these engines.
    - Tesseract: uses config as-is (config can include tessedit_char_whitelist).

    This allows callers to get unfiltered output from RapidOCR/Windows (e.g. to
    preserve words like "wave" for pattern matching) while still constraining
    Tesseract when needed.

    RapidOCR expects BGR or RGB images (handles both color and grayscale),
    Windows OCR and Tesseract work with grayscale masks.
    """
    # Try RapidOCR first (fastest, most accurate for small stylized text)
    if is_rapidocr_available():
        try:
            rapid_engine = get_rapidocr()

            # Ensure contiguous array for RapidOCR
            if not mask.flags['C_CONTIGUOUS']:
                mask = np.ascontiguousarray(mask)

            # RapidOCR expects RGB format (OpenCV uses BGR)
            # Handle both grayscale masks and BGR images
            if mask.ndim == 2:  # Grayscale mask
                rgb_mask = cv2.cvtColor(mask, cv2.COLOR_GRAY2RGB)
            else:  # BGR image
                rgb_mask = cv2.cvtColor(mask, cv2.COLOR_BGR2RGB)

            result, _elapse = rapid_engine(rgb_mask)
            if result:
                # Combine all detected text from this candidate
                text = " ".join([line[1] for line in result])

                # Apply whitelist only if explicitly passed (not from config)
                if whitelist:
                    text = "".join(c for c in text if c in whitelist or c.isspace())
                return text.strip()
        except Exception:
            # Fall through to Windows OCR/Tesseract on any RapidOCR error
            pass

    # Fall back to Windows OCR
    from core import ocr_windows
    if ocr_windows.is_available():
        text = ocr_windows.ocr_image(mask)
        # Apply whitelist only if explicitly passed (not from config)
        if whitelist:
            text = "".join(c for c in text if c in whitelist or c.isspace())
        return text.strip()

    # Final fallback to Tesseract (uses config which may include whitelist)
    if pytesseract is None:
        return ""
    return pytesseract.image_to_string(mask, config=base_config).strip()


def ocr_best(pytesseract, cell_bgr: np.ndarray, base_config: str,
             psm_modes: tuple = (7, 8), valid_pattern=None) -> str:
    """Runs OCR against multiple preprocessed candidates, keeping whichever
    result scored best (see score_text).

    EARLY EXIT: Stops as soon as a result matches valid_pattern, since that's
    already the top score tier and nothing later could beat it. Critical for
    performance with enhanced candidates (60+ variations vs original 3 masks).

    Engine selection:
    - RapidOCR (if available): tries enhanced color candidates first - multiple
      scales/interpolations/preprocessing. RapidOCR works best with color images,
      not binarized masks. No PSM modes (not applicable).
    - Windows OCR (if available): uses traditional binarized masks. No PSM modes
      (not applicable), so only one pass per mask.
    - Tesseract (fallback): uses traditional binarized masks with multiple PSM
      segmentation modes per mask.

    The RapidOCR path generates many more candidates but the early-exit pattern
    keeps actual OCR calls reasonable - typically finds a match in the first
    few tries for clean text.
    """
    # Try RapidOCR first with enhanced color candidates (most accurate for stylized text)
    if is_rapidocr_available():
        best = ""
        best_score = (-1, -1)

        for candidate in enhanced_candidate_masks(cell_bgr):
            text = ocr_mask(pytesseract, candidate, base_config)
            score = score_text(text, valid_pattern)
            if score > best_score:
                best_score = score
                best = text
            # Early exit on pattern match (top score tier)
            if valid_pattern is not None and score[0] == 1:
                return best

        # If RapidOCR found something, return it (even without pattern match)
        if best:
            return best
        # Otherwise fall through to Windows OCR/Tesseract

    # Windows OCR or Tesseract fallback with traditional binarized masks.
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
            # Early exit on pattern match (top score tier)
            if valid_pattern is not None and score[0] == 1:
                return best
    return best
