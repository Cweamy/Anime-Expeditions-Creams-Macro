import ctypes
import numpy as np
import pytest

from core import window_win


def test_capture_window_rgb_vectorized_parsing(monkeypatch):
    """Verifies that capture_window_rgb parses DIBits correctly and handles black frames."""
    w, h = 10, 10

    def mock_get_window_rect(hwnd, rect_ptr):
        # rect_ptr is byref(rect)
        r = ctypes.cast(rect_ptr, ctypes.POINTER(window_win.RECT)).contents
        r.left = 0
        r.top = 0
        r.right = w
        r.bottom = h
        return True

    monkeypatch.setattr(window_win.user32, "GetWindowRect", mock_get_window_rect)
    monkeypatch.setattr(window_win.user32, "GetWindowDC", lambda hwnd: 123)
    monkeypatch.setattr(window_win.gdi32, "CreateCompatibleDC", lambda hdc: 456)
    monkeypatch.setattr(window_win.gdi32, "CreateCompatibleBitmap", lambda hdc, w, h: 789)
    monkeypatch.setattr(window_win.gdi32, "SelectObject", lambda dc, bmp: 0)
    monkeypatch.setattr(window_win.gdi32, "PatBlt", lambda *args: True)
    monkeypatch.setattr(window_win.user32, "PrintWindow", lambda hwnd, dc, flags: True)
    monkeypatch.setattr(window_win.gdi32, "DeleteObject", lambda obj: None)
    monkeypatch.setattr(window_win.gdi32, "DeleteDC", lambda dc: None)
    monkeypatch.setattr(window_win.user32, "ReleaseDC", lambda hwnd, hdc: None)

    # Test black frame return None
    def mock_get_dibits_black(hdc, bmp, start, lines, buf, bmi, usage):
        return lines

    monkeypatch.setattr(window_win.gdi32, "GetDIBits", mock_get_dibits_black)

    assert window_win.capture_window_rgb(1) is None

    # Test valid frame return RGB bytes
    def mock_get_dibits_valid(hdc, bmp, start, lines, buf, bmi, usage):
        # Fill buffer with BGRA (e.g. B=100, G=150, R=200, A=255)
        bgra_data = np.full((h, w, 4), [100, 150, 200, 255], dtype=np.uint8).tobytes()
        ctypes.memmove(buf, bgra_data, len(bgra_data))
        return lines

    monkeypatch.setattr(window_win.gdi32, "GetDIBits", mock_get_dibits_valid)

    res = window_win.capture_window_rgb(1)
    assert res is not None
    rgb_bytes, rw, rh = res
    assert (rw, rh) == (w, h)
    # Expected RGB values: R=200, G=150, B=100
    arr = np.frombuffer(rgb_bytes, dtype=np.uint8).reshape(h, w, 3)
    assert np.all(arr[:, :, 0] == 200)
    assert np.all(arr[:, :, 1] == 150)
    assert np.all(arr[:, :, 2] == 100)


def test_is_roblox_process_or_title(monkeypatch):
    """Verifies process name and window title detection logic for Roblox."""
    # Scenario 1: Standard Roblox process name
    monkeypatch.setattr(window_win, "get_process_name", lambda hwnd: "robloxplayerbeta.exe")
    assert window_win.is_roblox_process_or_title(1, "Roblox") is True

    # Scenario 2: Bloxstrap launcher process name
    monkeypatch.setattr(window_win, "get_process_name", lambda hwnd: "bloxstrap.exe")
    assert window_win.is_roblox_process_or_title(1, "Roblox") is True

    # Scenario 3: Process name empty (elevated process permission issue) but title is Roblox
    monkeypatch.setattr(window_win, "get_process_name", lambda hwnd: "")
    assert window_win.is_roblox_process_or_title(1, "Roblox") is True
    assert window_win.is_roblox_process_or_title(1, "Roblox Player") is True

    # Scenario 4: Chrome tab titled Roblox (should NOT match)
    monkeypatch.setattr(window_win, "get_process_name", lambda hwnd: "chrome.exe")
    assert window_win.is_roblox_process_or_title(1, "Roblox - Google Chrome") is False

    # Scenario 5: Title has no Roblox keyword
    monkeypatch.setattr(window_win, "get_process_name", lambda hwnd: "robloxplayerbeta.exe")
    assert window_win.is_roblox_process_or_title(1, "Discord") is False

