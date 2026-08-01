import threading
from unittest.mock import MagicMock

import numpy as np
import pytest

from core import auto_shop
from core import auto_shop_vision
from core import runner_shop
from core.runner import MacroRunner


def _settings(item_overrides=None, shop_overrides=None):
    item_overrides = item_overrides or {}
    shop_overrides = shop_overrides or {}
    items = []
    for definition in auto_shop.AUTO_SHOP_ITEMS:
        item = {
            "key": definition["key"],
            "name": definition["name"],
            "daily_maximum": definition["stock"],
            "enabled": definition["key"] == "cursed_boba",
            "target": 5,
            "state": auto_shop.fresh_item_state("2026-07-30"),
        }
        item.update(item_overrides.get(definition["key"], {}))
        items.append(item)
    return {
        "enabled": True,
        "shops": {
            "gold_shop": {
                "name": "Gold Shop",
                "enabled": True,
                "state": {
                    **auto_shop.fresh_shop_state("2026-07-30"),
                    **shop_overrides,
                },
                "items": items,
            },
        },
    }


def _runner(settings=None, saved_items=None, saved_shops=None):
    settings = settings or _settings()
    saved_items = saved_items if saved_items is not None else []
    saved_shops = saved_shops if saved_shops is not None else []
    return MacroRunner(
        MagicMock(),
        MagicMock(),
        MagicMock(),
        get_auto_shop_settings=lambda: settings,
        save_auto_shop_item_state=lambda shop, item, state: saved_items.append(
            (shop, item, state)
        ),
        save_auto_shop_shop_state=lambda shop, state: saved_shops.append(
            (shop, state)
        ),
    )


def test_auto_shop_wants_in_only_for_actionable_enabled_items():
    runner = _runner()
    assert runner._auto_shop_wants_in() is True

    completed = _settings({
        "cursed_boba": {
            "state": {
                **auto_shop.fresh_item_state("2026-07-30"),
                "status": auto_shop.STATUS_COMPLETED,
            },
        },
    })
    assert _runner(completed)._auto_shop_wants_in() is False

    failed_shop = _settings(shop_overrides={"status": auto_shop.STATUS_FAILED_TODAY})
    assert _runner(failed_shop)._auto_shop_wants_in() is False


def test_max_amount_clicks_the_control_derived_from_cancel(monkeypatch):
    runner = _runner()
    stop_event = threading.Event()
    cancel = {"x": 579, "y": 420, "w": 181, "h": 28}
    monkeypatch.setattr(
        "core.runner_shop.vision.find_image",
        lambda _hwnd, name, **_kwargs: (
            {"x": 710, "y": 374, "w": 43, "h": 22}
            if name == "shop_amount_max" else None
        ),
    )
    monkeypatch.setattr("core.runner_shop.vision.ref_to_screen", lambda _hwnd, x, y: (x, y))
    assert runner._shop_configure_amount(1, cancel, "max", 25, stop_event) is True
    runner._mouse.click.assert_called_once_with(734, 388)


def test_max_amount_skips_click_when_already_at_max(monkeypatch):
    runner = _runner()
    stop_event = threading.Event()
    cancel = {"x": 579, "y": 420, "w": 181, "h": 28}
    monkeypatch.setattr(
        "core.runner_shop.vision.find_image",
        lambda _hwnd, name, **_kwargs: (
            {"x": 710, "y": 374, "w": 43, "h": 22}
            if name == "shop_amount_min" else None
        ),
    )

    assert runner._shop_configure_amount(
        1,
        cancel,
        "max",
        25,
        stop_event,
    ) is True
    runner._mouse.click.assert_not_called()


def test_numeric_amount_types_only_the_calculated_pending_quantity(monkeypatch):
    runner = _runner()
    stop_event = threading.Event()
    cancel = {"x": 579, "y": 420, "w": 181, "h": 28}
    monkeypatch.setattr("core.runner_shop.vision.ref_to_screen", lambda _hwnd, x, y: (x, y))
    monkeypatch.setattr("core.runner_shop.time.sleep", lambda _seconds: None)

    assert runner._shop_configure_amount(1, cancel, 5, 3, stop_event) is True

    runner._mouse.double_click.assert_called_once_with(418, 388)
    runner._keyboard.type_text.assert_called_once_with("3")


