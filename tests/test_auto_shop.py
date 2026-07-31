from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import pytest

from core import auto_shop
from core import auto_shop_vision
from core import vision


UTC = timezone.utc
AUTO_SHOP_REFERENCES = Path(__file__).resolve().parents[1] / "Assets" / "reference" / "auto_shop"
AUTO_SHOP_REFERENCE_ITEMS = {
    "gold_shop_top.png": (
        "cursed_boba",
        "red_flower",
        "frown_fruit",
        "delicious_pie",
    ),
    "gold_shop_middle.png": (
        "mana_flask",
        "trait_crystal",
        "sprite_grey",
        "equipment_reroll",
    ),
    "gold_shop_bottom.png": (
        "equipment_lock",
        "stat_reroll",
        "stat_lock",
    ),
}
KNOWN_GOLD_SHOP_BUY_VARIANTS = {
    "shop_buy_150.png",
    "shop_buy_200.png",
    "shop_buy_250.png",
    "shop_buy_1000.png",
    "shop_buy_2500.png",
    "shop_buy_5000.png",
}


def _epoch(year, month, day, hour=0, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC).timestamp()


def test_auto_shop_catalog_has_every_gold_shop_item_and_asset():
    expected = [
        ("cursed_boba", "Cursed Boba", 50),
        ("red_flower", "Red Flower", 75),
        ("frown_fruit", "Frown Fruit", 100),
        ("delicious_pie", "Delicious Pie", 125),
        ("mana_flask", "Mana Flask", 150),
        ("trait_crystal", "Trait Crystal", 25),
        ("sprite_grey", "Sprite (Grey)", 25),
        ("equipment_reroll", "Equipment Reroll", 10),
        ("equipment_lock", "Equipment Lock", 10),
        ("stat_reroll", "Stat Reroll", 10),
        ("stat_lock", "Stat Lock", 10),
    ]

    assert [(item["key"], item["name"], item["stock"]) for item in auto_shop.AUTO_SHOP_ITEMS] == expected
    for item in auto_shop.AUTO_SHOP_ITEMS:
        assert vision.template_variant_paths(item["template"])
    for template in auto_shop.AUTO_SHOP_UI_TEMPLATES.values():
        assert vision.template_variant_paths(template)


def test_every_item_icon_matches_its_real_scrolled_shop_reference():
    for filename, item_keys in AUTO_SHOP_REFERENCE_ITEMS.items():
        frame = cv2.imread(
            str(AUTO_SHOP_REFERENCES / filename),
            cv2.IMREAD_GRAYSCALE,
        )
        assert frame is not None
        for item_key in item_keys:
            template = auto_shop.item_definition(item_key)["template"]
            assert vision.find_in_gray_multiscale(frame, template), (
                f"{item_key} did not match {filename}"
            )


def test_buy_color_classifier_accepts_text_interrupted_green_buttons():
    """Price glyphs must not make an enabled green button look disabled."""
    cases = (
        ("gold_shop_top.png", "cursed_boba"),
        ("gold_shop_top.png", "red_flower"),
        ("gold_shop_middle.png", "mana_flask"),
        ("gold_shop_middle.png", "trait_crystal"),
        ("gold_shop_bottom.png", "equipment_lock"),
        ("gold_shop_bottom.png", "stat_reroll"),
    )
    for filename, item_key in cases:
        frame = cv2.imread(str(AUTO_SHOP_REFERENCES / filename))
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        match = vision.find_in_gray_multiscale(
            gray,
            auto_shop.item_definition(item_key)["template"],
        )
        region = auto_shop_vision.initial_buy_region_from_item_match(match)
        crop = auto_shop_vision.crop_region(frame, region)

        assert auto_shop_vision.buy_button_is_enabled(crop), (
            f"{item_key} active Buy was rejected"
        )


def test_buy_color_classifier_rejects_out_of_stock_gray_buttons():
    frame = cv2.imread(
        str(AUTO_SHOP_REFERENCES / "gold_shop_out_of_stock_full.png")
    )
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    for item_key in ("cursed_boba", "red_flower"):
        match = vision.find_in_gray_multiscale(
            gray,
            auto_shop.item_definition(item_key)["template"],
        )
        region = auto_shop_vision.initial_buy_region_from_item_match(match)
        crop = auto_shop_vision.crop_region(frame, region)

        assert not auto_shop_vision.buy_button_is_enabled(crop)


def test_terminal_region_contains_each_real_out_of_stock_label():
    frame = cv2.imread(
        str(AUTO_SHOP_REFERENCES / "gold_shop_out_of_stock_full.png"),
        cv2.IMREAD_GRAYSCALE,
    )
    for item_key in ("cursed_boba", "red_flower"):
        match = vision.find_in_gray_multiscale(
            frame,
            auto_shop.item_definition(item_key)["template"],
        )
        x, y, width, height = (
            auto_shop_vision.card_terminal_region_from_item_match(match)
        )
        terminal_crop = frame[y:y + height, x:x + width]

        assert vision.find_in_gray_multiscale(
            terminal_crop,
            auto_shop.AUTO_SHOP_UI_TEMPLATES["out_of_stock"],
        ), f"{item_key} terminal region clipped Out of Stock"


