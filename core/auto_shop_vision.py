"""Offline-safe visual helpers for the Gold Shop item list.

All coordinates produced here stay in the same reference space as the item
match returned by :mod:`core.vision`. Re-finding the item after every scroll
therefore relocates the stock label without relying on page coordinates.
"""

from collections import Counter
from typing import Iterable, Mapping, Optional

import numpy as np

from . import auto_shop, ocr


STOCK_REGION_FROM_CENTER = (-42, -48, 64, 24)
STOCK_REGION_BASE_HEIGHT = STOCK_REGION_FROM_CENTER[3]
STOCK_REGION_TIGHT_HEIGHT = 18
STOCK_STATUS_FROM_CENTER = (-34, -55, 90, 46)
INITIAL_BUY_FROM_CENTER = (-68, 110, 140, 42)
CARD_TERMINAL_FROM_CENTER = (-68, -55, 140, 207)

CANCEL_ANCHOR_BASE_WIDTH = 181
AMOUNT_INPUT_FROM_CANCEL = (-185, -46, 48, 29)
AMOUNT_TOGGLE_FROM_CANCEL = (131, -46, 48, 29)
FINAL_BUY_FROM_CANCEL = (-190, -3, 185, 36)

MODAL_OPENED = "opened"
MODAL_NOT_OPENED = "not_opened"
MODAL_CLOSED_AFTER_BUY = "closed_after_buy"
MODAL_REMAINS_AFTER_BUY = "remains_after_buy"
MODAL_UNKNOWN = "unknown"

_OCR_CONFIG = "--psm 7 -c tessedit_char_whitelist=0123456789"
_OCR_SHARPEN_AMOUNTS = (0.0, 1.5)
BUY_BUTTON_GREEN_MIN_FRACTION = 0.06


def stock_region_from_item_match(match: Mapping) -> tuple:
    """Derive the ``Left!`` crop from the freshly located item icon."""
    return _region_from_item_match(match, STOCK_REGION_FROM_CENTER)


