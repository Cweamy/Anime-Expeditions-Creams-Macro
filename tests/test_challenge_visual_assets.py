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
        ("daily_challenge_available", "daily_challenge_available.png"),
        ("daily_challenge_unavailable", "daily_challenge_unavailable.png"),
        ("daily_challenge_stage", "daily_challenge_stage.png"),
        ("daily_challenge_hud", "daily_challenge_hud.png"),
        ("chal_select", "chal_select_current.png"),
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


@pytest.mark.parametrize(
    ("name", "filename"),
    (
        ("daily_challenge_available", "daily_challenge_available.png"),
        ("daily_challenge_unavailable", "daily_challenge_unavailable.png"),
        ("daily_challenge_stage", "daily_challenge_stage.png"),
        ("chal_select", "chal_select_current.png"),
    ),
)
@pytest.mark.parametrize("scale", (0.85, 1.15))
def test_daily_challenge_variants_cover_fifteen_percent_rendering_shift(name, filename, scale):
    source = Path(vision.UI_ASSETS_DIR, name, filename)
    template = cv2.imread(str(source), cv2.IMREAD_GRAYSCALE)
    assert template is not None

    scaled = cv2.resize(
        template,
        None,
        fx=scale,
        fy=scale,
        interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR,
    )
    frame = np.zeros((756, 1152), dtype=np.uint8)
    height, width = scaled.shape
    frame[250:250 + height, 470:470 + width] = scaled

    vision.clear_template_cache()
    try:
        match = vision.find_in_gray_multiscale(frame, name)
    finally:
        vision.clear_template_cache()

    assert match is not None
    assert match["score"] >= vision.DEFAULT_THRESHOLD


@pytest.mark.parametrize(
    "filename",
    ("daily_challenge_hud_small.png", "daily_challenge_hud_large.png"),
)
def test_daily_hud_render_variants_are_detected(filename):
    variant = cv2.imread(
        str(Path(vision.UI_ASSETS_DIR, "daily_challenge_hud", filename)),
        cv2.IMREAD_GRAYSCALE,
    )
    assert variant is not None
    frame = np.zeros((756, 1152), dtype=np.uint8)
    height, width = variant.shape
    frame[250:250 + height, 470:470 + width] = variant

    vision.clear_template_cache()
    try:
        match = vision.find_in_gray_multiscale(frame, "daily_challenge_hud")
    finally:
        vision.clear_template_cache()

    assert match is not None
    assert match["score"] >= vision.DEFAULT_THRESHOLD


@pytest.mark.parametrize(
    "name",
    (
        "challenge",
        "challenge_loaded",
        "daily_challenge_available",
        "daily_challenge_unavailable",
        "daily_challenge_stage",
        "daily_challenge_hud",
        "chal_select",
    ),
)
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
