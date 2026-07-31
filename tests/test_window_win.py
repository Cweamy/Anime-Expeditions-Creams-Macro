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


def test_set_dpi_aware_passes_pointer_sized_context(monkeypatch):
    """DPI_AWARENESS_CONTEXT is a pointer-sized pseudo-handle, so -4
    (PER_MONITOR_AWARE_V2) must not be marshalled as a 32-bit int -- it would
    arrive as 0x00000000FFFFFFFC and the call would fail with
    ERROR_INVALID_PARAMETER on every 64-bit Windows, silently downgrading the
    process to the V1 shcore fallback."""
    seen = []

    def mock_set_context(ctx):
        seen.append(ctx)
        return True  # V2 accepted -- nothing after this should run

    monkeypatch.setattr(window_win.user32, "SetProcessDpiAwarenessContext", mock_set_context)
    monkeypatch.setattr(window_win.user32, "SetProcessDPIAware",
                        lambda: pytest.fail("fell through to SetProcessDPIAware despite V2 succeeding"))

    window_win.set_dpi_aware()

    assert len(seen) == 1, "SetProcessDpiAwarenessContext was not called exactly once"
    ctx = seen[0]
    assert isinstance(ctx, ctypes.c_void_p), f"context passed as {type(ctx).__name__}, not a pointer"
    # c_void_p normalizes -4 to its unsigned pointer-sized representation.
    assert ctx.value == (-4 & (2 ** (8 * ctypes.sizeof(ctypes.c_void_p)) - 1))


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

    # Scenario 6: Roblox Studio -- its process starts with "roblox" but is NOT
    # the game client, so it must be excluded (this was the multi-window
    # attach bug: Studio got docked alongside the real Player window).
    monkeypatch.setattr(window_win, "get_process_name", lambda hwnd: "robloxstudiobeta.exe")
    assert window_win.is_roblox_process_or_title(1, "Baseplate - Roblox Studio") is False

    # Scenario 7: Studio window with no readable process name -- excluded by
    # its "Roblox Studio" title.
    monkeypatch.setattr(window_win, "get_process_name", lambda hwnd: "")
    assert window_win.is_roblox_process_or_title(1, "Place1 - Roblox Studio") is False

    # Scenario 8: A real Player window whose place name happens to contain
    # "studio" still matches -- the PLAYER process is authoritative.
    monkeypatch.setattr(window_win, "get_process_name", lambda hwnd: "robloxplayerbeta.exe")
    assert window_win.is_roblox_process_or_title(1, "Art Studio Tycoon - Roblox") is True

    # Scenario 9: a third-party account switcher whose exe name merely starts
    # with "roblox" (reported live, listed and attachable as if it were the
    # game itself) must NOT match -- the old bare .startswith("roblox") check
    # matched it just like it matched the real client's renamed copies.
    monkeypatch.setattr(window_win, "get_process_name", lambda hwnd: "robloxaccountmanager.exe")
    assert window_win.is_roblox_process_or_title(1, "Roblox Account Manager") is False

    # Scenario 10: a multi-instance tool's renamed COPY of the real client
    # exe (a non-letter suffix tacked onto the real base name) must still
    # match, so running several accounts at once still works.
    monkeypatch.setattr(window_win, "get_process_name", lambda hwnd: "robloxplayerbeta_1.exe")
    assert window_win.is_roblox_process_or_title(1, "Roblox") is True
    monkeypatch.setattr(window_win, "get_process_name", lambda hwnd: "roblox (2).exe")
    assert window_win.is_roblox_process_or_title(1, "Roblox") is True

    # Scenario 11: an unrelated tool riding on the Bloxstrap name gets the
    # same treatment as scenario 9.
    monkeypatch.setattr(window_win, "get_process_name", lambda hwnd: "bloxstrapaccountmanager.exe")
    assert window_win.is_roblox_process_or_title(1, "Roblox") is False



def test_base_window_manager_and_factory():
    """Verifies BaseWindowManager abstraction, WindowsWindowManager inheritance, and get_window_manager factory function."""
    from core.window import BaseWindowManager, get_window_manager, WindowsWindowManager, WindowManager

    wm = get_window_manager("Roblox")
    assert isinstance(wm, BaseWindowManager)
    assert isinstance(wm, WindowsWindowManager)
    assert isinstance(wm, WindowManager)
    assert wm.title_substring == "Roblox"

    assert hasattr(wm, "find_window")
    assert hasattr(wm, "activate")
    assert hasattr(wm, "get_rect")
    assert hasattr(wm, "set_bounds")
