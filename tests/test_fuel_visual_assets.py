from pathlib import Path

import cv2
import numpy as np
import pytest

from core import vision


FUEL_TEMPLATES = (
    "nav_expedition",
    "expedition_hub",
    "fuel_add",
    "fuel_max",
    "fuel_confirm",
    "fuel_input",
)


@pytest.mark.parametrize("name", FUEL_TEMPLATES)
def test_fuel_template_is_valid_and_survives_small_scale_shift(name):
    source = Path(vision.UI_ASSETS_DIR, name, f"{name}.png")
    template = cv2.imread(str(source), cv2.IMREAD_GRAYSCALE)
    assert template is not None
    assert template.shape[0] >= 20
    assert template.shape[1] >= 40
    assert float(template.std()) > 5.0

    scaled = cv2.resize(
        template,
        None,
        fx=0.95,
        fy=0.95,
        interpolation=cv2.INTER_AREA,
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


@pytest.mark.parametrize("name", FUEL_TEMPLATES)
def test_fuel_template_does_not_match_blank_screen(name):
    vision.clear_template_cache()
    try:
        match = vision.find_in_gray_multiscale(
            np.zeros((756, 1152), dtype=np.uint8),
            name,
        )
    finally:
        vision.clear_template_cache()

    assert match is None


def test_fuel_max_detects_min_variant_used_at_ninety_nine_fuel():
    variant = cv2.imread(
        str(Path(vision.UI_ASSETS_DIR, "fuel_max", "fuel_max_alt3.png")),
        cv2.IMREAD_GRAYSCALE,
    )
    assert variant is not None
    frame = np.zeros((756, 1152), dtype=np.uint8)
    height, width = variant.shape
    frame[250:250 + height, 470:470 + width] = variant

    vision.clear_template_cache()
    try:
        match = vision.find_in_gray_multiscale(frame, "fuel_max")
    finally:
        vision.clear_template_cache()

    assert match is not None
    assert match["score"] >= vision.DEFAULT_THRESHOLD