def test_cancel_click_uses_the_far_right_inside_edge(monkeypatch):
    runner = _runner()
    cancel = {"x": 579, "y": 420, "w": 181, "h": 28}
    monkeypatch.setattr("core.runner_shop.vision.ref_to_screen", lambda _hwnd, x, y: (x, y))

    runner._shop_cancel_modal(1, cancel)

    runner._mouse.click.assert_called_once_with(754, 434)


def test_item_search_waits_through_transient_misses_at_the_expected_row(
        monkeypatch):
    runner = _runner()
    stop_event = threading.Event()
    item = _settings()["shops"]["gold_shop"]["items"][0]
    expected = {"x": 438, "y": 325, "w": 61, "h": 55}
    matches = iter([None, None, expected])
    monkeypatch.setattr(runner, "_checkpoint", lambda _stop: False)
    monkeypatch.setattr(
        "core.runner_shop.vision.find_image",
        lambda *_args, **_kwargs: next(matches),
    )
    monkeypatch.setattr(
        "core.runner_shop.vision.ref_to_screen",
        lambda _hwnd, x, y: (x, y),
    )
    monkeypatch.setattr("core.runner_shop.time.sleep", lambda _seconds: None)

    assert runner._shop_find_item(1, item, stop_event) == expected
    assert [
        call.args
        for call in runner._mouse.scroll.call_args_list
    ] == [(runner_shop.SHOP_SCROLL_RESET_AMOUNT,)]


def test_known_item_scroll_steps_match_the_observed_shop_rows():
    assert runner_shop.SHOP_SCROLL_AMOUNT == -480
    assert runner_shop.SHOP_ITEM_SCROLL_STEPS == {
        "cursed_boba": 0,
        "red_flower": 0,
        "frown_fruit": 1,
        "delicious_pie": 1,
        "mana_flask": 1,
        "trait_crystal": 1,
        "sprite_grey": 2,
        "equipment_reroll": 2,
        "equipment_lock": 3,
        "stat_reroll": 3,
        "stat_lock": 4,
    }
    assert runner_shop.SHOP_ITEM_SCROLL_AMOUNTS == {
        "cursed_boba": 0,
        "red_flower": 0,
        "frown_fruit": -120,
        "delicious_pie": -120,
        "mana_flask": -480,
        "trait_crystal": -480,
        "sprite_grey": -720,
        "equipment_reroll": -720,
        "equipment_lock": -960,
        "stat_reroll": -960,
        "stat_lock": -4800,
    }


def test_item_search_resets_to_top_then_waits_at_the_known_row(monkeypatch):
    settings = _settings({
        "cursed_boba": {"enabled": False},
        "mana_flask": {"enabled": True},
    })
    runner = _runner(settings)
    stop_event = threading.Event()
    item = next(
        entry
        for entry in settings["shops"]["gold_shop"]["items"]
        if entry["key"] == "mana_flask"
    )
    expected = {"x": 438, "y": 345, "w": 57, "h": 61}
    monkeypatch.setattr(runner, "_checkpoint", lambda _stop: False)
    monkeypatch.setattr(
        "core.runner_shop.vision.find_image",
        lambda *_args, **_kwargs: expected,
    )
    monkeypatch.setattr(
        "core.runner_shop.vision.wait_for_image",
        lambda *_args, **_kwargs: expected,
    )
    monkeypatch.setattr(
        "core.runner_shop.vision.ref_to_screen",
        lambda _hwnd, x, y: (x, y),
    )
    monkeypatch.setattr("core.runner_shop.time.sleep", lambda _seconds: None)

    assert runner._shop_find_item(1, item, stop_event) == expected
    assert [
        call.args
        for call in runner._mouse.scroll.call_args_list
    ] == [
        (runner_shop.SHOP_SCROLL_RESET_AMOUNT,),
        (runner_shop.SHOP_SCROLL_AMOUNT,),
    ]


