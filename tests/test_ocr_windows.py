from types import SimpleNamespace

import numpy as np

from core import ocr, ocr_windows


def _reset_ocr_windows():
    ocr_windows._engine = None
    ocr_windows._backend = None
    ocr_windows._checked = False
    ocr_windows._unavailable_reason = ""
    ocr_windows._warned_unavailable = False


class _FakeLanguage:
    def __init__(self, tag):
        self.tag = tag


class _FakeOcrEngine:
    @staticmethod
    def try_create_from_language(_language):
        return object()

    @staticmethod
    def try_create_from_user_profile_languages():
        return object()


def _fake_winrt_modules(*, include_collections=True):
    modules = {
        "winrt.windows.media.ocr": SimpleNamespace(OcrEngine=_FakeOcrEngine),
        "winrt.windows.globalization": SimpleNamespace(Language=_FakeLanguage),
        "winrt.windows.graphics.imaging": SimpleNamespace(),
        "winrt.windows.security.cryptography": SimpleNamespace(),
        "winrt.windows.foundation": SimpleNamespace(),
        "winrt.windows.storage.streams": SimpleNamespace(),
    }
    if include_collections:
        modules["winrt.windows.foundation.collections"] = SimpleNamespace()
    return modules


def test_windows_ocr_falls_back_to_winrt_when_winsdk_is_missing(monkeypatch):
    _reset_ocr_windows()
    modules = _fake_winrt_modules()

    def fake_import(name):
        if name.startswith("winsdk."):
            raise ModuleNotFoundError(name)
        return modules[name]

    monkeypatch.setattr(ocr_windows.importlib, "import_module", fake_import)

    assert ocr_windows.is_available() is True
    assert ocr_windows.backend_name() == "winrt"


def test_winrt_missing_collections_is_reported_as_unavailable(monkeypatch):
    _reset_ocr_windows()
    modules = _fake_winrt_modules(include_collections=False)

    def fake_import(name):
        if name.startswith("winsdk."):
            raise ModuleNotFoundError(name)
        if name not in modules:
            raise ModuleNotFoundError(name)
        return modules[name]

    monkeypatch.setattr(ocr_windows.importlib, "import_module", fake_import)

    assert ocr_windows.is_available() is False
    assert "foundation.collections" in ocr_windows.unavailable_reason().lower()


def test_winrt_bitmap_creation_uses_four_arguments():
    calls = []

    class SoftwareBitmap:
        @staticmethod
        def create_copy_from_buffer(*args):
            calls.append(args)
            return object()

    ocr_windows._backend = SimpleNamespace(
        buffer_alpha_arg=False,
        crypto=SimpleNamespace(
            CryptographicBuffer=SimpleNamespace(
                create_from_byte_array=lambda data: ("buffer", data)
            )
        ),
        imaging=SimpleNamespace(
            SoftwareBitmap=SoftwareBitmap,
            BitmapPixelFormat=SimpleNamespace(BGRA8="bgra8"),
        ),
    )

    ocr_windows._software_bitmap_from_image(np.zeros((2, 3, 3), dtype=np.uint8))

    assert len(calls[0]) == 4


def test_winsdk_bitmap_creation_uses_five_arguments():
    calls = []

    class SoftwareBitmap:
        @staticmethod
        def create_copy_from_buffer(*args):
            calls.append(args)
            return object()

    ocr_windows._backend = SimpleNamespace(
        buffer_alpha_arg=True,
        crypto=SimpleNamespace(
            CryptographicBuffer=SimpleNamespace(
                create_from_byte_array=lambda data: ("buffer", data)
            )
        ),
        imaging=SimpleNamespace(
            SoftwareBitmap=SoftwareBitmap,
            BitmapPixelFormat=SimpleNamespace(BGRA8="bgra8"),
            BitmapAlphaMode=SimpleNamespace(PREMULTIPLIED="premultiplied"),
        ),
    )

    ocr_windows._software_bitmap_from_image(np.zeros((2, 3, 3), dtype=np.uint8))

    assert len(calls[0]) == 5


def test_ocr_smoke_test_fails_when_windows_ocr_returns_empty(monkeypatch):
    _reset_ocr_windows()
    monkeypatch.setattr(ocr_windows, "is_available", lambda: True)
    monkeypatch.setattr(ocr_windows, "ocr_image", lambda _image: "")
    monkeypatch.setattr(ocr_windows, "unavailable_reason", lambda: "")

    ok, detail = ocr.smoke_test_text_reader()

    assert ok is False
    assert "recognized no text" in detail
