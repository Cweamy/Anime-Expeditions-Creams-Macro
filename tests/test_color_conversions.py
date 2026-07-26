import cv2
import numpy as np


def test_bgra_to_rgb_pixel_equivalency():
    """Verifies that cv2.cvtColor BGRA2RGB matches legacy bytearray slicing pixel for pixel."""
    h, w = 50, 50
    # Generate deterministic BGRA test data
    np.random.seed(42)
    bgra_raw = np.random.randint(0, 256, (h, w, 4), dtype=np.uint8).tobytes()

    # Legacy bytearray slicing method
    raw = bytearray(bgra_raw)
    rgb_legacy = bytearray(w * h * 3)
    rgb_legacy[0::3] = raw[2::4]  # R
    rgb_legacy[1::3] = raw[1::4]  # G
    rgb_legacy[2::3] = raw[0::4]  # B
    legacy_bytes = bytes(rgb_legacy)

    # Vectorized OpenCV method
    arr = np.frombuffer(bgra_raw, dtype=np.uint8).reshape(h, w, 4)
    new_bytes = cv2.cvtColor(arr, cv2.COLOR_BGRA2RGB).tobytes()

    assert legacy_bytes == new_bytes, "BGRA to RGB conversion differs from legacy implementation!"


def test_bgra_to_bgr_pixel_equivalency():
    """Verifies that cv2.cvtColor BGRA2BGR matches direct array channel slicing."""
    h, w = 50, 50
    np.random.seed(123)
    bgra_arr = np.random.randint(0, 256, (h, w, 4), dtype=np.uint8)

    # Legacy method: numpy array slice
    bgr_legacy = bgra_arr[:, :, :3].copy()

    # OpenCV method: cv2.cvtColor
    bgr_new = cv2.cvtColor(bgra_arr, cv2.COLOR_BGRA2BGR)

    assert np.array_equal(bgr_legacy, bgr_new), "BGRA to BGR conversion differs from legacy array slicing!"


def test_bgra_to_gray_pixel_equivalency():
    """Verifies that direct BGRA2GRAY matches intermediate BGR2GRAY conversion."""
    h, w = 50, 50
    np.random.seed(999)
    bgra_arr = np.random.randint(0, 256, (h, w, 4), dtype=np.uint8)

    # Legacy 2-step method: slice BGR then convert BGR2GRAY
    bgr = bgra_arr[:, :, :3]
    gray_legacy = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    # Direct 1-step method: BGRA2GRAY
    gray_new = cv2.cvtColor(bgra_arr, cv2.COLOR_BGRA2GRAY)

    assert np.array_equal(gray_legacy, gray_new), "Direct BGRA to GRAY conversion differs from BGR to GRAY!"
