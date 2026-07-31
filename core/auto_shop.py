"""Pure Auto Shop catalog, daily-state, and stock-verification helpers.

This module intentionally has no runner, bridge, or UI dependencies. The
feature can be integrated after the shared resource-automation pull requests
land without rebasing its safety-critical calculations.
"""

from collections import Counter
from datetime import datetime, timezone
import re
from typing import Iterable, Mapping, Optional, Union

import cv2
import numpy as np


AUTO_SHOP_RESET_SCHEDULE = "utc_midnight_v1"
AUTO_SHOP_MAX_ITEM_ATTEMPTS = 3
AUTO_SHOP_MAX_NAVIGATION_FAILURES = 3
AUTO_SHOP_CANCEL_RIGHT_PADDING = 6

STATUS_PENDING = "pending"
STATUS_RETRY_PENDING = "retry_pending"
STATUS_PENDING_VERIFICATION = "pending_verification"
STATUS_COMPLETED = "completed"
STATUS_OUT_OF_STOCK = "out_of_stock"
STATUS_MAX_INVENTORY = "max_inventory"
STATUS_FAILED_TODAY = "failed_today"

VERIFICATION_PROGRESS = "progress"
VERIFICATION_UNCHANGED = "unchanged"
VERIFICATION_PENDING = "pending_verification"
VERIFICATION_OUT_OF_STOCK = "out_of_stock"

MAX_TOGGLE_CLICK = "click_max"
MAX_TOGGLE_ALREADY_SELECTED = "already_max"
MAX_TOGGLE_UNKNOWN = "unknown"

AUTO_SHOP_UI_TEMPLATES = {
    "navigation": "nav_shop",
    "destination": "area_gold_shop",
    "shop_tab": "shop_gold_tab",
    "buy": "shop_buy",
    "amount_max": "shop_amount_max",
    "amount_min": "shop_amount_min",
    "amount_input": "shop_amount_input",
    "cancel": "shop_cancel",
    "out_of_stock": "shop_out_of_stock",
    "max_inventory": "shop_max_inventory",
}

AUTO_SHOP_ITEMS = (
    {"key": "cursed_boba", "name": "Cursed Boba", "stock": 50, "template": "shop_cursed_boba"},
    {"key": "red_flower", "name": "Red Flower", "stock": 75, "template": "shop_red_flower"},
    {"key": "frown_fruit", "name": "Frown Fruit", "stock": 100, "template": "shop_frown_fruit"},
    {"key": "delicious_pie", "name": "Delicious Pie", "stock": 125, "template": "shop_delicious_pie"},
    {"key": "mana_flask", "name": "Mana Flask", "stock": 150, "template": "shop_mana_flask"},
    {"key": "trait_crystal", "name": "Trait Crystal", "stock": 25, "template": "shop_trait_crystal"},
    {"key": "sprite_grey", "name": "Sprite (Grey)", "stock": 25, "template": "shop_sprite_grey"},
    {
        "key": "equipment_reroll",
        "name": "Equipment Reroll",
        "stock": 10,
        "template": "shop_equipment_reroll",
    },
    {
        "key": "equipment_lock",
        "name": "Equipment Lock",
        "stock": 10,
        "template": "shop_equipment_lock",
    },
    {"key": "stat_reroll", "name": "Stat Reroll", "stock": 10, "template": "shop_stat_reroll"},
    {"key": "stat_lock", "name": "Stat Lock", "stock": 10, "template": "shop_stat_lock"},
)

AUTO_SHOP_ITEMS_BY_KEY = {item["key"]: item for item in AUTO_SHOP_ITEMS}

_LEFT_COUNT_PATTERN = re.compile(r"(?<!\d)(\d{1,3})(?!\d)")
_VALID_STATUSES = {
    STATUS_PENDING,
    STATUS_RETRY_PENDING,
    STATUS_PENDING_VERIFICATION,
    STATUS_COMPLETED,
    STATUS_OUT_OF_STOCK,
    STATUS_MAX_INVENTORY,
    STATUS_FAILED_TODAY,
}


def current_auto_shop_period(now: Optional[float] = None) -> str:
    """Return the UTC game-day identifier used by Challenge and Auto Shop."""
    if now is None:
        now = datetime.now(timezone.utc).timestamp()
    return datetime.fromtimestamp(now, timezone.utc).date().isoformat()


def item_definition(item_key: str) -> Mapping:
    """Return one catalog entry or raise a clear error for an unknown key."""
    try:
        return AUTO_SHOP_ITEMS_BY_KEY[item_key]
    except KeyError as exc:
        raise ValueError(f"Unknown Auto Shop item: {item_key}") from exc


def default_auto_shop_settings() -> dict:
    """Return the disabled, safest-first settings for the Gold Shop."""
    return {
        "enabled": False,
        "shops": {
            "gold_shop": {
                "enabled": False,
                "items": {
                    item["key"]: {"enabled": False, "target": 1}
                    for item in AUTO_SHOP_ITEMS
                },
            },
        },
    }


