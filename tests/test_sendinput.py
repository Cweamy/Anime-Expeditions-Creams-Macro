from core import _sendinput


def test_screen_to_absolute_maps_every_pixel_back_to_itself(monkeypatch):
    metrics = {
        _sendinput.SM_XVIRTUALSCREEN: -320,
        _sendinput.SM_YVIRTUALSCREEN: 40,
        _sendinput.SM_CXVIRTUALSCREEN: 2560,
        _sendinput.SM_CYVIRTUALSCREEN: 1440,
    }
    monkeypatch.setattr(_sendinput.user32, "GetSystemMetrics", metrics.__getitem__)

    vx, vy, vw, vh = _sendinput.virtual_screen_rect()
    for x in range(vx, vx + vw):
        absolute, _ = _sendinput.screen_to_absolute(x, vy)
        round_trip = vx + round(absolute * (vw - 1) / 65535)
        assert round_trip == x
    for y in range(vy, vy + vh):
        _, absolute = _sendinput.screen_to_absolute(vx, y)
        round_trip = vy + round(absolute * (vh - 1) / 65535)
        assert round_trip == y
