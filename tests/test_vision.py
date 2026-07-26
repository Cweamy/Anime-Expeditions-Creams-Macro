import numpy as np
import pytest

from core import vision


def test_find_image_any_captures_once_for_multiple_candidates(monkeypatch):
    """Alternative names must be compared against one captured frame."""
    captured = []
    frame = np.zeros((20, 30), dtype=np.uint8)

    monkeypatch.setattr(vision, "load_template_grays", lambda *args: [(frame, None)])

    def capture_game_gray(hwnd, region):
        captured.append((hwnd, region))
        return frame

    monkeypatch.setattr(vision, "capture_game_gray", capture_game_gray)

    def find_in_gray_multiscale(haystack, name, template_dir, threshold):
        assert haystack is frame
        if name == "second":
            return {"x": 2, "y": 3, "w": 4, "h": 5, "cx": 4, "cy": 5, "score": 0.95}
        return None

    monkeypatch.setattr(vision, "find_in_gray_multiscale", find_in_gray_multiscale)

    match, name = vision.find_image_any(123, ("first", "second"), region=(10, 20, 30, 20))

    assert captured == [(123, (10, 20, 30, 20))]
    assert name == "second"
    assert match["x"] == 12
    assert match["y"] == 23
    assert match["cx"] == 14
    assert match["cy"] == 25


def test_find_image_any_raises_when_every_template_is_missing(monkeypatch):
    """A missing candidate set must retain the existing error behavior."""
    monkeypatch.setattr(vision, "load_template_grays", lambda *args: (_ for _ in ()).throw(
        vision.TemplateNotFound("missing")))
    monkeypatch.setattr(vision, "capture_game_gray", lambda *args: pytest.fail("must not capture"))

    with pytest.raises(vision.TemplateNotFound, match="missing"):
        vision.find_image_any(123, ("first", "second"))
