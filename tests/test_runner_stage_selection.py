import cv2
import numpy as np
import pytest

from core import vision
from core.runner import MacroRunner
from core.runner_constants import (
    STORY_STAGE_MATCH_THRESHOLD,
    STORY_STAGE_SEARCH_REGION,
)


@pytest.mark.parametrize(
    "variant_name, expected_center",
    [
        ("stage_infinite.png", (247, 508)),
        ("stage_infinite_large.png", (215, 524)),
        ("stage_infinite_selected.png", (215, 524)),
    ],
)
def test_infinite_variants_match_both_observed_stage_layouts(variant_name, expected_center):
    """Each real layout variant must remain searchable in the bounded column.

    A synthetic frame keeps the test fixture tiny while still exercising
    the real asset loader, variant folder, multiscale matcher, region offset,
    and final click-center calculation.
    """
    template_path = vision.template_variant_paths("stage_infinite")
    template_path = next(path for path in template_path if path.endswith(variant_name))
    template = cv2.imread(template_path)
    assert template is not None

    frame = np.full((756, 1152, 3), 20, dtype=np.uint8)
    h, w = template.shape[:2]
    cx, cy = expected_center
    frame[cy - h // 2:cy - h // 2 + h, cx - w // 2:cx - w // 2 + w] = template

    x, y, rw, rh = STORY_STAGE_SEARCH_REGION
    gray = cv2.cvtColor(frame[y:y + rh, x:x + rw], cv2.COLOR_BGR2GRAY)
    match = vision.find_in_gray_multiscale(
        gray, "stage_infinite", threshold=STORY_STAGE_MATCH_THRESHOLD)

    assert match is not None
    assert (match["cx"] + x, match["cy"] + y) == expected_center


def test_infinite_search_rejects_a_stage_column_without_the_glyph():
    gray = np.full(
        (STORY_STAGE_SEARCH_REGION[3], STORY_STAGE_SEARCH_REGION[2]),
        49,
        dtype=np.uint8,
    )
    assert vision.find_in_gray_multiscale(
        gray, "stage_infinite", threshold=STORY_STAGE_MATCH_THRESHOLD) is None


@pytest.mark.parametrize(
    "color, expected",
    [
        ((170, 133, 33), True),   # observed selected cyan/blue fill, BGR
        ((49, 49, 49), False),    # observed unselected neutral gray fill
        ((0, 180, 0), False),     # unrelated green UI must not count
    ],
)
def test_selected_stage_color_verification(color, expected):
    image = np.full((49, 49, 3), color, dtype=np.uint8)
    assert MacroRunner._stage_row_looks_selected(image) is expected
