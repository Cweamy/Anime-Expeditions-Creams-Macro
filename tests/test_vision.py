import numpy as np
import pytest

from core import vision
from core import window as wm


def test_screen_to_ref_is_the_inverse_of_ref_to_screen(monkeypatch):
    """core.input_record's Record block relies on screen_to_ref undoing
    ref_to_screen exactly, so a captured screen point round-trips back to
    the same reference point it was converted from."""
    monkeypatch.setattr(wm, "get_window_rect_screen", lambda hwnd: (100, 50, 100 + 576, 50 + 378))

    ref_x, ref_y = 300.0, 200.0
    screen_x, screen_y = vision.ref_to_screen(1, ref_x, ref_y)
    back_x, back_y = vision.screen_to_ref(1, screen_x, screen_y)

    assert round(back_x) == ref_x
    assert round(back_y) == ref_y


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


def test_template_cache_lru_eviction():
    """Verify that _template_cache respects max capacity and evicts least recently used items."""
    vision.clear_template_cache()
    try:
        max_size = vision.MAX_TEMPLATE_CACHE_SIZE
        for i in range(max_size):
            vision._template_cache[f"key_{i}"] = i

        assert len(vision._template_cache) == max_size
        assert "key_0" in vision._template_cache

        # Access key_0 so it becomes recently used
        _ = vision._template_cache["key_0"]

        # Insert a new item beyond capacity
        vision._template_cache["key_overflow"] = 999

        assert len(vision._template_cache) == max_size
        # key_1 was least recently used, so it must be evicted
        assert "key_1" not in vision._template_cache
        # key_0 was recently accessed, so it must be kept
        assert "key_0" in vision._template_cache
        assert "key_overflow" in vision._template_cache
    finally:
        vision.clear_template_cache()

