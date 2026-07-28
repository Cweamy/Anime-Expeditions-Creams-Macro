from pathlib import Path

import cv2
import numpy as np
import pytest

from core import vision


@pytest.mark.parametrize(
    ("name", "filename"),
    (
        ("challenge", "challenge_current.png"),
        ("challenge_loaded", "challenge_loaded_current.png"),
    ),
)
def test_current_challenge_variants_survive_small_ui_scale_shift(tmp_path, name, filename):
    source = Path(vision.UI_ASSETS_DIR, name, filename)
    template = cv2.imread(str(source), cv2.IMREAD_GRAYSCALE)
    assert template is not None

    variant_dir = tmp_path / name
    variant_dir.mkdir()
    cv2.imwrite(str(variant_dir / filename), template)

    scaled = cv2.resize(template, None, fx=0.95, fy=0.95, interpolation=cv2.INTER_AREA)
    frame = np.zeros((756, 1152), dtype=np.uint8)
    height, width = scaled.shape
    frame[250:250 + height, 470:470 + width] = scaled

    vision.clear_template_cache()
    try:
        match = vision.find_in_gray_multiscale(frame, name, template_dir=str(tmp_path))
    finally:
        vision.clear_template_cache()

    assert match is not None
    assert match["score"] >= vision.DEFAULT_THRESHOLD
    assert abs(match["cx"] - (470 + width // 2)) <= 2
    assert abs(match["cy"] - (250 + height // 2)) <= 2


@pytest.mark.parametrize("name", ("challenge", "challenge_loaded"))
def test_current_challenge_variants_do_not_match_a_blank_screen(name):
    vision.clear_template_cache()
    try:
        match = vision.find_in_gray_multiscale(
            np.zeros((756, 1152), dtype=np.uint8),
            name,
        )
    finally:
        vision.clear_template_cache()

    assert match is None
