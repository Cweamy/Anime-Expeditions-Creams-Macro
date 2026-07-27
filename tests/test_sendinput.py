"""screen_to_absolute must land on the pixel the caller asked for.

The inverse used here -- floor(absolute * size / 65536) -- is not a guess. It
was established by driving the production Mouse.move_to through real SendInput
on a DPI-aware 2560x1600 desktop and reading the cursor back with
GetCursorPos, for every column and every row:

    int(x * 65536 / size)           2048/2560 columns, 1536/1600 rows wrong
    round(x * 65535 / (size - 1))      8/2560 columns,    0/1600 rows wrong
    (x * 65536 + 32768) // size        0/2560 columns,    0/1600 rows wrong

The middle form is the natural reading of "0..65535 maps onto 0..size-1", and
a test asserting a ROUND-based inverse passes for it happily -- which is how a
formula that still misses 8 real columns can look correct. Windows floors, so
the forward mapping has to aim at the middle of each pixel's absolute band.
"""
import pytest

from core import _sendinput


def _windows_inverse(absolute: int, size: int) -> int:
    """How Windows turns an absolute coordinate back into a pixel index."""
    return (absolute * size) >> 16


@pytest.mark.parametrize("vx,vy,vw,vh", [
    (0, 0, 2560, 1600),        # the desktop the measurements above came from
    (-320, 40, 2560, 1440),    # negative origin, a monitor left of primary
    (0, 0, 1463, 914),         # odd width AND height, neither a power of two
    (0, 0, 1920, 1080),
    (0, 0, 3, 3),              # degenerate but legal
])
def test_every_pixel_maps_back_to_itself(vx, vy, vw, vh, monkeypatch):
    metrics = {
        _sendinput.SM_XVIRTUALSCREEN: vx,
        _sendinput.SM_YVIRTUALSCREEN: vy,
        _sendinput.SM_CXVIRTUALSCREEN: vw,
        _sendinput.SM_CYVIRTUALSCREEN: vh,
    }
    monkeypatch.setattr(_sendinput.user32, "GetSystemMetrics", metrics.__getitem__)

    for x in range(vx, vx + vw):
        absolute, _ = _sendinput.screen_to_absolute(x, vy)
        assert vx + _windows_inverse(absolute, vw) == x, (
            f"x={x} on a {vw}px-wide desktop came back as "
            f"{vx + _windows_inverse(absolute, vw)}")

    for y in range(vy, vy + vh):
        _, absolute = _sendinput.screen_to_absolute(vx, y)
        assert vy + _windows_inverse(absolute, vh) == y, (
            f"y={y} on a {vh}px-tall desktop came back as "
            f"{vy + _windows_inverse(absolute, vh)}")


def test_result_stays_inside_the_absolute_range(monkeypatch):
    metrics = {
        _sendinput.SM_XVIRTUALSCREEN: 0,
        _sendinput.SM_YVIRTUALSCREEN: 0,
        _sendinput.SM_CXVIRTUALSCREEN: 2560,
        _sendinput.SM_CYVIRTUALSCREEN: 1600,
    }
    monkeypatch.setattr(_sendinput.user32, "GetSystemMetrics", metrics.__getitem__)

    # Well outside the desktop both ways -- the clamp is what stops a bad
    # caller (or a wrong scale) sending an out-of-range absolute coordinate.
    for x, y in ((-99999, -99999), (99999, 99999)):
        ax, ay = _sendinput.screen_to_absolute(x, y)
        assert 0 <= ax <= 65535 and 0 <= ay <= 65535


def test_a_zero_sized_desktop_does_not_divide_by_zero(monkeypatch):
    """GetSystemMetrics can report 0 while a display is being reconfigured."""
    metrics = {
        _sendinput.SM_XVIRTUALSCREEN: 0,
        _sendinput.SM_YVIRTUALSCREEN: 0,
        _sendinput.SM_CXVIRTUALSCREEN: 0,
        _sendinput.SM_CYVIRTUALSCREEN: 0,
    }
    monkeypatch.setattr(_sendinput.user32, "GetSystemMetrics", metrics.__getitem__)
    assert _sendinput.screen_to_absolute(100, 100) == (0, 0)
