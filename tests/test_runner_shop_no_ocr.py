import threading
from unittest.mock import MagicMock

import numpy as np
import pytest

from core import auto_shop
from core import runner_shop
from core.runner import MacroRunner


def _runner(saved_items):
    return MacroRunner(
        MagicMock(),
        MagicMock(),
        MagicMock(),
        save_auto_shop_item_state=lambda shop, item, state: saved_items.append(
            (shop, item, state)
        ),
    )


def _item(target=5):
    return {
        "key": "cursed_boba",
        "name": "Cursed Boba",
        "daily_maximum": 50,
        "target": target,
        "state": auto_shop.fresh_item_state("2026-07-30"),
    }


def test_visible_numeric_purchase_remains_due_without_reading_stock(monkeypatch):
    """A numeric purchase must repeat on later passes until the card is terminal."""
    saved_items = []
    runner = _runner(saved_items)
    cancel = {"x": 579, "y": 420, "w": 181, "h": 28}
    item = _item(target=5)
    runner._shop_read_observation = MagicMock(
        side_effect=AssertionError("The no-OCR path must not read stock")
    )
    monkeypatch.setattr(runner, "_shop_find_terminal_label", lambda *_args: None)
    monkeypatch.setattr(runner, "_shop_open_purchase_modal", lambda *_args: cancel)
    monkeypatch.setattr(runner, "_shop_configure_amount", lambda *_args: True)
    monkeypatch.setattr(runner, "_shop_confirm_purchase", lambda *_args: True)

    runner._shop_process_visible_item(
        1,
        "gold_shop",
        item,
        {"x": 429, "y": 245, "w": 61, "h": 55},
        threading.Event(),
    )

    runner._shop_read_observation.assert_not_called()
    assert saved_items[-1][2]["status"] == auto_shop.STATUS_RETRY_PENDING
    assert saved_items[-1][2]["verification"] is None


def test_visible_max_purchase_is_completed_for_the_current_day(monkeypatch):
    saved_items = []
    runner = _runner(saved_items)
    cancel = {"x": 579, "y": 420, "w": 181, "h": 28}
    item = _item(target="max")
    monkeypatch.setattr(runner, "_shop_find_terminal_label", lambda *_args: None)
    monkeypatch.setattr(runner, "_shop_open_purchase_modal", lambda *_args: cancel)
    monkeypatch.setattr(runner, "_shop_configure_amount", lambda *_args: True)
    monkeypatch.setattr(runner, "_shop_confirm_purchase", lambda *_args: True)

    runner._shop_process_visible_item(
        1,
        "gold_shop",
        item,
        {"x": 429, "y": 245, "w": 61, "h": 55},
        threading.Event(),
    )

    assert saved_items[-1][2]["status"] == auto_shop.STATUS_COMPLETED


def test_visible_purchase_with_uncertain_modal_requires_a_manual_today_reset(monkeypatch):
    """A final Buy that does not close must not restart the shop every task."""
    saved_items = []
    runner = _runner(saved_items)
    cancel = {"x": 579, "y": 420, "w": 181, "h": 28}
    item = _item(target="max")
    monkeypatch.setattr(runner, "_shop_find_terminal_label", lambda *_args: None)
    monkeypatch.setattr(runner, "_shop_open_purchase_modal", lambda *_args: cancel)
    monkeypatch.setattr(runner, "_shop_configure_amount", lambda *_args: True)
    monkeypatch.setattr(runner, "_shop_confirm_purchase", lambda *_args: False)

    runner._shop_process_visible_item(
        1,
        "gold_shop",
        item,
        {"x": 429, "y": 245, "w": 61, "h": 55},
        threading.Event(),
    )

    assert saved_items[-1][2]["status"] == auto_shop.STATUS_FAILED_TODAY


def test_visible_item_lookup_does_not_scroll_when_the_card_is_clipped(monkeypatch):
    """Row alignment owns scrolling, so an item miss cannot move later cards."""
    runner = _runner([])
    item = _item()
    clipped = {"x": 429, "y": 445, "w": 61, "h": 55}
    monkeypatch.setattr(runner, "_checkpoint", lambda _stop: False)
    monkeypatch.setattr(
        "core.runner_shop.vision.find_image",
        lambda *_args, **_kwargs: clipped,
    )

    assert runner._shop_find_visible_item(1, item, threading.Event()) is None
    runner._mouse.scroll.assert_not_called()