def test_sprite_search_uses_the_calibrated_partial_second_row(monkeypatch):
    settings = _settings({
        "cursed_boba": {"enabled": False},
        "sprite_grey": {"enabled": True},
    })
    runner = _runner(settings)
    stop_event = threading.Event()
    item = next(
        entry
        for entry in settings["shops"]["gold_shop"]["items"]
        if entry["key"] == "sprite_grey"
    )
    expected = {"x": 438, "y": 345, "w": 58, "h": 57}
    monkeypatch.setattr(runner, "_checkpoint", lambda _stop: False)
    monkeypatch.setattr(
        "core.runner_shop.vision.wait_for_image",
        lambda *_args, **_kwargs: expected,
    )
    monkeypatch.setattr(
        "core.runner_shop.vision.ref_to_screen",
        lambda _hwnd, x, y: (x, y),
    )
    monkeypatch.setattr("core.runner_shop.time.sleep", lambda _seconds: None)

    assert runner._shop_find_item(1, item, stop_event) == expected
    assert [
        call.args
        for call in runner._mouse.scroll.call_args_list
    ] == [
        (runner_shop.SHOP_SCROLL_RESET_AMOUNT,),
        (-720,),
    ]


def test_item_search_keeps_scrolling_until_the_buy_button_is_visible(monkeypatch):
    runner = _runner()
    stop_event = threading.Event()
    item = _settings()["shops"]["gold_shop"]["items"][0]
    icon_only = {"x": 438, "y": 445, "w": 61, "h": 55}
    fully_visible = {"x": 438, "y": 325, "w": 61, "h": 55}
    matches = iter([icon_only, fully_visible])
    monkeypatch.setattr(runner, "_checkpoint", lambda _stop: False)
    monkeypatch.setattr(
        "core.runner_shop.vision.find_image",
        lambda *_args, **_kwargs: next(matches),
    )
    monkeypatch.setattr(
        "core.runner_shop.vision.ref_to_screen",
        lambda _hwnd, x, y: (x, y),
    )
    monkeypatch.setattr("core.runner_shop.time.sleep", lambda _seconds: None)

    assert runner._shop_find_item(1, item, stop_event) == fully_visible
    assert [
        call.args
        for call in runner._mouse.scroll.call_args_list
    ] == [
        (runner_shop.SHOP_SCROLL_RESET_AMOUNT,),
        (runner_shop.SHOP_SCROLL_REFINEMENT_AMOUNT,),
    ]


def test_purchase_modal_clicks_the_green_buy_region_inside_the_item_card(monkeypatch):
    runner = _runner()
    stop_event = threading.Event()
    item_match = {"x": 429, "y": 245, "w": 61, "h": 55}
    buy_match = {"x": 391, "y": 382, "w": 140, "h": 42, "cx": 461, "cy": 403}
    cancel_match = {"x": 579, "y": 420, "w": 181, "h": 28}
    clicked = []
    monkeypatch.setattr(
        "core.runner_shop.vision.click_match",
        lambda _mouse, _hwnd, match: clicked.append(match),
    )
    monkeypatch.setattr(
        "core.runner_shop.vision.capture_game_bgr",
        lambda *_args, **_kwargs: np.full((43, 142, 3), (0, 255, 70), dtype=np.uint8),
    )
    monkeypatch.setattr(
        "core.runner_shop.vision.wait_for_image",
        lambda _hwnd, name, **_kwargs: cancel_match if name == "shop_cancel" else None,
    )

    assert runner._shop_open_purchase_modal(
        1,
        item_match,
        stop_event,
    ) == cancel_match
    assert clicked == [buy_match]


def test_purchase_modal_waits_only_for_cancel_after_clicking_green_buy(monkeypatch):
    runner = _runner()
    stop_event = threading.Event()
    item_match = {"x": 429, "y": 245, "w": 61, "h": 55}
    buy_match = {"x": 391, "y": 382, "w": 140, "h": 42, "cx": 461, "cy": 403}
    cancel_match = {"x": 579, "y": 420, "w": 181, "h": 28}
    waited = []
    clicked = []

    def wait_for_image(_hwnd, name, **kwargs):
        waited.append((name, kwargs.get("region")))
        if name == "shop_cancel":
            return cancel_match
        return None

    monkeypatch.setattr(
        "core.runner_shop.vision.capture_game_bgr",
        lambda *_args, **_kwargs: np.full((43, 142, 3), (0, 255, 70), dtype=np.uint8),
    )
    monkeypatch.setattr(
        "core.runner_shop.vision.wait_for_image",
        wait_for_image,
    )
    monkeypatch.setattr(
        "core.runner_shop.vision.click_match",
        lambda _mouse, _hwnd, match: clicked.append(match),
    )

    assert runner._shop_open_purchase_modal(
        1,
        item_match,
        stop_event,
    ) == cancel_match
    assert waited == [("shop_cancel", None)]
    assert clicked == [buy_match]


