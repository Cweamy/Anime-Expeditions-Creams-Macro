"""Image file I/O that remains reliable on Windows Unicode paths.

OpenCV's filename-based readers use a narrow path conversion on some Windows
builds, so ``cv2.imread`` can return ``None`` for a perfectly valid image
under a username or folder containing non-ASCII characters. Python's file I/O
handles those paths correctly; decode the bytes with OpenCV instead.
"""
import cv2
import numpy as np


def read_image(path, flags=cv2.IMREAD_UNCHANGED):
    """Read an image from *path*, returning ``None`` on read/decode failure."""
    try:
        with open(path, "rb") as image_file:
            encoded = image_file.read()
    except (OSError, TypeError):
        return None

    if not encoded:
        return None

    try:
        return cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), flags)
    except cv2.error:
        return None