def test_auto_shop_uses_the_same_utc_midnight_reset_as_challenge():
    import main

    assert auto_shop.AUTO_SHOP_RESET_SCHEDULE == main.CHALLENGE_RESET_SCHEDULE
    assert auto_shop.current_auto_shop_period(_epoch(2026, 7, 29, 23, 59, 59)) == "2026-07-29"
    assert auto_shop.current_auto_shop_period(_epoch(2026, 7, 30, 0, 0, 0)) == "2026-07-30"


def test_auto_shop_settings_are_disabled_and_safe_by_default():
    settings = auto_shop.default_auto_shop_settings()

    assert settings["enabled"] is False
    assert settings["shops"]["gold_shop"]["enabled"] is False
    assert set(settings["shops"]["gold_shop"]["items"]) == {
        item["key"] for item in auto_shop.AUTO_SHOP_ITEMS
    }
    assert all(
        value == {"enabled": False, "target": 1}
        for value in settings["shops"]["gold_shop"]["items"].values()
    )


def test_auto_shop_settings_normalize_known_items_and_reject_unsafe_targets():
    settings = auto_shop.normalize_auto_shop_settings({
        "enabled": True,
        "shops": {
            "gold_shop": {
                "enabled": True,
                "items": {
                    "cursed_boba": {"enabled": True, "target": "Max"},
                    "red_flower": {"enabled": True, "target": 999},
                    "unknown_item": {"enabled": True, "target": "max"},
                },
            },
        },
    })

    gold_shop = settings["shops"]["gold_shop"]
    assert settings["enabled"] is True
    assert gold_shop["enabled"] is True
    assert gold_shop["items"]["cursed_boba"] == {"enabled": True, "target": "max"}
    assert gold_shop["items"]["red_flower"] == {"enabled": True, "target": 1}
    assert "unknown_item" not in gold_shop["items"]


def test_purchase_plan_counts_manual_purchases_toward_numeric_target():
    plan = auto_shop.calculate_purchase_plan("cursed_boba", 15, current_left=40)

    assert plan == {
        "daily_maximum": 50,
        "current_left": 40,
        "already_bought": 10,
        "desired_total": 15,
        "pending_amount": 5,
        "status": auto_shop.STATUS_PENDING,
    }


def test_purchase_plan_buys_only_the_difference_after_target_increase():
    plan = auto_shop.calculate_purchase_plan("cursed_boba", 20, current_left=35)

    assert plan["already_bought"] == 15
    assert plan["pending_amount"] == 5


def test_purchase_plan_stops_when_reduced_target_was_already_met():
    plan = auto_shop.calculate_purchase_plan("cursed_boba", 5, current_left=40)

    assert plan["pending_amount"] == 0
    assert plan["status"] == auto_shop.STATUS_COMPLETED


def test_max_target_uses_every_remaining_item():
    plan = auto_shop.calculate_purchase_plan("trait_crystal", "Max", current_left=17)

    assert plan["desired_total"] == 25
    assert plan["pending_amount"] == 17


def test_numeric_target_never_types_above_the_available_stock():
    plan = auto_shop.calculate_purchase_plan("cursed_boba", 50, current_left=3)

    assert plan["already_bought"] == 47
    assert plan["pending_amount"] == 3


def test_zero_stock_is_terminal_out_of_stock():
    plan = auto_shop.calculate_purchase_plan("equipment_lock", "max", current_left=0)

    assert plan["pending_amount"] == 0
    assert plan["status"] == auto_shop.STATUS_OUT_OF_STOCK


@pytest.mark.parametrize("target", [0, 51, True, "invalid", None])
def test_invalid_targets_are_rejected(target):
    with pytest.raises(ValueError):
        auto_shop.calculate_purchase_plan("cursed_boba", target, current_left=50)


def test_left_ocr_requires_two_matching_in_range_reads():
    assert auto_shop.parse_left_count("50 Left!", 50) == 50
    assert auto_shop.parse_left_count("150 Left!", 50) is None
    assert auto_shop.consensus_left_count([50, 50, 5], 50) == 50
    assert auto_shop.consensus_left_count([50, 49, None], 50) is None


