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


def test_find_template_with_and_without_roi():
    """Verify find_template matching works correctly both without roi and with roi."""
    image = np.zeros((100, 100), dtype=np.uint8)
    template = np.zeros((10, 10), dtype=np.uint8)
    template[2:8, 2:8] = 255

    # Place template into target image at x=30, y=40
    image[40:50, 30:40] = template


    # Match without ROI
    match = vision.find_template(image, template, threshold=0.9)
    assert match is not None
    assert match["x"] == 30
    assert match["y"] == 40
    assert match["w"] == 10
    assert match["h"] == 10
    assert match["cx"] == 35
    assert match["cy"] == 45
    assert match["score"] >= 0.99

    # Match with ROI covering the template region
    roi_valid = (20, 30, 40, 40)
    match_roi = vision.find_template(image, template, threshold=0.9, roi=roi_valid)
    assert match_roi is not None
    assert match_roi["x"] == 30
    assert match_roi["y"] == 40
    assert match_roi["w"] == 10
    assert match_roi["h"] == 10
    assert match_roi["cx"] == 35
    assert match_roi["cy"] == 45

    # Match with ROI outside the template region
    roi_outside = (0, 0, 20, 20)
    match_miss = vision.find_template(image, template, threshold=0.9, roi=roi_outside)
    assert match_miss is None

