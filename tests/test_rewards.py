"""Tests for core/rewards.py cell detection, scrollbar probe, and helpers."""
import numpy as np
import pytest

from core import rewards


def test_detect_icon_cells_empty_image():
    # Solid background image (no icons) should return no cells
    blank = np.zeros((100, 500, 3), dtype=np.uint8)
    cells = rewards.detect_icon_cells(blank)
    assert cells == []


def test_detect_icon_cells_synthetic():
    # Create synthetic image with 2 distinct icons (with vertical pixel variance) separated by dark gap
    img = np.zeros((100, 300, 3), dtype=np.uint8)
    # Icon 1 from x=20 to 80 with vertical variation
    for y in range(20, 80):
        img[y, 20:80] = y * 3
    # Icon 2 from x=120 to 180 with vertical variation
    for y in range(20, 80):
        img[y, 120:180] = y * 3

    cells = rewards.detect_icon_cells(img, min_width_frac=0.05)
    assert len(cells) == 2
    assert abs(cells[0][0] - 20) <= 2
    assert abs(cells[0][1] - 80) <= 2
    assert abs(cells[1][0] - 120) <= 2
    assert abs(cells[1][1] - 180) <= 2


def test_scrollbar_constants():
    assert isinstance(rewards.SCROLLBAR_PROBE, tuple)
    assert len(rewards.SCROLLBAR_PROBE) == 4
    assert rewards.SCROLLBAR_COLOR == 0x373737
    assert rewards.SCROLLBAR_TOLERANCE == 1