def normalize_auto_shop_settings(saved: Optional[Mapping]) -> dict:
    """Merge persisted input into the known catalog without trusting its shape."""
    defaults = default_auto_shop_settings()
    if not isinstance(saved, Mapping):
        return defaults

    defaults["enabled"] = bool(saved.get("enabled", False))
    saved_shops = saved.get("shops")
    if not isinstance(saved_shops, Mapping):
        return defaults
    saved_gold_shop = saved_shops.get("gold_shop")
    if not isinstance(saved_gold_shop, Mapping):
        return defaults

    gold_shop = defaults["shops"]["gold_shop"]
    gold_shop["enabled"] = bool(saved_gold_shop.get("enabled", False))
    saved_items = saved_gold_shop.get("items")
    if not isinstance(saved_items, Mapping):
        return defaults

    for item in AUTO_SHOP_ITEMS:
        item_key = item["key"]
        saved_item = saved_items.get(item_key)
        if not isinstance(saved_item, Mapping):
            continue
        target = saved_item.get("target", 1)
        try:
            target = normalize_target(target, int(item["stock"]))
        except ValueError:
            target = 1
        gold_shop["items"][item_key] = {
            "enabled": bool(saved_item.get("enabled", False)),
            "target": target,
        }
    return defaults


def normalize_target(target: Union[str, int], daily_maximum: int) -> Union[str, int]:
    """Validate a numeric daily target or the special ``max`` target."""
    if isinstance(target, str) and target.strip().lower() == "max":
        return "max"
    if isinstance(target, bool):
        raise ValueError("Auto Shop target must be a number or 'max'")
    try:
        value = int(target)
    except (TypeError, ValueError) as exc:
        raise ValueError("Auto Shop target must be a number or 'max'") from exc
    if value < 1 or value > daily_maximum:
        raise ValueError(f"Auto Shop target must be between 1 and {daily_maximum}")
    return value


def calculate_purchase_plan(
    item_key: str,
    target: Union[str, int],
    current_left: int,
) -> dict:
    """Calculate daily progress and the exact safe quantity still required."""
    item = item_definition(item_key)
    daily_maximum = int(item["stock"])
    if isinstance(current_left, bool) or not isinstance(current_left, int):
        raise ValueError("Current stock must be an integer")
    if current_left < 0 or current_left > daily_maximum:
        raise ValueError(f"Current stock must be between 0 and {daily_maximum}")

    normalized_target = normalize_target(target, daily_maximum)
    already_bought = daily_maximum - current_left
    desired_total = daily_maximum if normalized_target == "max" else normalized_target
    pending_amount = max(0, min(current_left, desired_total - already_bought))

    if current_left == 0:
        status = STATUS_OUT_OF_STOCK
    elif pending_amount == 0:
        status = STATUS_COMPLETED
    else:
        status = STATUS_PENDING

    return {
        "daily_maximum": daily_maximum,
        "current_left": current_left,
        "already_bought": already_bought,
        "desired_total": desired_total,
        "pending_amount": pending_amount,
        "status": status,
    }


def parse_left_count(text: str, daily_maximum: int) -> Optional[int]:
    """Extract one plausible ``Left!`` count from a small OCR result."""
    values = [int(match) for match in _LEFT_COUNT_PATTERN.findall(str(text or ""))]
    values = [value for value in values if 0 <= value <= daily_maximum]
    if len(values) != 1:
        return None
    return values[0]


def consensus_left_count(readings: Iterable[Optional[int]], daily_maximum: int) -> Optional[int]:
    """Require at least two matching, in-range OCR reads."""
    valid = [
        value
        for value in readings
        if isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= daily_maximum
    ]
    if not valid:
        return None
    value, count = Counter(valid).most_common(1)[0]
    return value if count >= 2 else None


def build_stock_signature(crop_bgr: np.ndarray, width: int = 96, height: int = 24) -> str:
    """Build a compact bright-text signature for cross-run visual comparison."""
    if crop_bgr is None or crop_bgr.size == 0:
        raise ValueError("Stock crop cannot be empty")
    if crop_bgr.ndim == 2:
        gray = crop_bgr
    elif crop_bgr.ndim == 3 and crop_bgr.shape[2] >= 3:
        gray = cv2.cvtColor(crop_bgr[:, :, :3], cv2.COLOR_BGR2GRAY)
    else:
        raise ValueError("Stock crop must be a grayscale or BGR image")

    _, bright = cv2.threshold(gray, 185, 255, cv2.THRESH_BINARY)
    normalized = cv2.resize(bright, (width, height), interpolation=cv2.INTER_NEAREST)
    packed = np.packbits(normalized.reshape(-1) > 0)
    return packed.tobytes().hex()


def stock_signature_distance(first: str, second: str) -> float:
    """Return the normalized Hamming distance between two visual signatures."""
    try:
        first_bytes = bytes.fromhex(first)
        second_bytes = bytes.fromhex(second)
    except ValueError as exc:
        raise ValueError("Stock signatures must be hexadecimal strings") from exc
    if not first_bytes or len(first_bytes) != len(second_bytes):
        raise ValueError("Stock signatures must have the same non-zero length")
    changed_bits = sum((left ^ right).bit_count() for left, right in zip(first_bytes, second_bytes))
    return changed_bits / (len(first_bytes) * 8)