def test_item_lookup_is_restricted_to_its_expected_column(monkeypatch):
    runner = _runner([])
    regions = []
    monkeypatch.setattr(runner, "_checkpoint", lambda _stop: False)

    def find_image(_hwnd, _template, **kwargs):
        regions.append(kwargs["region"])
        return {
            "x": 429, "y": 325, "w": 61, "h": 55,
        }

    monkeypatch.setattr("core.runner_shop.vision.find_image", find_image)

    runner._shop_find_visible_item(
        1,
        {
            **_item(),
            "key": "equipment_lock",
            "name": "Equipment Lock",
            "daily_maximum": 10,
        },
        threading.Event(),
    )
    runner._shop_find_visible_item(
        1,
        {
            **_item(),
            "key": "stat_reroll",
            "name": "Stat Reroll",
            "daily_maximum": 10,
        },
        threading.Event(),
    )

    assert regions == [
        (398, 218, 154, 362),
        (552, 218, 154, 362),
    ]


def test_no_ocr_sweep_uses_absolute_scroll_position_for_each_due_row(monkeypatch):
    """Every due row must start from Top and use only its calibrated delta."""
    runner = _runner([])
    items = [
        {**_item(), "key": "stat_lock", "name": "Stat Lock", "daily_maximum": 10},
        {
            **_item(),
            "key": "equipment_lock",
            "name": "Equipment Lock",
            "daily_maximum": 10,
        },
        {**_item(), "key": "mana_flask", "name": "Mana Flask", "daily_maximum": 150},
        {**_item(), "key": "frown_fruit", "name": "Frown Fruit", "daily_maximum": 100},
    ]
    found = []
    processed = []
    runner._shop_find_item = MagicMock(
        side_effect=AssertionError("The legacy finder resets the list per item")
    )
    monkeypatch.setattr(runner, "_checkpoint", lambda _stop: False)
    monkeypatch.setattr(
        runner,
        "_shop_find_slot_out_of_stock",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        runner,
        "_shop_find_visible_item",
        lambda _hwnd, item, _stop: found.append(item["key"]) or {
            "x": 429, "y": 325, "w": 61, "h": 55,
        },
    )
    monkeypatch.setattr(
        runner,
        "_shop_process_visible_item",
        lambda _hwnd, _shop, item, _match, _stop: processed.append(item["key"]),
    )
    monkeypatch.setattr(
        "core.runner_shop.vision.ref_to_screen",
        lambda _hwnd, x, y: (x, y),
    )
    monkeypatch.setattr("core.runner_shop.time.sleep", lambda _seconds: None)

    runner._shop_run_no_ocr_sweep(1, "gold_shop", items, threading.Event())

    assert found == [
        "frown_fruit",
        "mana_flask",
        "equipment_lock",
        "stat_lock",
    ]
    assert processed == found
    assert [call.args for call in runner._mouse.scroll.call_args_list] == [
        (runner_shop.SHOP_SCROLL_RESET_AMOUNT,),
        (-120,),
        (runner_shop.SHOP_SCROLL_RESET_AMOUNT,),
        (-480,),
        (runner_shop.SHOP_SCROLL_RESET_AMOUNT,),
        (-960,),
        (runner_shop.SHOP_SCROLL_RESET_AMOUNT,),
        (runner_shop.SHOP_BOTTOM_SCROLL_AMOUNT,),
    ]


def test_missing_top_row_never_triggers_search_scrolling(monkeypatch):
    """A failed identity check must skip the row without moving toward Bottom."""
    runner = _runner([])
    items = [
        _item(),
        {**_item(), "key": "red_flower", "name": "Red Flower", "daily_maximum": 75},
    ]
    processed = []
    monkeypatch.setattr(runner, "_checkpoint", lambda _stop: False)
    monkeypatch.setattr(
        runner,
        "_shop_find_slot_out_of_stock",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        "core.runner_shop.vision.find_image",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        runner,
        "_shop_process_visible_item",
        lambda _hwnd, _shop, item, _match, _stop: processed.append(item["key"]),
    )
    monkeypatch.setattr(
        "core.runner_shop.vision.ref_to_screen",
        lambda _hwnd, x, y: (x, y),
    )
    monkeypatch.setattr("core.runner_shop.time.sleep", lambda _seconds: None)

    runner._shop_run_no_ocr_sweep(1, "gold_shop", items, threading.Event())

    assert processed == []
    assert [call.args for call in runner._mouse.scroll.call_args_list] == [
        (runner_shop.SHOP_SCROLL_RESET_AMOUNT,),
    ]


