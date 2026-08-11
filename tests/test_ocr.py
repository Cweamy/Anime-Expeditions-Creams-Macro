import importlib.util
import runpy
import subprocess
import sys

import numpy as np

from core import ocr, ocr_windows


class DummyTesseract:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def image_to_string(self, img, config=""):
        self.calls.append((img, config))
        return self.text


def test_get_rapidocr_missing_returns_none_and_is_cached(monkeypatch):
    ocr.reset_rapidocr_cache()
    calls = []
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "rapidocr_onnxruntime":
            calls.append(name)
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    assert ocr.get_rapidocr() is None
    assert ocr.get_rapidocr() is None
    assert calls == ["rapidocr_onnxruntime"]
    ocr.reset_rapidocr_cache()


def test_ocr_mask_rapidocr_miss_falls_back_to_windows(monkeypatch):
    img = np.zeros((4, 4), dtype=np.uint8)
    monkeypatch.setattr(ocr, "get_rapidocr", lambda: (lambda _img: ([], 0)))
    monkeypatch.setattr(ocr_windows, "is_available", lambda: True)
    monkeypatch.setattr(ocr_windows, "ocr_image", lambda _img: "Win 42")

    assert ocr.ocr_mask(None, img, "--psm 7") == "Win 42"


def test_ocr_mask_rapidocr_error_and_windows_miss_falls_back_to_tesseract(monkeypatch):
    img = np.zeros((4, 4), dtype=np.uint8)

    def boom(_img):
        raise RuntimeError("model failed")

    monkeypatch.setattr(ocr, "get_rapidocr", lambda: boom)
    monkeypatch.setattr(ocr_windows, "is_available", lambda: True)
    monkeypatch.setattr(ocr_windows, "ocr_image", lambda _img: "")
    tess = DummyTesseract("Tess 99")

    assert ocr.ocr_mask(tess, img, "--psm 7") == "Tess 99"
    assert tess.calls


def test_ocr_mask_preserves_config_whitelist_for_windows(monkeypatch):
    img = np.zeros((4, 4), dtype=np.uint8)
    monkeypatch.setattr(ocr, "get_rapidocr", lambda: None)
    monkeypatch.setattr(ocr_windows, "is_available", lambda: True)
    monkeypatch.setattr(ocr_windows, "ocr_image", lambda _img: "Wave 12x!")

    assert ocr.ocr_mask(None, img, "--psm 7 -c tessedit_char_whitelist=0123456789x") == "12x"


def test_ocr_windows_uses_windows_primary_then_rapidocr_fallback(monkeypatch):
    img = np.zeros((4, 4), dtype=np.uint8)
    monkeypatch.setattr(ocr_windows, "is_available", lambda: True)

    class Result:
        text = ""

    monkeypatch.setattr(ocr_windows, "_recognize", lambda _img: Result())
    monkeypatch.setattr(ocr, "_rapidocr_text", lambda _img: "rapid text")

    assert ocr_windows.ocr_image(img) == "rapid text"


def test_build_pyinstaller_keeps_winrt_winsdk_and_optional_rapidocr(monkeypatch):
    seen = []

    def fake_find_spec(name):
        return object() if name in {"winsdk", "winrt", "rapidocr_onnxruntime"} else None

    def fake_run(cmd, cwd=None):
        seen.extend(cmd)

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("platform.machine", lambda: "AMD64")

    ns = runpy.run_path("build_pyinstaller.py")
    cmd = ns["cmd"]

    assert "--collect-submodules=winsdk" in cmd
    assert "--collect-submodules=winrt" in cmd
    assert "--collect-data=rapidocr_onnxruntime" in cmd
    assert seen == cmd