def classify_stock_verification(
    before_left: Optional[int],
    after_left: Optional[int],
    visual_changed: Optional[bool],
    *,
    out_of_stock: bool = False,
) -> str:
    """Classify a post-purchase observation without risking a duplicate click."""
    if out_of_stock:
        return VERIFICATION_OUT_OF_STOCK
    if before_left is None or after_left is None or visual_changed is None:
        return VERIFICATION_PENDING
    if after_left < before_left and visual_changed:
        return VERIFICATION_PROGRESS
    if after_left == before_left and not visual_changed:
        return VERIFICATION_UNCHANGED
    return VERIFICATION_PENDING


def max_toggle_action(max_visible: bool, min_visible: bool) -> str:
    """Decide whether the Max toggle needs a click.

    The game shows ``Min`` when the modal is already holding the maximum
    quantity. Clicking that state would undo the requested Max selection.
    """
    if min_visible:
        return MAX_TOGGLE_ALREADY_SELECTED
    if max_visible:
        return MAX_TOGGLE_CLICK
    return MAX_TOGGLE_UNKNOWN


def fresh_item_state(period: str) -> dict:
    """Create the persisted daily state for one item."""
    return {
        "period": str(period),
        "status": STATUS_PENDING,
        "attempts": 0,
        "last_known_left": None,
        "stock_signature": "",
        "verification": None,
    }


def normalize_item_state(saved: Optional[Mapping], period: str) -> dict:
    """Reset stale daily state and sanitize data loaded from settings."""
    if not isinstance(saved, Mapping) or str(saved.get("period") or "") != str(period):
        return fresh_item_state(period)

    state = fresh_item_state(period)
    status = str(saved.get("status") or STATUS_PENDING)
    state["status"] = status if status in _VALID_STATUSES else STATUS_PENDING
    try:
        attempts = int(saved.get("attempts", 0))
    except (TypeError, ValueError):
        attempts = 0
    state["attempts"] = min(AUTO_SHOP_MAX_ITEM_ATTEMPTS, max(0, attempts))
    state["last_known_left"] = saved.get("last_known_left")
    state["stock_signature"] = str(saved.get("stock_signature") or "")
    verification = saved.get("verification")
    state["verification"] = dict(verification) if isinstance(verification, Mapping) else None
    if state["attempts"] >= AUTO_SHOP_MAX_ITEM_ATTEMPTS:
        state["status"] = STATUS_FAILED_TODAY
    return state


def record_item_failure(saved: Optional[Mapping], period: str) -> dict:
    """Consume one daily item attempt and stop it after the third failure."""
    state = normalize_item_state(saved, period)
    state["attempts"] = min(AUTO_SHOP_MAX_ITEM_ATTEMPTS, state["attempts"] + 1)
    state["status"] = (
        STATUS_FAILED_TODAY
        if state["attempts"] >= AUTO_SHOP_MAX_ITEM_ATTEMPTS
        else STATUS_PENDING
    )
    return state


def fresh_shop_state(period: str) -> dict:
    """Create the persisted daily navigation state for one shop."""
    return {
        "period": str(period),
        "navigation_failures": 0,
        "status": STATUS_PENDING,
    }


def normalize_shop_state(saved: Optional[Mapping], period: str) -> dict:
    """Reset stale shop-level failures and sanitize persisted values."""
    if not isinstance(saved, Mapping) or str(saved.get("period") or "") != str(period):
        return fresh_shop_state(period)
    state = fresh_shop_state(period)
    try:
        failures = int(saved.get("navigation_failures", 0))
    except (TypeError, ValueError):
        failures = 0
    state["navigation_failures"] = min(
        AUTO_SHOP_MAX_NAVIGATION_FAILURES,
        max(0, failures),
    )
    if state["navigation_failures"] >= AUTO_SHOP_MAX_NAVIGATION_FAILURES:
        state["status"] = STATUS_FAILED_TODAY
    return state


def record_navigation_failure(saved: Optional[Mapping], period: str) -> dict:
    """Consume one shop-level navigation attempt for the current UTC period."""
    state = normalize_shop_state(saved, period)
    state["navigation_failures"] = min(
        AUTO_SHOP_MAX_NAVIGATION_FAILURES,
        state["navigation_failures"] + 1,
    )
    state["status"] = (
        STATUS_FAILED_TODAY
        if state["navigation_failures"] >= AUTO_SHOP_MAX_NAVIGATION_FAILURES
        else STATUS_PENDING
    )
    return state


def cancel_right_edge_point(
    left: int,
    top: int,
    width: int,
    height: int,
    padding: int = AUTO_SHOP_CANCEL_RIGHT_PADDING,
) -> tuple:
    """Return the safest point near the far-right edge of the Cancel button."""
    if width <= padding or height <= 0:
        raise ValueError("Cancel bounds are too small for a safe click")
    return left + width - padding, top + height // 2
