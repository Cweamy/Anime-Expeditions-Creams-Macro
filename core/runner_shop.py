"""Daily Gold Shop automation executed only from lobby-safe runner boundaries."""

import threading
import time

from . import auto_shop
from . import auto_shop_vision
from . import camera
from . import keys
from . import vision
from . import window as wm


SHOP_KEY = "gold_shop"
SHOP_NAV_TIMEOUT = 10.0
SHOP_LOAD_TIMEOUT = 60.0
SHOP_OPEN_TIMEOUT = 10.0
SHOP_MODAL_TIMEOUT = 4.0
SHOP_BUY_TIMEOUT = 2.0
SHOP_TERMINAL_TIMEOUT = 2.0
SHOP_MODAL_CLOSE_TIMEOUT = 5.0
SHOP_SCROLL_AMOUNT = -480
SHOP_SCROLL_REFINEMENT_AMOUNT = -120
SHOP_SCROLL_RESET_AMOUNT = 2400
SHOP_ITEM_SEARCH_TIMEOUT = 3.0
SHOP_BOTTOM_SCROLL_AMOUNT = -4800
SHOP_LIST_CENTER = (545, 410)
SHOP_LIST_SLOT_VIEWPORTS = {
    "left": (398, 218, 154, 362),
    "right": (552, 218, 154, 362),
}
SHOP_OUT_OF_STOCK_BANDS = {
    0: (218, 90),
    -120: (300, 110),
}
SHOP_OUT_OF_STOCK_DEFAULT_BAND = (218, 110)
SHOP_LIST_ACTION_VIEWPORT = (390, 218, 316, 362)
SHOP_SETTLE_DELAY = 1.2
SHOP_LIST_SETTLE_DELAY = 1.2
SHOP_MODAL_POST_CLOSE_DELAY = 0.8
SHOP_CAPTURE_INTERVAL = 0.12
SHOP_ITEM_SCROLL_STEPS = {
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
SHOP_ITEM_SCROLL_AMOUNTS = {
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
SHOP_ITEM_COLUMNS = {
    "cursed_boba": "left",
    "red_flower": "right",
    "frown_fruit": "left",
    "delicious_pie": "right",
    "mana_flask": "left",
    "trait_crystal": "right",
    "sprite_grey": "left",
    "equipment_reroll": "right",
    "equipment_lock": "left",
    "stat_reroll": "right",
    "stat_lock": "left",
}
SHOP_SWEEP_POSITIONS = (
    (0, ("cursed_boba", "red_flower")),
    (-120, ("frown_fruit", "delicious_pie")),
    (-480, ("mana_flask", "trait_crystal")),
    (-720, ("sprite_grey", "equipment_reroll")),
    (-960, ("equipment_lock", "stat_reroll")),
    (SHOP_BOTTOM_SCROLL_AMOUNT, ("stat_lock",)),
)

_TERMINAL_ITEM_STATUSES = {
    auto_shop.STATUS_COMPLETED,
    auto_shop.STATUS_OUT_OF_STOCK,
    auto_shop.STATUS_MAX_INVENTORY,
    auto_shop.STATUS_FAILED_TODAY,
}


class ShopOps:
    def _auto_shop_settings(self) -> dict:
        if self._get_auto_shop_settings is None:
            return {}
        try:
            return self._get_auto_shop_settings() or {}
        except Exception as exc:
            self._log(f"[Shop] Couldn't read Auto Shop settings: {exc}")
            return {}

    def _auto_shop_due_items(self, settings=None) -> list:
        settings = settings or self._auto_shop_settings()
        if not settings.get("enabled"):
            return []
        shop = (settings.get("shops") or {}).get(SHOP_KEY) or {}
        if not shop.get("enabled"):
            return []
        if (shop.get("state") or {}).get("status") == auto_shop.STATUS_FAILED_TODAY:
            return []
        return [
            item
            for item in (shop.get("items") or [])
            if item.get("enabled")
            and (item.get("state") or {}).get("status") not in _TERMINAL_ITEM_STATUSES
        ]

    def _auto_shop_wants_in(self) -> bool:
        """Return whether the Gold Shop has any actionable configured item."""
        return bool(self._auto_shop_due_items())

    def _shop_save_item_state(self, shop_key: str, item_key: str, state: dict) -> None:
        if self._save_auto_shop_item_state is None:
            return
        try:
            self._save_auto_shop_item_state(shop_key, item_key, state)
        except Exception as exc:
            self._log(f"[Shop] Couldn't save {item_key} state: {exc}")

    def _shop_save_shop_state(self, shop_key: str, state: dict) -> None:
        if self._save_auto_shop_shop_state is None:
            return
        try:
            self._save_auto_shop_shop_state(shop_key, state)
        except Exception as exc:
            self._log(f"[Shop] Couldn't save {shop_key} state: {exc}")

    @staticmethod
    def _shop_state_period(state: dict) -> str:
        return str((state or {}).get("period") or auto_shop.current_auto_shop_period())

    @staticmethod
    def _shop_region_is_visible(
            region: tuple, allow_top_clip: bool = False) -> bool:
        x, y, width, height = region
        view_x, view_y, view_width, view_height = SHOP_LIST_ACTION_VIEWPORT
        min_y = 180 if allow_top_clip else view_y
        return (
            x >= view_x
            and y >= min_y
            and x + width <= view_x + view_width
            and y + height <= view_y + view_height
        )

    def _shop_find_item(
            self, hwnd, item: dict, stop_event: threading.Event):
        template = auto_shop.item_definition(item["key"])["template"]
        target_step = SHOP_ITEM_SCROLL_STEPS[item["key"]]
        self._log(
            f'[Shop] Locating "{item["name"]}" at scroll step '
            f"{target_step}..."
        )
        x, y = vision.ref_to_screen(hwnd, *SHOP_LIST_CENTER)
        self._mouse.move_to(x, y)
        self._mouse.nudge()
        self._mouse.scroll(SHOP_SCROLL_RESET_AMOUNT)
        time.sleep(SHOP_SETTLE_DELAY)
        scroll_amount = SHOP_ITEM_SCROLL_AMOUNTS[item["key"]]
        if scroll_amount:
            if self._checkpoint(stop_event):
                return None
            self._mouse.scroll(scroll_amount)
            time.sleep(SHOP_SETTLE_DELAY)
        if self._checkpoint(stop_event):
            return None
        try:
            match = vision.wait_for_image(
                hwnd,
                template,
                timeout=SHOP_ITEM_SEARCH_TIMEOUT,
                stop_event=stop_event,
            )
        except vision.TemplateNotFound as exc:
            self._log(f"[Shop] {exc}")
            return None
        allow_top = (scroll_amount == 0)
        if match is not None:
            stock_region = auto_shop_vision.stock_status_region_from_item_match(match)
            buy_region = auto_shop_vision.initial_buy_region_from_item_match(match)
            if (
                    self._shop_region_is_visible(stock_region, allow_top_clip=allow_top)
                    and self._shop_region_is_visible(buy_region)):
                return match

            self._log(
                f'[Shop] "{item["name"]}" is visible but its controls are '
                "clipped; scrolling one more step..."
            )
            self._mouse.scroll(SHOP_SCROLL_REFINEMENT_AMOUNT)
            time.sleep(SHOP_SETTLE_DELAY)
            try:
                match = vision.wait_for_image(
                    hwnd,
                    template,
                    timeout=SHOP_ITEM_SEARCH_TIMEOUT,
                    stop_event=stop_event,
                )
            except vision.TemplateNotFound as exc:
                self._log(f"[Shop] {exc}")
                return None
            if match is not None:
                stock_region = auto_shop_vision.stock_status_region_from_item_match(match)
                buy_region = auto_shop_vision.initial_buy_region_from_item_match(match)
                if (
                        self._shop_region_is_visible(stock_region, allow_top_clip=allow_top)
                        and self._shop_region_is_visible(buy_region)):
                    return match
        self._log(
            f'[Shop] "{item["name"]}" was not found at its expected '
            f"scroll step ({target_step})."
        )
        return None

    def _shop_find_visible_item(
            self, hwnd, item: dict, stop_event: threading.Event):
        """Find one actionable card with 1-notch refinement if slightly off."""
        if self._checkpoint(stop_event):
            return None
        template = auto_shop.item_definition(item["key"])["template"]
        column = SHOP_ITEM_COLUMNS[item["key"]]
        scroll_amount = SHOP_ITEM_SCROLL_AMOUNTS.get(item["key"], 0)
        allow_top = (scroll_amount == 0)
        for attempt in range(2):
            if self._checkpoint(stop_event):
                return None
            try:
                match = vision.find_image(
                    hwnd,
                    template,
                    region=SHOP_LIST_SLOT_VIEWPORTS[column],
                )
            except vision.TemplateNotFound as exc:
                self._log(f"[Shop] {exc}")
                return None
            if match is not None:
                stock_region = auto_shop_vision.stock_status_region_from_item_match(match)
                buy_region = auto_shop_vision.initial_buy_region_from_item_match(match)
                if (
                        self._shop_region_is_visible(stock_region, allow_top_clip=allow_top)
                        and self._shop_region_is_visible(buy_region)):
                    return match
            if attempt == 0:
                time.sleep(0.3)

        # Refinement search: if scroll slightly undershot, try a 1-notch adjustment (-120px)
        if scroll_amount != 0 and scroll_amount != SHOP_BOTTOM_SCROLL_AMOUNT:
            self._mouse.scroll(SHOP_SCROLL_REFINEMENT_AMOUNT)
            time.sleep(0.4)
            try:
                match = vision.find_image(
                    hwnd,
                    template,
                    region=SHOP_LIST_SLOT_VIEWPORTS[column],
                )
            except vision.TemplateNotFound:
                match = None
            if match is not None:
                stock_region = auto_shop_vision.stock_status_region_from_item_match(match)
                buy_region = auto_shop_vision.initial_buy_region_from_item_match(match)
                if (
                        self._shop_region_is_visible(stock_region, allow_top_clip=allow_top)
                        and self._shop_region_is_visible(buy_region)):
                    return match
        return None

    def _shop_find_slot_out_of_stock(
            self, hwnd, item: dict, scroll_amount: int,
            stop_event: threading.Event) -> bool:
        """Check the sold-out label inside one calibrated row and column."""
        if self._checkpoint(stop_event):
            return False
        column = SHOP_ITEM_COLUMNS[item["key"]]
        x, _y, width, _height = SHOP_LIST_SLOT_VIEWPORTS[column]
        y, height = SHOP_OUT_OF_STOCK_BANDS.get(
            scroll_amount,
            SHOP_OUT_OF_STOCK_DEFAULT_BAND,
        )
        try:
            match = vision.find_image(
                hwnd,
                auto_shop.AUTO_SHOP_UI_TEMPLATES["out_of_stock"],
                region=(x, y, width, height),
            )
        except vision.TemplateNotFound as exc:
            self._log(f"[Shop] {exc}")
            return False
        return match is not None

    def _shop_retry_item_state(self, item: dict) -> dict:
        state = item.get("state") or {}
        period = self._shop_state_period(state)
        retry = auto_shop.normalize_item_state(state, period)
        if retry["status"] != auto_shop.STATUS_PENDING_VERIFICATION:
            # Increment attempts to avoid infinite retry loop
            retry["attempts"] = min(
                auto_shop.AUTO_SHOP_MAX_ITEM_ATTEMPTS,
                retry["attempts"] + 1,
            )
            if retry["attempts"] >= auto_shop.AUTO_SHOP_MAX_ITEM_ATTEMPTS:
                retry["status"] = auto_shop.STATUS_FAILED_TODAY
            else:
                retry["status"] = auto_shop.STATUS_RETRY_PENDING
            retry["verification"] = None
        return retry

    def _shop_out_of_stock_state(self, item: dict) -> dict:
        state = item.get("state") or {}
        period = self._shop_state_period(state)
        completed = auto_shop.normalize_item_state(state, period)
        completed["status"] = auto_shop.STATUS_OUT_OF_STOCK
        completed["attempts"] = 0
        completed["verification"] = None
        return completed

    def _shop_move_to_scroll_position(
            self, hwnd, scroll_amount: int,
            stop_event: threading.Event) -> bool:
        """Reset to Top, then apply one calibrated absolute scroll delta."""
        if self._checkpoint(stop_event):
            return False
        x, y = vision.ref_to_screen(hwnd, *SHOP_LIST_CENTER)
        self._mouse.move_to(x, y)
        self._mouse.nudge()
        self._mouse.scroll(SHOP_SCROLL_RESET_AMOUNT)
        time.sleep(SHOP_LIST_SETTLE_DELAY)
        if scroll_amount:
            if self._checkpoint(stop_event):
                return False
            self._mouse.scroll(scroll_amount)
            time.sleep(SHOP_LIST_SETTLE_DELAY)
        return not self._checkpoint(stop_event)

    def _shop_run_no_ocr_sweep(
            self, hwnd, shop_key: str, items: list,
            stop_event: threading.Event) -> None:
        """Process each enabled row from its calibrated absolute position."""
        due_by_key = {item["key"]: item for item in items}
        for scroll_amount, row_keys in SHOP_SWEEP_POSITIONS:
            row_items = [
                due_by_key[item_key]
                for item_key in row_keys
                if item_key in due_by_key
            ]
            if not row_items:
                continue
            names = ", ".join(item["name"] for item in row_items)
            self._log(
                f"[Shop] Positioning {names}: reset Top, "
                f"then scroll {scroll_amount}."
            )
            if not self._shop_move_to_scroll_position(
                    hwnd, scroll_amount, stop_event):
                return

            for item in row_items:
                if self._checkpoint(stop_event):
                    return
                self._set_status(action=f'Checking {item["name"]}...')
                if self._shop_find_slot_out_of_stock(
                        hwnd, item, scroll_amount, stop_event):
                    self._shop_save_item_state(
                        shop_key,
                        item["key"],
                        self._shop_out_of_stock_state(item),
                    )
                    self._log(f'[Shop] "{item["name"]}" is out of stock.')
                    continue
                match = self._shop_find_visible_item(hwnd, item, stop_event)
                cancel_match = None
                if match is None:
                    synthetic = self._shop_synthetic_item_match(item, scroll_amount)
                    cancel_match = self._shop_try_fallback_modal(hwnd, item, synthetic, stop_event)
                    if cancel_match is not None:
                        match = synthetic

                if match is None:
                    self._shop_save_item_state(
                        shop_key,
                        item["key"],
                        self._shop_retry_item_state(item),
                    )
                    self._log(
                        f'[Shop] "{item["name"]}" was not found at its '
                        f"calibrated scroll position ({scroll_amount}); "
                        "it will retry on the next Auto Shop pass."
                    )
                    continue
                if cancel_match is not None:
                    self._shop_process_visible_item(
                        hwnd,
                        shop_key,
                        item,
                        match,
                        stop_event,
                        cancel_match=cancel_match,
                    )
                else:
                    self._shop_process_visible_item(
                        hwnd,
                        shop_key,
                        item,
                        match,
                        stop_event,
                    )

    def _shop_synthetic_item_match(self, item: dict, scroll_amount: int) -> dict:
        """Construct synthetic item match coordinates from calibrated slot position."""
        column = SHOP_ITEM_COLUMNS[item["key"]]
        center_x = 475 if column == "left" else 629
        center_y = 295 if scroll_amount == 0 else 430
        return {
            "x": center_x - 30,
            "y": center_y - 27,
            "w": 61,
            "h": 55,
            "cx": center_x,
            "cy": center_y,
        }

    def _shop_try_fallback_modal(
            self, hwnd, item: dict, synthetic_match: dict,
            stop_event: threading.Event):
        """Attempt clicking calibrated buy button if template match failed, returning cancel_match if modal opens."""
        if self._checkpoint(stop_event):
            return None
        region = auto_shop_vision.initial_buy_region_from_item_match(synthetic_match)
        try:
            buy_crop = vision.capture_game_bgr(hwnd, region)
        except Exception:
            buy_crop = None
        if buy_crop is None or not buy_crop.size or not auto_shop_vision.buy_button_is_enabled(buy_crop):
            return None
        x, y, width, height = region
        buy_match = {
            "x": x,
            "y": y,
            "w": width,
            "h": height,
            "cx": x + width // 2,
            "cy": y + height // 2,
        }
        vision.click_match(self._mouse, hwnd, buy_match)
        try:
            cancel_match = vision.wait_for_image(
                hwnd,
                auto_shop.AUTO_SHOP_UI_TEMPLATES["modal_cancel"],
                timeout=SHOP_MODAL_TIMEOUT,
                stop_event=stop_event,
            )
        except Exception:
            cancel_match = None
        if cancel_match is not None:
            self._log(f'[Shop] Fallback modal opened for "{item["name"]}"!')
            return cancel_match
        return None

    def _shop_read_observation(
            self, hwnd, item: dict, item_match: dict,
            stop_event: threading.Event) -> dict:
        status_region = auto_shop_vision.stock_status_region_from_item_match(item_match)
        try:
            out_of_stock = vision.find_image(
                hwnd,
                auto_shop.AUTO_SHOP_UI_TEMPLATES["out_of_stock"],
                region=status_region,
            ) is not None
        except vision.TemplateNotFound:
            out_of_stock = False

        stock_region = auto_shop_vision.stock_region_from_item_match(item_match)
        crops = []
        for index in range(3):
            if self._checkpoint(stop_event):
                break
            crop = vision.capture_game_bgr(hwnd, stock_region)
            if crop is not None and crop.size:
                crops.append(crop)
            if index < 2:
                time.sleep(SHOP_CAPTURE_INTERVAL)

        signature = (
            auto_shop.build_stock_signature(crops[-1])
            if crops else ""
        )
        if out_of_stock:
            return {
                "left": 0,
                "signature": signature,
                "out_of_stock": True,
            }
        daily_maximum = int(item["daily_maximum"])
        left = (
            auto_shop_vision.read_left_consensus(crops, daily_maximum)
            if len(crops) == 3 else None
        )
        return {
            "left": left,
            "signature": signature,
            "out_of_stock": False,
        }

    def _shop_find_terminal_label(
            self, hwnd, item_match: dict, stop_event: threading.Event,
            wait: bool = False):
        region = auto_shop_vision.card_terminal_region_from_item_match(
            item_match
        )
        names = (
            auto_shop.AUTO_SHOP_UI_TEMPLATES["out_of_stock"],
            auto_shop.AUTO_SHOP_UI_TEMPLATES["max_inventory"],
        )
        try:
            if wait:
                match, name = vision.wait_for_image_any(
                    hwnd,
                    names,
                    region=region,
                    timeout=SHOP_TERMINAL_TIMEOUT,
                    stop_event=stop_event,
                )
            else:
                match, name = vision.find_image_any(
                    hwnd,
                    names,
                    region=region,
                )
        except vision.TemplateNotFound as exc:
            self._log(f"[Shop] {exc}")
            return None
        return name if match is not None else None

    def _shop_max_inventory_state(
            self, state: dict, period: str) -> dict:
        completed = auto_shop.normalize_item_state(state, period)
        completed["status"] = auto_shop.STATUS_MAX_INVENTORY
        completed["verification"] = None
        return completed

    def _shop_open_purchase_modal(
            self, hwnd, item_match: dict, stop_event: threading.Event):
        region = auto_shop_vision.initial_buy_region_from_item_match(item_match)
        buy_crop = vision.capture_game_bgr(hwnd, region)
        if not auto_shop_vision.buy_button_is_enabled(buy_crop):
            if stop_event.is_set():
                return None
            self._log(
                "[Shop] The visible Buy region is not enabled; "
                "no purchase was attempted."
            )
            return None
        x, y, width, height = region
        buy_match = {
            "x": x,
            "y": y,
            "w": width,
            "h": height,
            "cx": x + width // 2,
            "cy": y + height // 2,
        }
        vision.click_match(self._mouse, hwnd, buy_match)
        try:
            cancel_match = vision.wait_for_image(
                hwnd,
                auto_shop.AUTO_SHOP_UI_TEMPLATES["cancel"],
                timeout=SHOP_MODAL_TIMEOUT,
                stop_event=stop_event,
            )
        except vision.TemplateNotFound as exc:
            self._log(f"[Shop] {exc}")
            return None
        if cancel_match is None and not self._checkpoint(stop_event):
            self._log(
                "[Shop] Buy was clicked, but the purchase modal did not open; "
                "the button may be disabled by insufficient Gold."
            )
        return cancel_match

    def _shop_configure_amount(
            self, hwnd, cancel_match: dict, target, amount: int,
            stop_event: threading.Event) -> bool:
        if str(target).lower() == "max":
            region = auto_shop_vision.amount_toggle_region_from_cancel(cancel_match)
            # Check if Max is already selected before clicking the toggle.
            # The game displays "Min" when Max is active and vice versa.
            try:
                max_match = vision.find_image(
                    hwnd,
                    auto_shop.AUTO_SHOP_UI_TEMPLATES["amount_max"],
                    region=region,
                )
            except vision.TemplateNotFound:
                max_match = None
            try:
                min_match = vision.find_image(
                    hwnd,
                    auto_shop.AUTO_SHOP_UI_TEMPLATES["amount_min"],
                    region=region,
                )
            except vision.TemplateNotFound:
                min_match = None
            action = auto_shop.max_toggle_action(
                max_visible=max_match is not None,
                min_visible=min_match is not None,
            )
            if action == auto_shop.MAX_TOGGLE_ALREADY_SELECTED:
                return not self._checkpoint(stop_event)
            x, y, width, height = region
            screen_x, screen_y = vision.ref_to_screen(
                hwnd,
                x + width // 2,
                y + height // 2,
            )
            self._mouse.click(screen_x, screen_y)
            return not self._checkpoint(stop_event)

        region = auto_shop_vision.amount_input_region_from_cancel(cancel_match)
        x, y, width, height = region
        screen_x, screen_y = vision.ref_to_screen(
            hwnd,
            x + width // 2,
            y + height // 2,
        )
        self._mouse.double_click(screen_x, screen_y)
        time.sleep(0.15)
        self._keyboard.combo(keys.VK_CONTROL, ord("A"))
        self._keyboard.tap(keys.VK_DELETE)
        self._keyboard.type_text(str(int(amount)))
        time.sleep(SHOP_SETTLE_DELAY)
        return not self._checkpoint(stop_event)

    def _shop_cancel_modal(self, hwnd, cancel_match: dict) -> None:
        x, y = auto_shop_vision.cancel_click_point(cancel_match)
        screen_x, screen_y = vision.ref_to_screen(hwnd, x, y)
        self._mouse.click(screen_x, screen_y)

    def _shop_confirm_purchase(
            self, hwnd, cancel_match: dict, stop_event: threading.Event) -> bool:
        region = auto_shop_vision.final_buy_region_from_cancel(cancel_match)
        x, y, width, height = region
        screen_x, screen_y = vision.ref_to_screen(
            hwnd,
            x + width // 2,
            y + height // 2,
        )
        self._mouse.click(screen_x, screen_y)
        if self._checkpoint(stop_event):
            return False
        if self._wait_for_image_gone(
                hwnd,
                (auto_shop.AUTO_SHOP_UI_TEMPLATES["cancel"],),
                SHOP_MODAL_CLOSE_TIMEOUT,
                stop_event,
        ):
            time.sleep(SHOP_MODAL_POST_CLOSE_DELAY)
            return True
        self._shop_cancel_modal(hwnd, cancel_match)
        return False

    @staticmethod
    def _shop_observation_changed(before: dict, after: dict):
        before_signature = str(before.get("signature") or "")
        after_signature = str(after.get("signature") or "")
        if not before_signature or not after_signature:
            return None
        try:
            return auto_shop_vision.stock_visual_changed(
                before_signature,
                after_signature,
            )
        except ValueError:
            return None

    def _shop_state_from_observation(
            self, item: dict, state: dict, observation: dict) -> dict:
        current = auto_shop.normalize_item_state(
            state,
            self._shop_state_period(state),
        )
        current["last_known_left"] = observation.get("left")
        current["stock_signature"] = str(observation.get("signature") or "")
        current["verification"] = None
        if observation.get("out_of_stock"):
            current["status"] = auto_shop.STATUS_OUT_OF_STOCK
        else:
            plan = auto_shop.calculate_purchase_plan(
                item["key"],
                item["target"],
                int(observation["left"]),
            )
            current["status"] = plan["status"]
        return current

    def _shop_verify_pending(
            self, item: dict, state: dict, observation: dict):
        verification = state.get("verification") or {}
        if observation.get("out_of_stock"):
            return self._shop_state_from_observation(item, state, observation), True
        if observation.get("left") is None:
            return state, False
        before = {
            "left": verification.get("before_left"),
            "signature": verification.get("before_signature"),
        }
        result = auto_shop.classify_stock_verification(
            before.get("left"),
            observation.get("left"),
            self._shop_observation_changed(before, observation),
        )
        if result == auto_shop.VERIFICATION_PROGRESS:
            return self._shop_state_from_observation(item, state, observation), True
        if result == auto_shop.VERIFICATION_UNCHANGED:
            ready = auto_shop.normalize_item_state(
                state,
                self._shop_state_period(state),
            )
            ready["status"] = auto_shop.STATUS_PENDING
            ready["verification"] = None
            ready["last_known_left"] = observation["left"]
            ready["stock_signature"] = observation.get("signature") or ""
            return ready, True
        return state, False

    def _shop_process_visible_item(
            self, hwnd, shop_key: str, item: dict, item_match: dict,
            stop_event: threading.Event, cancel_match: dict = None) -> None:
        """Buy one already-visible card without reading its remaining stock."""
        item_key = item["key"]
        period = self._shop_state_period(item.get("state") or {})
        state = auto_shop.normalize_item_state(item.get("state"), period)

        if state["status"] == auto_shop.STATUS_PENDING_VERIFICATION:
            self._log(
                f'[Shop] "{item["name"]}" has an uncertain earlier purchase; '
                "skipping it safely."
            )
            self._shop_save_item_state(shop_key, item_key, state)
            return

        terminal_label = self._shop_find_terminal_label(
            hwnd,
            item_match,
            stop_event,
        )
        if terminal_label == auto_shop.AUTO_SHOP_UI_TEMPLATES["max_inventory"]:
            self._log(f'[Shop] "{item["name"]}" has Max Inventory.')
            self._shop_save_item_state(
                shop_key,
                item_key,
                self._shop_max_inventory_state(state, period),
            )
            return
        if terminal_label == auto_shop.AUTO_SHOP_UI_TEMPLATES["out_of_stock"]:
            completed = auto_shop.normalize_item_state(state, period)
            completed["status"] = auto_shop.STATUS_OUT_OF_STOCK
            completed["verification"] = None
            self._log(f'[Shop] "{item["name"]}" is out of stock.')
            self._shop_save_item_state(shop_key, item_key, completed)
            return

        target = item["target"]
        amount = int(item["daily_maximum"])
        if str(target).lower() != "max":
            amount = int(target)
        self._set_status(action=f'Opening {item["name"]} purchase...')
        self._log(
            f'[Shop] Opening purchase for "{item["name"]}": '
            f"{amount} requested."
        )
        if cancel_match is None:
            cancel_match = self._shop_open_purchase_modal(
                hwnd,
                item_match,
                stop_event,
            )
        if cancel_match is None:
            if not stop_event.is_set():
                self._shop_save_item_state(
                    shop_key,
                    item_key,
                    self._shop_retry_item_state(item),
                )
                self._log(
                    f'[Shop] "{item["name"]}" Buy is unavailable; '
                    "leaving it for a later pass."
                )
            return
        if not self._shop_configure_amount(
                hwnd,
                cancel_match,
                target,
                amount,
                stop_event,
        ):
            self._shop_cancel_modal(hwnd, cancel_match)
            return
        if not self._shop_confirm_purchase(hwnd, cancel_match, stop_event):
            if stop_event.is_set():
                return
            uncertain = auto_shop.normalize_item_state(state, period)
            uncertain["status"] = auto_shop.STATUS_FAILED_TODAY
            uncertain["verification"] = {"reason": "modal_did_not_close"}
            self._shop_save_item_state(shop_key, item_key, uncertain)
            self._log(
                f'[Shop] "{item["name"]}" purchase could not be confirmed; '
                "use Reset Today before retrying it."
            )
            return

        updated = auto_shop.normalize_item_state(state, period)
        updated["attempts"] = 0
        updated["verification"] = None
        if str(target).lower() == "max":
            updated["status"] = auto_shop.STATUS_COMPLETED
            message = f'[Shop] "{item["name"]}" purchase executed today.'
        else:
            updated["status"] = auto_shop.STATUS_RETRY_PENDING
            message = (
                f'[Shop] "{item["name"]}" purchase executed; it remains '
                "scheduled until stock is exhausted."
            )
        self._shop_save_item_state(shop_key, item_key, updated)
        self._log(message)

    def _shop_process_item(
            self, hwnd, shop_key: str, item: dict,
            stop_event: threading.Event) -> None:
        item_key = item["key"]
        period = self._shop_state_period(item.get("state") or {})
        state = auto_shop.normalize_item_state(item.get("state"), period)
        match = self._shop_find_item(hwnd, item, stop_event)
        if match is None:
            self._shop_save_item_state(
                shop_key,
                item_key,
                auto_shop.record_item_failure(state, period),
            )
            return

        terminal_label = self._shop_find_terminal_label(
            hwnd,
            match,
            stop_event,
        )
        if terminal_label == auto_shop.AUTO_SHOP_UI_TEMPLATES["max_inventory"]:
            self._log(
                f'[Shop] "{item["name"]}" has Max Inventory; '
                "skipping stock OCR."
            )
            self._shop_save_item_state(
                shop_key,
                item_key,
                self._shop_max_inventory_state(state, period),
            )
            return
        if terminal_label == auto_shop.AUTO_SHOP_UI_TEMPLATES["out_of_stock"]:
            observation = {
                "left": 0,
                "signature": "",
                "out_of_stock": True,
            }
        else:
            self._set_status(action=f'Reading {item["name"]} stock...')
            self._log(f'[Shop] Reading stock for "{item["name"]}"...')
            observation = self._shop_read_observation(
                hwnd,
                item,
                match,
                stop_event,
            )
        if observation.get("out_of_stock"):
            self._log(f'[Shop] "{item["name"]}" is out of stock.')
        elif observation.get("left") is not None:
            self._log(
                f'[Shop] "{item["name"]}" stock confirmed: '
                f'{observation["left"]} left.'
            )
        else:
            self._log(
                f'[Shop] OCR could not confirm "{item["name"]}" stock.'
            )
        if state["status"] == auto_shop.STATUS_PENDING_VERIFICATION:
            self._set_status(action=f'Verifying {item["name"]}...')
            self._log(
                f'[Shop] Verifying the previous "{item["name"]}" purchase...'
            )
            state, resolved = self._shop_verify_pending(item, state, observation)
            self._shop_save_item_state(shop_key, item_key, state)
            if not resolved or state["status"] != auto_shop.STATUS_PENDING:
                return

        if observation.get("out_of_stock"):
            self._shop_save_item_state(
                shop_key,
                item_key,
                self._shop_state_from_observation(item, state, observation),
            )
            return
        if observation.get("left") is None:
            self._shop_save_item_state(
                shop_key,
                item_key,
                auto_shop.record_item_failure(state, period),
            )
            return

        plan = auto_shop.calculate_purchase_plan(
            item_key,
            item["target"],
            int(observation["left"]),
        )
        if plan["status"] != auto_shop.STATUS_PENDING:
            self._shop_save_item_state(
                shop_key,
                item_key,
                self._shop_state_from_observation(item, state, observation),
            )
            return

        self._set_status(action=f'Opening {item["name"]} purchase...')
        self._log(
            f'[Shop] Opening purchase for "{item["name"]}": '
            f'{plan["pending_amount"]} requested.'
        )
        cancel_match = self._shop_open_purchase_modal(
            hwnd,
            match,
            stop_event,
        )
        if cancel_match is None:
            if stop_event.is_set():
                return
            self._log(
                f'[Shop] "{item["name"]}" purchase was not started safely.'
            )
            self._shop_save_item_state(
                shop_key,
                item_key,
                auto_shop.record_item_failure(state, period),
            )
            return
        if not self._shop_configure_amount(
                hwnd,
                cancel_match,
                item["target"],
                plan["pending_amount"],
                stop_event,
        ):
            self._shop_cancel_modal(hwnd, cancel_match)
            if stop_event.is_set():
                return
            self._shop_save_item_state(
                shop_key,
                item_key,
                auto_shop.record_item_failure(state, period),
            )
            return

        pending = auto_shop.normalize_item_state(state, period)
        pending["status"] = auto_shop.STATUS_PENDING_VERIFICATION
        pending["last_known_left"] = observation["left"]
        pending["stock_signature"] = observation.get("signature") or ""
        pending["verification"] = {
            "before_left": observation["left"],
            "before_signature": observation.get("signature") or "",
        }
        self._shop_save_item_state(shop_key, item_key, pending)

        if not self._shop_confirm_purchase(hwnd, cancel_match, stop_event):
            if not stop_event.is_set():
                self._shop_save_item_state(
                    shop_key,
                    item_key,
                    auto_shop.record_item_failure(pending, period),
                )
            return

        self._set_status(action=f'Verifying {item["name"]} purchase...')
        self._log(
            f'[Shop] Purchase submitted for "{item["name"]}"; '
            "re-reading stock..."
        )
        terminal_label = self._shop_find_terminal_label(
            hwnd,
            match,
            stop_event,
            wait=True,
        )
        if terminal_label == auto_shop.AUTO_SHOP_UI_TEMPLATES["max_inventory"]:
            self._log(
                f'[Shop] "{item["name"]}" reached Max Inventory '
                "after purchase."
            )
            self._shop_save_item_state(
                shop_key,
                item_key,
                self._shop_max_inventory_state(pending, period),
            )
            return
        if terminal_label == auto_shop.AUTO_SHOP_UI_TEMPLATES["out_of_stock"]:
            after = {
                "left": 0,
                "signature": "",
                "out_of_stock": True,
            }
        else:
            after = self._shop_read_observation(
                hwnd,
                item,
                match,
                stop_event,
            )
        if after.get("out_of_stock"):
            self._log(
                f'[Shop] "{item["name"]}" is out of stock after purchase.'
            )
        elif after.get("left") is not None:
            self._log(
                f'[Shop] "{item["name"]}" stock after purchase: '
                f'{after["left"]} left.'
            )
        else:
            self._log(
                f'[Shop] "{item["name"]}" stock could not be confirmed '
                "after purchase; verification remains pending."
            )
        result = auto_shop.classify_stock_verification(
            observation["left"],
            after.get("left"),
            self._shop_observation_changed(observation, after),
            out_of_stock=bool(after.get("out_of_stock")),
        )
        if result in (
                auto_shop.VERIFICATION_PROGRESS,
                auto_shop.VERIFICATION_OUT_OF_STOCK):
            final_state = self._shop_state_from_observation(item, pending, after)
            self._shop_save_item_state(shop_key, item_key, final_state)
        elif result == auto_shop.VERIFICATION_UNCHANGED:
            self._shop_save_item_state(
                shop_key,
                item_key,
                auto_shop.record_item_failure(pending, period),
            )

    def _shop_enter_gold_shop(
            self, hwnd, stop_event: threading.Event) -> bool:
        for name, action in (
            ("nav_area", "Opening Areas..."),
            (auto_shop.AUTO_SHOP_UI_TEMPLATES["navigation"], "Opening Shop..."),
            (auto_shop.AUTO_SHOP_UI_TEMPLATES["destination"], "Entering Gold Shop..."),
        ):
            self._set_status(action=action)
            if self._click_found_image(
                    hwnd,
                    name,
                    SHOP_NAV_TIMEOUT,
                    stop_event,
            ) is None:
                return False
            if self._checkpoint(stop_event):
                return False
            if name != auto_shop.AUTO_SHOP_UI_TEMPLATES["destination"]:
                time.sleep(0.3)

        # Check for teleport fade-out immediately after clicking the destination
        self._wait_for_image_gone(
            hwnd,
            ("nav_play",),
            SHOP_NAV_TIMEOUT,
            stop_event,
        )
        self._set_status(action="Loading Gold Shop...")
        try:
            loaded = vision.wait_for_image(
                hwnd,
                "nav_play",
                timeout=SHOP_LOAD_TIMEOUT,
                stop_event=stop_event,
            )
        except vision.TemplateNotFound:
            loaded = None
        if loaded is None:
            return False
        time.sleep(1.5)
        if not wm.activate_window(hwnd):
            self._log("[Shop] Couldn't confirm Roblox focus before opening Gold Shop.")
            return False
        time.sleep(0.3)
        camera.tilt_camera_top_down(self._mouse, hwnd)
        time.sleep(0.5)

        # Retry pressing E and locating shop_tab up to 3 times
        tab_match = None
        for attempt in range(3):
            if self._checkpoint(stop_event):
                return False
            self._set_status(action="Selecting Gold Shop...")
            self._keyboard.tap(ord("E"))
            tab_match = self._click_found_image(
                hwnd,
                auto_shop.AUTO_SHOP_UI_TEMPLATES["shop_tab"],
                3.0,
                stop_event,
            )
            if tab_match is not None:
                break
            time.sleep(0.5)

        if tab_match is None:
            self._log("[Shop] Couldn't find the Gold Shop tab after pressing E.")
            return False
        if self._checkpoint(stop_event):
            return False
        time.sleep(SHOP_SETTLE_DELAY)
        try:
            opened = vision.wait_for_image(
                hwnd,
                auto_shop.item_definition("cursed_boba")["template"],
                timeout=SHOP_OPEN_TIMEOUT,
                stop_event=stop_event,
            )
        except vision.TemplateNotFound:
            opened = None
        return opened is not None

    def _run_auto_shop(
            self, hwnd, stop_event: threading.Event) -> None:
        settings = self._auto_shop_settings()
        due_items = self._auto_shop_due_items(settings)
        if not due_items:
            return
        shop = settings["shops"][SHOP_KEY]
        period = self._shop_state_period(shop.get("state") or {})
        self._log(f"[Shop] Starting Auto Shop for {len(due_items)} item(s).")
        self._set_status(
            current_task="Auto Shop",
            action="Preparing Gold Shop...",
            mode="shop",
            map="-",
            stage="-",
            difficulty="-",
            macro="-",
            play_mode="-",
        )
        wm.show_window(hwnd)
        if not wm.activate_window(hwnd):
            self._log("[Shop] Couldn't confirm Roblox took focus.")
            return
        time.sleep(0.5)
        if not self._ensure_lobby(hwnd, stop_event):
            return
        if not self._shop_enter_gold_shop(hwnd, stop_event):
            failed = auto_shop.record_navigation_failure(
                shop.get("state"),
                period,
            )
            self._shop_save_shop_state(SHOP_KEY, failed)
            if not stop_event.is_set():
                self._recover_to_lobby(hwnd, stop_event)
            return

        self._shop_save_shop_state(
            SHOP_KEY,
            auto_shop.fresh_shop_state(period),
        )
        self._shop_run_no_ocr_sweep(
            hwnd,
            SHOP_KEY,
            due_items,
            stop_event,
        )

        if not stop_event.is_set():
            try:
                close_match = vision.find_image(hwnd, "nav_closeui")
            except vision.TemplateNotFound:
                close_match = None
            if close_match is not None:
                vision.click_match(self._mouse, hwnd, close_match)
                time.sleep(SHOP_SETTLE_DELAY)
            self._log("[Shop] Auto Shop pass finished. Returning to the lobby.")
            self._recover_to_lobby(hwnd, stop_event)

    def _run_auto_shop_if_due(
            self, hwnd, stop_event: threading.Event) -> None:
        if self._auto_shop_wants_in():
            self._run_auto_shop(hwnd, stop_event)