def _region_from_item_match(match: Mapping, relative_region: tuple) -> tuple:
    try:
        x = int(match["x"])
        y = int(match["y"])
        width = int(match["w"])
        height = int(match["h"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "Item match must contain integer x, y, w, and h values"
        ) from exc
    if width <= 0 or height <= 0:
        raise ValueError("Item match dimensions must be positive")

    center_x = int(match.get("cx", x + width // 2))
    center_y = int(match.get("cy", y + height // 2))
    offset_x, offset_y, region_width, region_height = relative_region
    return (
        center_x + int(offset_x),
        center_y + int(offset_y),
        max(1, int(region_width)),
        max(1, int(region_height)),
    )


def stock_status_region_from_item_match(match: Mapping) -> tuple:
    """Return the larger item-relative region that can contain Out of Stock."""
    return _region_from_item_match(match, STOCK_STATUS_FROM_CENTER)


def initial_buy_region_from_item_match(match: Mapping) -> tuple:
    """Return the card's first Buy button bounds without matching its price."""
    return _region_from_item_match(match, INITIAL_BUY_FROM_CENTER)


def card_terminal_region_from_item_match(match: Mapping) -> tuple:
    """Return the card area containing terminal stock and inventory labels."""
    return _region_from_item_match(match, CARD_TERMINAL_FROM_CENTER)


def crop_region(frame_bgr: np.ndarray, region: tuple) -> np.ndarray:
    """Crop a validated reference-space region from a captured frame."""
    if frame_bgr is None or frame_bgr.size == 0:
        raise ValueError("Frame cannot be empty")
    x, y, width, height = (int(value) for value in region)
    frame_height, frame_width = frame_bgr.shape[:2]
    if width <= 0 or height <= 0:
        raise ValueError("Crop dimensions must be positive")
    if x < 0 or y < 0 or x + width > frame_width or y + height > frame_height:
        raise ValueError("Stock region falls outside the captured frame")
    return frame_bgr[y:y + height, x:x + width].copy()


def buy_button_is_enabled(crop_bgr: np.ndarray) -> bool:
    """Classify a known Buy rectangle by its total saturated-green coverage."""
    if crop_bgr is None or crop_bgr.size == 0:
        return False
    if crop_bgr.ndim != 3 or crop_bgr.shape[2] < 3:
        raise ValueError("Buy button crop must be a BGR image")
    pixels = crop_bgr[:, :, :3].astype(np.int16)
    blue, green, red = (pixels[:, :, index] for index in range(3))
    green_mask = (
        (green > 135)
        & (green - red > 45)
        & (green - blue > 70)
    )
    return float(green_mask.mean()) >= BUY_BUTTON_GREEN_MIN_FRACTION


def _region_from_cancel(cancel_match: Mapping, relative_region: tuple) -> tuple:
    try:
        x = int(cancel_match["x"])
        y = int(cancel_match["y"])
        width = int(cancel_match["w"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Cancel match must contain integer x, y, and w values") from exc
    if width <= 0:
        raise ValueError("Cancel match width must be positive")

    scale = width / CANCEL_ANCHOR_BASE_WIDTH
    offset_x, offset_y, region_width, region_height = relative_region
    return (
        x + round(offset_x * scale),
        y + round(offset_y * scale),
        max(1, round(region_width * scale)),
        max(1, round(region_height * scale)),
    )


def amount_input_region_from_cancel(cancel_match: Mapping) -> tuple:
    """Return the numeric input bounds anchored to the visible Cancel button."""
    return _region_from_cancel(cancel_match, AMOUNT_INPUT_FROM_CANCEL)


def amount_toggle_region_from_cancel(cancel_match: Mapping) -> tuple:
    """Return the Max/Min toggle bounds anchored to the Cancel button."""
    return _region_from_cancel(cancel_match, AMOUNT_TOGGLE_FROM_CANCEL)


def final_buy_region_from_cancel(cancel_match: Mapping) -> tuple:
    """Return the confirmation Buy bounds without matching its variable price."""
    return _region_from_cancel(cancel_match, FINAL_BUY_FROM_CANCEL)


def cancel_click_point(cancel_match: Mapping) -> tuple:
    """Return the required far-right, inside-button Cancel click point."""
    try:
        return auto_shop.cancel_right_edge_point(
            int(cancel_match["x"]),
            int(cancel_match["y"]),
            int(cancel_match["w"]),
            int(cancel_match["h"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Cancel match must contain integer x, y, w, and h values") from exc


def classify_modal_transition(was_open: bool, is_open: Optional[bool]) -> str:
    """Classify the modal around the initial or final Buy click."""
    if is_open is None:
        return MODAL_UNKNOWN
    if not was_open:
        return MODAL_OPENED if is_open else MODAL_NOT_OPENED
    return MODAL_REMAINS_AFTER_BUY if is_open else MODAL_CLOSED_AFTER_BUY


def _ocr_values(crop_bgr: np.ndarray, daily_maximum: int) -> list:
    try:
        engine = ocr.get_pytesseract()
    except ocr.TesseractNotAvailable:
        engine = None
    values = []
    for sharpen_amount in _OCR_SHARPEN_AMOUNTS:
        for mask in ocr.candidate_masks(crop_bgr, sharpen_amount=sharpen_amount):
            text = ocr.ocr_mask(engine, mask, _OCR_CONFIG)
            value = auto_shop.parse_left_count(text, daily_maximum)
            if value is not None:
                values.append(value)
    return values


def read_left_count(crop_bgr: np.ndarray, daily_maximum: int) -> Optional[int]:
    """Read one bounded stock count through a strict preprocessing vote."""
    def strict_vote(values):
        if not values:
            return None
        ranked = Counter(values).most_common()
        best_value, best_votes = ranked[0]
        second_votes = ranked[1][1] if len(ranked) > 1 else 0
        if best_votes < 2 or best_votes == second_votes:
            return None
        return best_value

    height = crop_bgr.shape[0]
    tight_height = round(
        height * STOCK_REGION_TIGHT_HEIGHT / STOCK_REGION_BASE_HEIGHT
    )
    if 0 < tight_height < height:
        tight_value = strict_vote(
            _ocr_values(crop_bgr[:tight_height], daily_maximum)
        )
        if tight_value is not None:
            return tight_value

    values = _ocr_values(crop_bgr, daily_maximum)
    if not values:
        return None
    return strict_vote(values)


def read_left_consensus(crops_bgr: Iterable[np.ndarray], daily_maximum: int) -> Optional[int]:
    """Read three short frames and require two identical stock values."""
    readings = [read_left_count(crop, daily_maximum) for crop in crops_bgr]
    return auto_shop.consensus_left_count(readings, daily_maximum)


def stock_visual_changed(first_signature: str, second_signature: str, threshold: float = 0.005) -> bool:
    """Return whether bright stock-label pixels changed beyond capture noise."""
    if threshold < 0 or threshold > 1:
        raise ValueError("Visual-change threshold must be between 0 and 1")
    return auto_shop.stock_signature_distance(first_signature, second_signature) > threshold