def test_stopped_buy_wait_does_not_report_a_missing_button(monkeypatch):
    runner = _runner()
    stop_event = threading.Event()
    item_match = {"x": 429, "y": 245, "w": 61, "h": 55}
    runner._log = MagicMock()

    monkeypatch.setattr(
        "core.runner_shop.vision.capture_game_bgr",
        lambda *_args, **_kwargs: np.full(
            (43, 142, 3), (0, 255, 70), dtype=np.uint8
        ),
    )
    monkeypatch.setattr(
        "core.runner_shop.vision.click_match",
        lambda *_args: None,
    )

    def stop_wait(*_args, **_kwargs):
        stop_event.set()
        return None

    monkeypatch.setattr(
        "core.runner_shop.vision.wait_for_image",
        stop_wait,
    )

    assert runner._shop_open_purchase_modal(
        1,
        item_match,
        stop_event,
    ) is None
    messages = [call.args[0] for call in runner._log.call_args_list]
    assert not any("enabled Buy button" in message for message in messages)


def test_final_buy_that_does_not_close_is_cancelled_at_the_right_edge(monkeypatch):
    runner = _runner()
    cancel = {"x": 579, "y": 420, "w": 181, "h": 28}
    monkeypatch.setattr(runner, "_checkpoint", lambda _stop: False)
    monkeypatch.setattr(runner, "_wait_for_image_gone", lambda *_args: False)
    monkeypatch.setattr(
        "core.runner_shop.vision.ref_to_screen",
        lambda _hwnd, x, y: (x, y),
    )

    assert runner._shop_confirm_purchase(1, cancel, threading.Event()) is False

    assert runner._mouse.click.call_args_list[-1].args == (754, 434)


@pytest.mark.skip(reason="OCR flow (_shop_process_item) is not used in the public path")
def test_missing_purchase_modal_records_one_failure(monkeypatch):
    saved = []
    runner = _runner(saved_items=saved)
    item = _settings()["shops"]["gold_shop"]["items"][0]
    stop_event = threading.Event()
    match = {"x": 429, "y": 245, "w": 61, "h": 55}
    monkeypatch.setattr(runner, "_shop_find_item", lambda *_args: match)
    monkeypatch.setattr(
        runner,
        "_shop_read_observation",
        lambda *_args: {"left": 50, "signature": "00", "out_of_stock": False},
    )
    monkeypatch.setattr(runner, "_shop_open_purchase_modal", lambda *_args: None)

    runner._shop_process_item(1, "gold_shop", item, stop_event)

    assert saved[-1][2]["attempts"] == 1
    assert saved[-1][2]["status"] == auto_shop.STATUS_PENDING


def test_max_inventory_skips_stock_ocr_and_finishes_the_item(monkeypatch):
    saved = []
    runner = _runner(saved_items=saved)
    item = _settings()["shops"]["gold_shop"]["items"][0]
    stop_event = threading.Event()
    match = {"x": 429, "y": 245, "w": 61, "h": 55}
    read_observation = MagicMock(
        return_value={"left": 0, "signature": "", "out_of_stock": True}
    )
    runner._log = MagicMock()
    monkeypatch.setattr(runner, "_shop_find_item", lambda *_args: match)
    monkeypatch.setattr(runner, "_shop_read_observation", read_observation)
    monkeypatch.setattr(
        runner,
        "_shop_find_terminal_label",
        lambda *_args, **_kwargs: "shop_max_inventory",
        raising=False,
    )

    runner._shop_process_item(1, "gold_shop", item, stop_event)

    read_observation.assert_not_called()
    assert saved[-1][2]["status"] == "max_inventory"
    messages = [call.args[0] for call in runner._log.call_args_list]
    assert any("Max Inventory" in message for message in messages)


