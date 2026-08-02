from pathlib import Path

import cv2
import numpy as np
import pytest

from core import vision


_FALLBACK_VARIANTS = (
    "click_anywhere_to_close_sword.png",
    "click_anywhere_to_close_level_1.png",
    "click_anywhere_to_close_8th_sword.png",
)


def test_raid_popup_fallback_variants_are_registered_with_one_template():
    paths = vision.template_variant_paths("click_anywhere_to_close")
    names = {Path(path).name for path in paths}

    assert set(_FALLBACK_VARIANTS).issubset(names)


@pytest.mark.parametrize("filename", _FALLBACK_VARIANTS)
def test_raid_popup_fallback_variant_matches_when_present(filename):
    source = Path(vision.UI_ASSETS_DIR, "click_anywhere_to_close", filename)
    template = cv2.imread(str(source), cv2.IMREAD_GRAYSCALE)
    assert template is not None

    rng = np.random.default_rng(2026)
    frame = rng.integers(0, 256, size=(756, 1152), dtype=np.uint8)
    x, y = 470, 250
    height, width = template.shape
    frame[y:y + height, x:x + width] = template

    vision.clear_template_cache()
    try:
        match = vision.find_in_gray_multiscale(frame, "click_anywhere_to_close")
    finally:
        vision.clear_template_cache()

    assert match is not None
    assert match["score"] >= vision.DEFAULT_THRESHOLD
    assert abs(match["cx"] - (x + width // 2)) <= 2
    assert abs(match["cy"] - (y + height // 2)) <= 2