def test_visual_signature_detects_a_small_text_change():
    before = np.zeros((24, 96, 3), dtype=np.uint8)
    after = before.copy()
    cv2.putText(before, "50 Left!", (2, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    cv2.putText(after, "49 Left!", (2, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)

    before_signature = auto_shop.build_stock_signature(before)
    after_signature = auto_shop.build_stock_signature(after)

    assert auto_shop.stock_signature_distance(before_signature, after_signature) > 0
    assert auto_shop.stock_signature_distance(before_signature, before_signature) == 0


def test_stock_region_tracks_the_current_item_match_after_scroll():
    first = auto_shop_vision.stock_region_from_item_match({"x": 234, "y": 102, "w": 61, "h": 55})
    scrolled = auto_shop_vision.stock_region_from_item_match({"x": 234, "y": 302, "w": 61, "h": 55})

    assert first == (222, 81, 64, 24)
    assert scrolled == (222, 281, 64, 24)


def test_item_relative_regions_cover_out_of_stock_and_initial_buy():
    match = {"x": 429, "y": 245, "w": 61, "h": 55}

    assert auto_shop_vision.stock_status_region_from_item_match(match) == (425, 217, 90, 46)
    assert auto_shop_vision.initial_buy_region_from_item_match(match) == (391, 382, 140, 42)
    assert auto_shop_vision.card_terminal_region_from_item_match(
        match
    ) == (391, 217, 140, 207)


def test_initial_buy_region_contains_the_full_live_button():
    live_mana_flask_match = {"x": 438, "y": 345, "w": 57, "h": 61}

    assert auto_shop_vision.initial_buy_region_from_item_match(
        live_mana_flask_match
    ) == (398, 485, 140, 42)


def test_buy_region_uses_the_icon_center_for_wide_item_templates():
    delicious_pie_match = {
        "x": 429,
        "y": 245,
        "w": 67,
        "h": 53,
        "cx": 462,
        "cy": 271,
    }

    assert auto_shop_vision.initial_buy_region_from_item_match(
        delicious_pie_match
    ) == (394, 381, 140, 42)


def test_shop_buy_template_includes_each_known_price_variant():
    paths = vision.template_variant_paths("shop_buy")
    names = {Path(path).name for path in paths}

    assert KNOWN_GOLD_SHOP_BUY_VARIANTS <= names


def test_stock_crop_rejects_regions_outside_the_frame():
    frame = np.zeros((100, 100, 3), dtype=np.uint8)

    assert auto_shop_vision.crop_region(frame, (10, 20, 30, 15)).shape == (15, 30, 3)
    with pytest.raises(ValueError):
        auto_shop_vision.crop_region(frame, (-1, 20, 30, 15))


def test_modal_regions_are_anchored_to_cancel_in_reference_space():
    cancel = {"x": 579, "y": 420, "w": 181, "h": 28}

    assert auto_shop_vision.amount_input_region_from_cancel(cancel) == (394, 374, 48, 29)
    assert auto_shop_vision.amount_toggle_region_from_cancel(cancel) == (710, 374, 48, 29)
    assert auto_shop_vision.final_buy_region_from_cancel(cancel) == (389, 417, 185, 36)
    assert auto_shop_vision.cancel_click_point(cancel) == (754, 434)


def test_modal_transition_distinguishes_no_currency_success_and_abort():
    assert (
        auto_shop_vision.classify_modal_transition(False, False)
        == auto_shop_vision.MODAL_NOT_OPENED
    )
    assert auto_shop_vision.classify_modal_transition(False, True) == auto_shop_vision.MODAL_OPENED
    assert (
        auto_shop_vision.classify_modal_transition(True, False)
        == auto_shop_vision.MODAL_CLOSED_AFTER_BUY
    )
    assert (
        auto_shop_vision.classify_modal_transition(True, True)
        == auto_shop_vision.MODAL_REMAINS_AFTER_BUY
    )
    assert auto_shop_vision.classify_modal_transition(True, None) == auto_shop_vision.MODAL_UNKNOWN


def test_full_reference_screens_confirm_modal_toggle_and_out_of_stock_assets():
    max_frame = cv2.imread(str(AUTO_SHOP_REFERENCES / "buy_amount_max_full.png"), cv2.IMREAD_GRAYSCALE)
    min_frame = cv2.imread(str(AUTO_SHOP_REFERENCES / "buy_amount_min_full.png"), cv2.IMREAD_GRAYSCALE)
    stock_frame = cv2.imread(
        str(AUTO_SHOP_REFERENCES / "gold_shop_out_of_stock_full.png"),
        cv2.IMREAD_GRAYSCALE,
    )

    max_cancel = vision.find_in_gray_multiscale(max_frame, "shop_cancel")
    min_cancel = vision.find_in_gray_multiscale(min_frame, "shop_cancel")
    assert max_cancel and min_cancel
    assert vision.find_in_gray_multiscale(max_frame, "shop_amount_min")
    assert vision.find_in_gray_multiscale(min_frame, "shop_amount_max")
    assert vision.find_in_gray_multiscale(stock_frame, "shop_out_of_stock")


def test_stock_ocr_consensus_uses_three_independent_crops(monkeypatch):
    readings = iter([[50, 50, 5], [50, 50], [5, 5]])
    monkeypatch.setattr(auto_shop_vision, "_ocr_values", lambda _crop, _maximum: next(readings))
    crops = [np.zeros((18, 64, 3), dtype=np.uint8) for _ in range(3)]

    assert auto_shop_vision.read_left_consensus(crops, 50) == 50


def test_stock_ocr_vote_rejects_single_reads_and_ties(monkeypatch):
    crop = np.zeros((18, 64, 3), dtype=np.uint8)

    monkeypatch.setattr(auto_shop_vision, "_ocr_values", lambda _crop, _maximum: [75, 75, 7])
    assert auto_shop_vision.read_left_count(crop, 75) == 75

    monkeypatch.setattr(auto_shop_vision, "_ocr_values", lambda _crop, _maximum: [75, 7])
    assert auto_shop_vision.read_left_count(crop, 75) is None


def test_stock_ocr_uses_an_expanded_crop_when_the_tight_vote_is_ambiguous(
        monkeypatch):
    crop = np.zeros((24, 64, 3), dtype=np.uint8)
    heights = []

    def readings(candidate, _maximum):
        heights.append(candidate.shape[0])
        return [1] if candidate.shape[0] < crop.shape[0] else [10, 10, 10]

    monkeypatch.setattr(auto_shop_vision, "_ocr_values", readings)

    assert auto_shop_vision.read_left_count(crop, 10) == 10
    assert heights == [18, 24]


def test_stock_verification_requires_ocr_and_visual_change_to_agree():
    assert (
        auto_shop.classify_stock_verification(50, 49, True)
        == auto_shop.VERIFICATION_PROGRESS
    )
    assert (
        auto_shop.classify_stock_verification(50, 50, False)
        == auto_shop.VERIFICATION_UNCHANGED
    )
    assert (
        auto_shop.classify_stock_verification(50, 49, False)
        == auto_shop.VERIFICATION_PENDING
    )
    assert (
        auto_shop.classify_stock_verification(50, None, True)
        == auto_shop.VERIFICATION_PENDING
    )
    assert (
        auto_shop.classify_stock_verification(50, None, None, out_of_stock=True)
        == auto_shop.VERIFICATION_OUT_OF_STOCK
    )


def test_min_means_max_is_already_selected():
    assert auto_shop.max_toggle_action(max_visible=True, min_visible=False) == auto_shop.MAX_TOGGLE_CLICK
    assert (
        auto_shop.max_toggle_action(max_visible=False, min_visible=True)
        == auto_shop.MAX_TOGGLE_ALREADY_SELECTED
    )
    assert auto_shop.max_toggle_action(max_visible=False, min_visible=False) == auto_shop.MAX_TOGGLE_UNKNOWN


def test_item_stops_after_three_failures_and_resets_next_period():
    state = auto_shop.fresh_item_state("2026-07-30")
    state = auto_shop.record_item_failure(state, "2026-07-30")
    state = auto_shop.record_item_failure(state, "2026-07-30")
    state = auto_shop.record_item_failure(state, "2026-07-30")

    assert state["attempts"] == 3
    assert state["status"] == auto_shop.STATUS_FAILED_TODAY

    reset = auto_shop.normalize_item_state(state, "2026-07-31")
    assert reset == auto_shop.fresh_item_state("2026-07-31")


def test_max_inventory_is_terminal_only_for_the_current_period():
    state = {
        **auto_shop.fresh_item_state("2026-07-30"),
        "status": "max_inventory",
    }

    assert (
        auto_shop.normalize_item_state(state, "2026-07-30")["status"]
        == "max_inventory"
    )
    assert (
        auto_shop.normalize_item_state(state, "2026-07-31")["status"]
        == auto_shop.STATUS_PENDING
    )


def test_navigation_failures_have_a_separate_daily_limit():
    state = auto_shop.fresh_shop_state("2026-07-30")
    state = auto_shop.record_navigation_failure(state, "2026-07-30")
    state = auto_shop.record_navigation_failure(state, "2026-07-30")
    state = auto_shop.record_navigation_failure(state, "2026-07-30")

    assert state["navigation_failures"] == 3
    assert state["status"] == auto_shop.STATUS_FAILED_TODAY
    assert auto_shop.normalize_shop_state(state, "2026-07-31") == auto_shop.fresh_shop_state("2026-07-31")


def test_cancel_click_is_near_the_far_right_edge_and_inside_the_button():
    point = auto_shop.cancel_right_edge_point(left=10, top=20, width=181, height=28)

    assert point == (185, 34)
    assert 10 <= point[0] < 191
    assert 20 <= point[1] < 48