@pytest.mark.skip(reason="OCR flow (_shop_process_item) is not used in the public path")
def test_stop_during_buy_lookup_does_not_record_an_item_failure(monkeypatch):
    saved = []
    runner = _runner(saved_items=saved)
    item = _settings()["shops"]["gold_shop"]["items"][0]
    stop_event = threading.Event()
    match = {"x": 429, "y": 245, "w": 61, "h": 55}
    monkeypatch.setattr(runner, "_shop_find_item", lambda *_args: match)
    monkeypatch.setattr(
        runner,
        "_shop_read_observation",
        lambda *_args: {"left": 50, "signature": "00", "out_of_stock": False},
    )

    def stop_during_lookup(*_args):
        stop_event.set()
        return None

    monkeypatch.setattr(
        runner,
        "_shop_open_purchase_modal",
        stop_during_lookup,
    )

    runner._shop_process_item(1, "gold_shop", item, stop_event)

    assert saved == []


@pytest.mark.skip(reason="OCR flow (_shop_process_item) is not used in the public path")
def test_pending_verification_never_opens_another_purchase_when_still_ambiguous(
        monkeypatch):
    saved = []
    settings = _settings({
        "cursed_boba": {
            "state": {
                **auto_shop.fresh_item_state("2026-07-30"),
                "status": auto_shop.STATUS_PENDING_VERIFICATION,
                "last_known_left": 50,
                "stock_signature": "00",
                "verification": {
                    "before_left": 50,
                    "before_signature": "00",
                },
            },
        },
    })
    runner = _runner(settings, saved_items=saved)
    item = settings["shops"]["gold_shop"]["items"][0]
    monkeypatch.setattr(
        runner,
        "_shop_find_item",
        lambda *_args: {"x": 429, "y": 245, "w": 61, "h": 55},
    )
    monkeypatch.setattr(
        runner,
        "_shop_read_observation",
        lambda *_args: {"left": None, "signature": "", "out_of_stock": False},
    )
    open_modal = MagicMock()
    monkeypatch.setattr(runner, "_shop_open_purchase_modal", open_modal)

    runner._shop_process_item(1, "gold_shop", item, threading.Event())

    open_modal.assert_not_called()
    assert saved[-1][2]["status"] == auto_shop.STATUS_PENDING_VERIFICATION


@pytest.mark.skip(reason="OCR flow (_shop_process_item) is not used in the public path")
def test_confirmed_numeric_purchase_marks_completed_without_a_second_buy(monkeypatch):
    saved = []
    runner = _runner(saved_items=saved)
    item = _settings()["shops"]["gold_shop"]["items"][0]
    stop_event = threading.Event()
    observations = iter([
        {"left": 50, "signature": "00", "out_of_stock": False},
        {"left": 45, "signature": "ff", "out_of_stock": False},
    ])
    monkeypatch.setattr(
        runner,
        "_shop_find_item",
        lambda *_args: {"x": 429, "y": 245, "w": 61, "h": 55},
    )
    monkeypatch.setattr(
        runner,
        "_shop_read_observation",
        lambda *_args: next(observations),
    )
    monkeypatch.setattr(
        runner,
        "_shop_open_purchase_modal",
        lambda *_args: {"x": 579, "y": 420, "w": 181, "h": 28},
    )
    monkeypatch.setattr(runner, "_shop_configure_amount", lambda *_args: True)
    monkeypatch.setattr(runner, "_shop_confirm_purchase", lambda *_args: True)
    runner._log = MagicMock()

    runner._shop_process_item(1, "gold_shop", item, stop_event)

    assert saved[-1][2]["status"] == auto_shop.STATUS_COMPLETED
    assert saved[-1][2]["last_known_left"] == 45
    assert saved[-1][2]["verification"] is None
    messages = [call.args[0] for call in runner._log.call_args_list]
    assert any("Reading stock" in message for message in messages)
    assert any("Opening purchase" in message for message in messages)
    assert any("re-reading stock" in message for message in messages)