def test_missing_calibrated_item_is_saved_for_a_later_retry(monkeypatch):
    """A transient identity miss must stay actionable instead of disappearing."""
    saved_items = []
    runner = _runner(saved_items)
    runner._log = MagicMock()
    item = _item()
    monkeypatch.setattr(runner, "_checkpoint", lambda _stop: False)
    monkeypatch.setattr(
        runner,
        "_shop_find_slot_out_of_stock",
        lambda *_args: False,
    )
    monkeypatch.setattr(runner, "_shop_find_visible_item", lambda *_args: None)
    monkeypatch.setattr(
        "core.runner_shop.vision.ref_to_screen",
        lambda _hwnd, x, y: (x, y),
    )
    monkeypatch.setattr("core.runner_shop.time.sleep", lambda _seconds: None)

    runner._shop_run_no_ocr_sweep(
        1,
        "gold_shop",
        [item],
        threading.Event(),
    )

    assert saved_items[-1][1] == "cursed_boba"
    assert saved_items[-1][2]["status"] == auto_shop.STATUS_RETRY_PENDING
    messages = [call.args[0] for call in runner._log.call_args_list]
    assert any("retry on the next Auto Shop pass" in message for message in messages)


def test_out_of_stock_slot_finishes_item_without_matching_its_icon(monkeypatch):
    """A dimmed sold-out card must be terminal even when its icon misses."""
    saved_items = []
    runner = _runner(saved_items)
    item = _item()
    runner._shop_find_visible_item = MagicMock(
        side_effect=AssertionError("Out of Stock must be checked before item identity")
    )
    monkeypatch.setattr(runner, "_checkpoint", lambda _stop: False)
    monkeypatch.setattr(
        runner,
        "_shop_find_slot_out_of_stock",
        lambda _hwnd, checked, scroll, _stop: (
            checked["key"] == "cursed_boba" and scroll == 0
        ),
    )
    monkeypatch.setattr(
        "core.runner_shop.vision.ref_to_screen",
        lambda _hwnd, x, y: (x, y),
    )
    monkeypatch.setattr("core.runner_shop.time.sleep", lambda _seconds: None)

    runner._shop_run_no_ocr_sweep(
        1,
        "gold_shop",
        [item],
        threading.Event(),
    )

    runner._shop_find_visible_item.assert_not_called()
    assert saved_items[-1][1] == "cursed_boba"
    assert saved_items[-1][2]["status"] == auto_shop.STATUS_OUT_OF_STOCK


def test_out_of_stock_slot_search_uses_the_calibrated_row_and_column(monkeypatch):
    """The same sold-out label must be attributed only to the expected card."""
    runner = _runner([])
    searched = []

    def find_image(_hwnd, name, **kwargs):
        searched.append((name, kwargs["region"]))
        return {"x": 430, "y": 230, "w": 65, "h": 14}

    monkeypatch.setattr("core.runner_shop.vision.find_image", find_image)

    assert runner._shop_find_slot_out_of_stock(
        1,
        _item(),
        0,
        threading.Event(),
    ) is True
    assert runner._shop_find_slot_out_of_stock(
        1,
        {
            **_item(),
            "key": "delicious_pie",
            "name": "Delicious Pie",
            "daily_maximum": 125,
        },
        -120,
        threading.Event(),
    ) is True
    assert searched == [
        ("shop_out_of_stock", (398, 218, 154, 90)),
        ("shop_out_of_stock", (552, 300, 154, 110)),
    ]


