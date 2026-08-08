"""Windows' built-in OCR (Windows.Media.Ocr, via WinRT Python projections)
-- the preferred OCR engine on Windows 10/11.

Why it exists: Tesseract is a separate ~50 MB native installer the user
has to download and put on PATH, and it spawns a subprocess per read
(slow -- ~0.4s each). Windows OCR ships with the OS, needs nothing
installed, and read the wave HUD ~16x faster than Tesseract while
matching it exactly on every test frame. winsdk is used on Python versions
where it still has wheels; Python 3.13 uses the successor winrt-Windows.*
packages. The chosen projection is bundled into the build, so from the
user's side there is genuinely nothing extra to install. Tesseract stays
as the fallback (older Windows without the OCR component, or macOS).

is_available() gates everything and caches its result; ocr_image() runs
one recognition on a numpy image. The mask-sweep + voting that turns a
noisy HUD crop into a confident reading lives in the callers
(core.wave / core.ocr), same as the Tesseract path -- this module is
just the single-image engine.
"""
import asyncio
import importlib
import logging
from dataclasses import dataclass
import threading

import cv2

_engine = None
_backend = None
_checked = False
_unavailable_reason = ""
_warned_unavailable = False
_lock = threading.Lock()  # serialize recognitions; one at a time is plenty for our cadence

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _WinRtBackend:
    name: str
    ocr: object
    globalization: object
    imaging: object
    crypto: object
    buffer_alpha_arg: bool


def _load_backend(prefix: str, *, buffer_alpha_arg: bool) -> _WinRtBackend:
    """Load one WinRT projection.

    The winrt-Windows.* packages are split more aggressively than winsdk.
    Importing Foundation.Collections up front matters because iterating
    result.lines/line.words can otherwise raise ModuleNotFoundError later
    inside ocr_lines(), which used to get swallowed as "no text found".
    """
    backend = _WinRtBackend(
        name=prefix,
        ocr=importlib.import_module(f"{prefix}.windows.media.ocr"),
        globalization=importlib.import_module(f"{prefix}.windows.globalization"),
        imaging=importlib.import_module(f"{prefix}.windows.graphics.imaging"),
        crypto=importlib.import_module(f"{prefix}.windows.security.cryptography"),
        buffer_alpha_arg=buffer_alpha_arg,
    )
    if prefix == "winrt":
        importlib.import_module("winrt.windows.foundation")
        importlib.import_module("winrt.windows.foundation.collections")
        importlib.import_module("winrt.windows.storage.streams")
    return backend


def _try_create_engine():
    """Return (backend, engine, error_detail)."""
    errors = []
    for prefix, alpha_arg in (("winsdk", True), ("winrt", False)):
        try:
            backend = _load_backend(prefix, buffer_alpha_arg=alpha_arg)
            engine = (
                backend.ocr.OcrEngine.try_create_from_language(
                    backend.globalization.Language("en-US")
                )
                or backend.ocr.OcrEngine.try_create_from_user_profile_languages()
            )
            if engine is not None:
                return backend, engine, ""
            errors.append(f"{prefix}: Windows OCR engine unavailable")
        except Exception as exc:
            errors.append(f"{prefix}: {exc.__class__.__name__}: {exc}")
    return None, None, "; ".join(errors)


def is_available() -> bool:
    """Whether Windows OCR can be used. Cached: the import + engine
    creation is only attempted once, and the answer never changes within a
    run (the OS OCR component doesn't come and go)."""
    global _backend, _engine, _checked, _unavailable_reason
    if _checked:
        return _engine is not None
    _checked = True
    _backend, _engine, _unavailable_reason = _try_create_engine()
    return _engine is not None


def backend_name() -> str:
    """Name of the active WinRT projection, or an empty string if unavailable."""
    return _backend.name if is_available() else ""


def unavailable_reason() -> str:
    """Why Windows OCR is unavailable after probing. Mainly for diagnostics."""
    is_available()
    return _unavailable_reason


def _warn_if_unavailable() -> None:
    global _warned_unavailable
    if _warned_unavailable:
        return
    _warned_unavailable = True
    detail = unavailable_reason()
    log.warning("Windows OCR unavailable%s", f": {detail}" if detail else "")


def _software_bitmap_from_image(img):
    bgr = img if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    bgra = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
    h, w = bgra.shape[:2]
    buf = _backend.crypto.CryptographicBuffer.create_from_byte_array(bytes(bgra.tobytes()))
    if _backend.buffer_alpha_arg:
        return _backend.imaging.SoftwareBitmap.create_copy_from_buffer(
            buf,
            _backend.imaging.BitmapPixelFormat.BGRA8,
            w,
            h,
            _backend.imaging.BitmapAlphaMode.PREMULTIPLIED,
        )
    return _backend.imaging.SoftwareBitmap.create_copy_from_buffer(
        buf,
        _backend.imaging.BitmapPixelFormat.BGRA8,
        w,
        h,
    )


def _recognize(img):
    bitmap = _software_bitmap_from_image(img)
    with _lock:
        # A fresh loop per call (not a cached one): recognitions can come
        # from different threads -- an asyncio loop is bound to the
        # thread that created it, so reusing one across threads raises.
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_engine.recognize_async(bitmap))
        finally:
            loop.close()


def ocr_image(img) -> str:
    """Recognize text in a numpy image (grayscale or BGR). Returns the
    recognized text as one space-joined string, or '' on any failure --
    callers regex/whitelist it themselves, exactly like a Tesseract
    result. Never raises: OCR is best-effort everywhere it's used."""
    if not is_available():
        _warn_if_unavailable()
        return ""
    try:
        result = _recognize(img)
        return result.text or ""
    except Exception:
        return ""


def ocr_lines(img) -> list:
    """Recognize text while preserving each line's image-space bounds."""
    if not is_available():
        _warn_if_unavailable()
        return []
    try:
        result = _recognize(img)
        lines = []
        for line in result.lines:
            words = list(line.words)
            if not words:
                continue
            rects = [word.bounding_rect for word in words]
            x1 = min(int(rect.x) for rect in rects)
            y1 = min(int(rect.y) for rect in rects)
            x2 = max(int(rect.x + rect.width) for rect in rects)
            y2 = max(int(rect.y + rect.height) for rect in rects)
            lines.append({
                "text": line.text or "",
                "x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1,
                "cx": (x1 + x2) // 2, "cy": (y1 + y2) // 2,
            })
        return lines
    except Exception:
        return []