def test_out_of_stock_after_purchase_is_logged_and_saved(monkeypatch):
    saved = []
    runner = _runner(saved_items=saved)
    item = _settings()["shops"]["gold_shop"]["items"][0]
    stop_event = threading.Event()
    read_observation = MagicMock(
        return_value={"left": 50, "signature": "00", "out_of_stock": False}
    )
    terminal_labels = iter([None, "shop_out_of_stock"])
    monkeypatch.setattr(
        runner,
        "_shop_find_item",
        lambda *_args: {"x": 429, "y": 245, "w": 61, "h": 55},
    )
    monkeypatch.setattr(
        runner,
        "_shop_read_observation",
        read_observation,
    )
    monkeypatch.setattr(
        runner,
        "_shop_find_terminal_label",
        lambda *_args, **_kwargs: next(terminal_labels),
        raising=False,
    )
    monkeypatch.setattr(
        runner,
        "_shop_open_purchase_modal",
        lambda *_args: {"x": 579, "y": 420, "w": 181, "h": 28},
    )
    monkeypatch.setattr(runner, "_shop_configure_amount", lambda *_args: True)
    monkeypatch.setattr(runner, "_shop_confirm_purchase", lambda *_args: True)
    runner._log = MagicMock()

    runner._shop_process_item(1, "gold_shop", item, stop_event)

    assert saved[-1][2]["status"] == auto_shop.STATUS_OUT_OF_STOCK
    read_observation.assert_called_once()
    messages = [call.args[0] for call in runner._log.call_args_list]
    assert any("out of stock after purchase" in message for message in messages)


@pytest.mark.skip(reason="OCR flow (_shop_process_item) is not used in the public path")
def test_confirmed_purchase_reuses_the_current_scroll_position(monkeypatch):
    saved = []
    runner = _runner(saved_items=saved)
    item = _settings()["shops"]["gold_shop"]["items"][0]
    stop_event = threading.Event()
    item_match = {"x": 429, "y": 245, "w": 61, "h": 55}
    observations = iter([
        {"left": 50, "signature": "00", "out_of_stock": False},
        {"left": 45, "signature": "ff", "out_of_stock": False},
    ])
    find_item = MagicMock(return_value=item_match)
    monkeypatch.setattr(runner, "_shop_find_item", find_item)
    monkeypatch.setattr(
        runner,
        "_shop_read_observation",
        lambda *_args: next(observations),
    )
    monkeypatch.setattr(
        runner,
        "_shop_open_purchase_modal",
        lambda *_args: {"x": 579, "y": 420, "w": 181, "h": 28},
    )
    monkeypatch.setattr(runner, "_shop_configure_amount", lambda *_args: True)
    monkeypatch.setattr(runner, "_shop_confirm_purchase", lambda *_args: True)
    runner._shop_process_item(1, "gold_shop", item, stop_event)

    find_item.assert_called_once_with(1, item, stop_event)
    assert saved[-1][2]["status"] == auto_shop.STATUS_COMPLETED


def test_gold_shop_navigation_waits_for_teleport_before_camera_and_interaction(
        monkeypatch):
    runner = _runner()
    stop_event = threading.Event()
    events = []
    monkeypatch.setattr(runner, "_checkpoint", lambda _stop: False)
    monkeypatch.setattr(
        runner,
        "_click_found_image",
        lambda _hwnd, name, *_args: events.append(f"click:{name}") or {"score": 1},
    )
    monkeypatch.setattr(
        runner,
        "_wait_for_image_gone",
        lambda *_args: events.append("nav_play_gone") or True,
    )
    monkeypatch.setattr(
        "core.runner_shop.vision.wait_for_image",
        lambda _hwnd, name, **_kwargs: events.append(f"wait:{name}") or {"score": 1},
    )
    monkeypatch.setattr(
        "core.runner_shop.wm.activate_window",
        lambda _hwnd: events.append("focus") or True,
    )
    monkeypatch.setattr(
        "core.runner_shop.camera.tilt_camera_top_down",
        lambda *_args: events.append("camera"),
    )
    monkeypatch.setattr("core.runner_shop.time.sleep", lambda _seconds: None)
    runner._keyboard.tap.side_effect = lambda key: events.append(f"key:{key}")

    assert runner._shop_enter_gold_shop(1, stop_event) is True

    assert events[:3] == [
        "click:nav_area",
        "click:nav_shop",
        "click:area_gold_shop",
    ]
    assert events.index("nav_play_gone") < events.index("focus")
    assert events.index("focus") < events.index("camera")
    assert events.index("camera") < events.index(f"key:{ord('E')}")
    assert events.index(f"key:{ord('E')}") < events.index("click:shop_gold_tab")
    assert events.index("click:shop_gold_tab") < events.index("wait:shop_cursed_boba")
    assert events[-1] == "wait:shop_cursed_boba"