def test_auto_shop_run_delegates_enabled_items_to_the_no_ocr_sweep(monkeypatch):
    """The public runner path must not retain the old OCR item processor."""
    item = _item()
    item["enabled"] = True
    settings = {
        "enabled": True,
        "shops": {
            "gold_shop": {
                "enabled": True,
                "state": auto_shop.fresh_shop_state("2026-07-30"),
                "items": [item],
            },
        },
    }
    runner = MacroRunner(
        MagicMock(),
        MagicMock(),
        MagicMock(),
        get_auto_shop_settings=lambda: settings,
    )
    dispatched = []
    monkeypatch.setattr("core.runner_shop.wm.show_window", lambda _hwnd: None)
    monkeypatch.setattr("core.runner_shop.wm.activate_window", lambda _hwnd: True)
    monkeypatch.setattr(runner, "_ensure_lobby", lambda *_args: True)
    monkeypatch.setattr(runner, "_shop_enter_gold_shop", lambda *_args: True)
    monkeypatch.setattr(
        runner,
        "_shop_run_no_ocr_sweep",
        lambda _hwnd, shop, items, _stop: dispatched.append((shop, items)),
    )
    runner._shop_process_item = MagicMock(
        side_effect=AssertionError("The OCR processor must not run")
    )
    monkeypatch.setattr("core.runner_shop.vision.find_image", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("core.runner_shop.time.sleep", lambda _seconds: None)

    runner._run_auto_shop(1, threading.Event())

    assert dispatched == [("gold_shop", [item])]


def test_public_auto_shop_run_never_reaches_stock_ocr(monkeypatch):
    """The runner integration must retain the no-OCR guarantee after refactors."""
    item = _item()
    item["enabled"] = True
    settings = {
        "enabled": True,
        "shops": {
            "gold_shop": {
                "enabled": True,
                "state": auto_shop.fresh_shop_state("2026-07-30"),
                "items": [item],
            },
        },
    }
    saved_items = []
    runner = MacroRunner(
        MagicMock(),
        MagicMock(),
        MagicMock(),
        get_auto_shop_settings=lambda: settings,
        save_auto_shop_item_state=lambda shop, key, state: saved_items.append(
            (shop, key, state)
        ),
    )
    cancel = {"x": 579, "y": 420, "w": 181, "h": 28}
    runner._shop_read_observation = MagicMock(
        side_effect=AssertionError("The public Auto Shop path must not use OCR")
    )
    monkeypatch.setattr("core.runner_shop.wm.show_window", lambda _hwnd: None)
    monkeypatch.setattr("core.runner_shop.wm.activate_window", lambda _hwnd: True)
    monkeypatch.setattr(runner, "_ensure_lobby", lambda *_args: True)
    monkeypatch.setattr(runner, "_shop_enter_gold_shop", lambda *_args: True)
    monkeypatch.setattr(
        runner,
        "_shop_find_slot_out_of_stock",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        runner,
        "_shop_find_visible_item",
        lambda *_args: {"x": 429, "y": 325, "w": 61, "h": 55},
    )
    monkeypatch.setattr(runner, "_shop_find_terminal_label", lambda *_args: None)
    monkeypatch.setattr(runner, "_shop_open_purchase_modal", lambda *_args: cancel)
    monkeypatch.setattr(runner, "_shop_configure_amount", lambda *_args: True)
    monkeypatch.setattr(runner, "_shop_confirm_purchase", lambda *_args: True)
    monkeypatch.setattr("core.runner_shop.vision.find_image", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("core.runner_shop.vision.ref_to_screen", lambda _hwnd, x, y: (x, y))
    monkeypatch.setattr("core.runner_shop.time.sleep", lambda _seconds: None)

    runner._run_auto_shop(1, threading.Event())

    runner._shop_read_observation.assert_not_called()
    assert saved_items[-1][2]["status"] == auto_shop.STATUS_RETRY_PENDING


def test_open_modal_clicks_the_green_buy_region_without_matching_a_price(monkeypatch):
    """Price artwork must not decide whether a known card Buy can be clicked."""
    runner = _runner([])
    item_match = {"x": 429, "y": 245, "w": 61, "h": 55}
    cancel = {"x": 579, "y": 420, "w": 181, "h": 28}
    clicked = []
    monkeypatch.setattr(
        "core.runner_shop.vision.capture_game_bgr",
        lambda *_args, **_kwargs: np.full((43, 142, 3), (0, 255, 70), dtype=np.uint8),
    )
    monkeypatch.setattr(
        "core.runner_shop.vision.find_color_run",
        lambda *_args, **_kwargs: pytest.fail("Text must not break a total-color check"),
    )
    monkeypatch.setattr(
        "core.runner_shop.vision.wait_for_image",
        lambda _hwnd, name, **_kwargs: cancel if name == "shop_cancel" else pytest.fail(
            "A dynamic Buy price must not be searched"
        ),
    )
    monkeypatch.setattr(
        "core.runner_shop.vision.click_match",
        lambda _mouse, _hwnd, match: clicked.append(match),
    )

    assert runner._shop_open_purchase_modal(1, item_match, threading.Event()) == cancel
    assert clicked == [
        {"x": 391, "y": 382, "w": 140, "h": 42, "cx": 461, "cy": 403},
    ]


def test_max_amount_checks_max_and_min_templates_before_clicking(monkeypatch):
    """Verify that Max toggle checks template states before clicking to avoid buying 1 instead of Max."""
    runner = _runner([])
    cancel = {"x": 579, "y": 420, "w": 181, "h": 28}
    monkeypatch.setattr(
        "core.runner_shop.vision.find_image",
        lambda _hwnd, name, **_kwargs: (
            {"x": 710, "y": 374, "w": 43, "h": 22}
            if name == "shop_amount_max" else None
        ),
    )
    monkeypatch.setattr(
        "core.runner_shop.vision.ref_to_screen",
        lambda _hwnd, x, y: (x, y),
    )

    assert runner._shop_configure_amount(1, cancel, "max", 50, threading.Event()) is True
    runner._mouse.click.assert_called_once_with(734, 388)
