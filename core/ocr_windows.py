"""Windows' built-in OCR (Windows.Media.Ocr, via the winsdk WinRT
projection) -- the preferred OCR engine on Windows 10/11.

Why it exists: Tesseract is a separate ~50 MB native installer the user
has to download and put on PATH, and it spawns a subprocess per read
(slow -- ~0.4s each). Windows OCR ships with the OS, needs nothing
installed, and read the wave HUD ~16x faster than Tesseract while
matching it exactly on every test frame. winsdk itself is bundled into
the build, so from the user's side there is genuinely nothing to
install. Tesseract stays as the fallback (older Windows without the OCR
component, or macOS).

is_available() gates everything and caches its result; ocr_image() runs
one recognition on a numpy image. The mask-sweep + voting that turns a
noisy HUD crop into a confident reading lives in the callers
(core.wave / core.ocr), same as the Tesseract path -- this module is
just the single-image engine.
"""
import asyncio
import threading

import cv2

_engine = None
_checked = False
_lock = threading.Lock()  # serialize recognitions; one at a time is plenty for our cadence


def is_available() -> bool:
    """Whether Windows OCR can be used. Cached: the import + engine
    creation is only attempted once, and the answer never changes within a
    run (the OS OCR component doesn't come and go)."""
    global _engine, _checked
    if _checked:
        return _engine is not None
    _checked = True
    try:
        from winsdk.windows.media.ocr import OcrEngine
        from winsdk.windows.globalization import Language
        _engine = (OcrEngine.try_create_from_language(Language("en-US"))
                   or OcrEngine.try_create_from_user_profile_languages())
    except Exception:
        _engine = None  # winsdk missing, non-Windows, or OCR component absent
    return _engine is not None


def ocr_image(img) -> str:
    """Recognize text in a numpy image (grayscale or BGR). Returns the
    recognized text as one space-joined string, or '' on any failure --
    callers regex/whitelist it themselves, exactly like a Tesseract
    result. Never raises: OCR is best-effort everywhere it's used."""
    if not is_available():
        return ""
    try:
        from winsdk.windows.graphics.imaging import SoftwareBitmap, BitmapPixelFormat, BitmapAlphaMode
        from winsdk.windows.security.cryptography import CryptographicBuffer

        bgr = img if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        bgra = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
        h, w = bgra.shape[:2]
        buf = CryptographicBuffer.create_from_byte_array(bytes(bgra.tobytes()))
        sb = SoftwareBitmap.create_copy_from_buffer(buf, BitmapPixelFormat.BGRA8, w, h,
                                                     BitmapAlphaMode.PREMULTIPLIED)
        with _lock:
            # A fresh loop per call (not a cached one): recognitions can come
            # from different threads -- an asyncio loop is bound to the
            # thread that created it, so reusing one across threads raises.
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(_engine.recognize_async(sb))
            finally:
                loop.close()
        return result.text or ""
    except Exception:
        return ""
