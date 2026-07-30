"""Clock-driven Auto Fuel passes for Resource Drill and Gold Mine.

This mixin follows the same lobby-safe interleave pattern as Auto Crafting:
the main runner decides when a pass is due, leaves the current stage cleanly,
runs the pass from the lobby, and then resumes the interrupted task.
"""
import threading
import time

from . import keys
from . import paths as walk_paths
from . import vision
from . import window as wm
from .runner_constants import *  # noqa: F401,F403 -- shared runner constants


class FuelOps:
    def _fuel_due_resources(self) -> list:
        if self._get_fuel_settings is None:
            return []
        try:
            settings = self._get_fuel_settings()
        except Exception:
            return []
        if not settings.get("enabled"):
            return []
        return [
            key for key in FUEL_RESOURCES
            if (settings.get("resources", {}).get(key) or {}).get("due")
        ]

    def _fuel_wants_in(self) -> bool:
        """Return whether at least one enabled resource is due right now."""
        return bool(self._fuel_due_resources())

    def _fuel_mark_result(self, resource: str, succeeded: bool) -> None:
        if self._mark_fuel_refill_result is None:
            return
        try:
            self._mark_fuel_refill_result(resource, succeeded)
        except Exception as exc:
            self._log(f"[Fuel] Couldn't save the {resource} timer result: {exc}")

    def _fuel_mark_failed(self, resources: list, stop_event: threading.Event) -> None:
        if stop_event.is_set():
            return
        for resource in dict.fromkeys(resources):
            self._fuel_mark_result(resource, False)

    def _fuel_wait_for(self, hwnd, name: str, timeout: float, stop_event):
        try:
            return vision.wait_for_image(
                hwnd, name, timeout=timeout, stop_event=stop_event)
        except vision.TemplateNotFound as exc:
            self._log(f"[Fuel] {exc}")
            return None

    def _fuel_run_path(self, path_name: str, stop_event: threading.Event) -> bool:
        if not path_name:
            self._log("[Fuel] Required walk path is not configured.")
            return False
        data = walk_paths.load_path(path_name)
        events = data.get("events") or []
        if not events:
            self._log(f'[Fuel] Walk path "{path_name}" is empty or missing.')
            return False
        self._log(f'[Fuel] Walking path "{path_name}"...')
        self._set_status(action=f'Walking "{path_name}"...')
        walk_paths.replay_events(events, self._keyboard, stop_event)
        if stop_event.is_set():
            return False
        self._log(f'[Fuel] Walk path "{path_name}" finished.')
        self._set_status(action="Opening the fuel station...")
        self._keyboard.tap(ord("E"))
        if self._checkpoint(stop_event):
            return False
        time.sleep(SETTLE_DELAY)
        return True

    def _fuel_refill_station(
            self, hwnd, stop_event: threading.Event, resource: str, amount) -> bool:
        label = "Resource Drill" if resource == "resource_drill" else "Gold Mine"
        self._set_status(action=f"Refilling {label}...")

        add_match = self._fuel_wait_for(
            hwnd, "fuel_add", FUEL_ACTION_TIMEOUT, stop_event)
        if add_match is None:
            self._log(f"[Fuel] Add Fuel was not found at {label}.")
            return False
        vision.click_match(self._mouse, hwnd, add_match)
        if self._checkpoint(stop_event):
            return False
        time.sleep(FUEL_CLICK_DELAY)

        if str(amount).lower() == "max":
            max_match = self._fuel_wait_for(
                hwnd, "fuel_max", FUEL_ACTION_TIMEOUT, stop_event)
            if max_match is None:
                self._log(f"[Fuel] Max was not found for {label}.")
                return False
            vision.click_match(self._mouse, hwnd, max_match)
        else:
            input_match = self._fuel_wait_for(
                hwnd, "fuel_input", FUEL_ACTION_TIMEOUT, stop_event)
            if input_match is None:
                self._log(f"[Fuel] Fuel quantity input was not found for {label}.")
                return False
            vision.double_click_match(self._mouse, hwnd, input_match)
            time.sleep(0.15)
            self._keyboard.combo(keys.VK_CONTROL, ord("A"))
            self._keyboard.tap(keys.VK_DELETE)
            self._keyboard.type_text(str(int(amount)))
        if self._checkpoint(stop_event):
            return False
        time.sleep(FUEL_CLICK_DELAY)

        confirm_match = self._fuel_wait_for(
            hwnd, "fuel_confirm", FUEL_ACTION_TIMEOUT, stop_event)
        if confirm_match is None:
            self._log(f"[Fuel] Confirm was not found for {label}.")
            return False
        vision.click_match(self._mouse, hwnd, confirm_match)
        if self._checkpoint(stop_event):
            return False
        time.sleep(FUEL_CLICK_DELAY)

        confirmed = self._wait_for_image_gone(
            hwnd, ("fuel_confirm",), FUEL_CONFIRM_TIMEOUT, stop_event)
        if not confirmed:
            self._log(f"[Fuel] {label} confirmation did not close.")
            return False

        # The refill is already confirmed at this point. Closing the remaining
        # station HUD is visual cleanup only and must never turn a success into
        # a retry if the optional close button is not detected.
        self._set_status(action=f"Closing {label} station...")
        close_match = self._fuel_wait_for(
            hwnd, "nav_closeui", FUEL_CLOSE_TIMEOUT, stop_event)
        if close_match is not None:
            vision.click_match(self._mouse, hwnd, close_match)
            time.sleep(SETTLE_DELAY)
            self._log(f"[Fuel] {label} station HUD closed.")
        self._log(f"[Fuel] {label} refilled successfully.")
        return True

    def _fuel_enter_hub(self, hwnd, stop_event: threading.Event) -> bool:
        self._set_status(action="Opening the Area menu...")
        if self._click_found_image(
                hwnd, "nav_area", FUEL_AREA_TIMEOUT, stop_event) is None:
            self._log("[Fuel] Couldn't open the Area menu.")
            return False
        if self._checkpoint(stop_event):
            return False
        time.sleep(SETTLE_DELAY)

        self._set_status(action="Opening Expeditions...")
        if self._click_found_image(
                hwnd, "nav_expedition", FUEL_AREA_TIMEOUT, stop_event) is None:
            self._log("[Fuel] Couldn't find the Expedition option.")
            return False
        if self._checkpoint(stop_event):
            return False
        time.sleep(SETTLE_DELAY)

        self._set_status(action="Entering Expedition Hub...")
        if self._click_found_image(
                hwnd, "expedition_hub", FUEL_AREA_TIMEOUT, stop_event) is None:
            self._log("[Fuel] Couldn't find Expedition Hub.")
            return False
        if self._checkpoint(stop_event):
            return False

        time.sleep(SETTLE_DELAY)
        self._set_status(action="Loading Expedition Hub...")
        loaded = self._fuel_wait_for(
            hwnd, "nav_play", FUEL_LOAD_TIMEOUT, stop_event)
        if loaded is None:
            self._log("[Fuel] Expedition Hub did not finish loading.")
            return False
        time.sleep(FUEL_LOAD_SETTLE)
        return not self._checkpoint(stop_event)

    def _run_fuel_refill(
            self, hwnd, stop_event: threading.Event, force: bool = False) -> None:
        """Run every resource that is due, preserving per-resource timers."""
        if self._get_fuel_settings is None:
            return
        try:
            settings = self._get_fuel_settings()
        except Exception as exc:
            self._log(f"[Fuel] Couldn't read Auto Fuel settings: {exc}")
            return

        resources = settings.get("resources", {})
        due = [
            key for key in FUEL_RESOURCES
            if (resources.get(key) or {}).get("enabled")
            and (force or (resources.get(key) or {}).get("due"))
        ]
        if (not force and not settings.get("enabled")) or not due:
            return

        self._log(f"[Fuel] Starting an Auto Fuel pass for {len(due)} resource(s).")
        self._set_status(
            current_task="Auto Fuel",
            action="Preparing Auto Fuel...",
            mode="fuel",
            map="-",
            stage="-",
            difficulty="-",
            macro="-",
            play_mode="-",
        )

        wm.show_window(hwnd)
        if not wm.activate_window(hwnd):
            self._log("[Fuel] Couldn't confirm Roblox took focus. Continuing anyway.")
        time.sleep(0.5)

        if not self._ensure_lobby(hwnd, stop_event):
            self._fuel_mark_failed(due, stop_event)
            return
        hwnd = self._current_hwnd or hwnd
        if self._checkpoint(stop_event):
            return
        if not self._fuel_enter_hub(hwnd, stop_event):
            self._fuel_mark_failed(due, stop_event)
            if not stop_event.is_set():
                self._recover_to_lobby(hwnd, stop_event)
            return

        paths = settings.get("paths") or {}
        resources = settings.get("resources") or {}
        both_due = all(key in due for key in FUEL_RESOURCES)

        if both_due:
            if not self._fuel_run_path(
                    paths.get("hub_to_resource_drill", ""), stop_event):
                self._fuel_mark_failed(due, stop_event)
            else:
                drill_ok = self._fuel_refill_station(
                    hwnd,
                    stop_event,
                    "resource_drill",
                    resources["resource_drill"].get("amount", "max"),
                )
                if not stop_event.is_set():
                    self._fuel_mark_result("resource_drill", drill_ok)

                if self._fuel_run_path(
                        paths.get("resource_drill_to_gold_mine", ""), stop_event):
                    gold_ok = self._fuel_refill_station(
                        hwnd,
                        stop_event,
                        "gold_mine",
                        resources["gold_mine"].get("amount", "max"),
                    )
                    if not stop_event.is_set():
                        self._fuel_mark_result("gold_mine", gold_ok)
                elif not stop_event.is_set():
                    self._fuel_mark_result("gold_mine", False)
        else:
            resource = due[0]
            path_key = (
                "hub_to_resource_drill"
                if resource == "resource_drill"
                else "hub_to_gold_mine"
            )
            if self._fuel_run_path(paths.get(path_key, ""), stop_event):
                succeeded = self._fuel_refill_station(
                    hwnd,
                    stop_event,
                    resource,
                    resources[resource].get("amount", "max"),
                )
                if not stop_event.is_set():
                    self._fuel_mark_result(resource, succeeded)
            elif not stop_event.is_set():
                self._fuel_mark_result(resource, False)

        if not stop_event.is_set():
            self._log("[Fuel] Auto Fuel pass finished. Returning to the lobby.")
            self._recover_to_lobby(hwnd, stop_event)

    def start_fuel_test(self, hwnd_getter) -> dict:
        """Run one immediate Auto Fuel pass through the normal runner thread."""
        if self.is_running():
            return {"ok": False, "reason": "already_running"}
        if self._get_fuel_settings is None:
            return {"ok": False, "reason": "settings_unavailable"}
        settings = self._get_fuel_settings()
        if not any(
                (settings.get("resources", {}).get(key) or {}).get("enabled")
                for key in FUEL_RESOURCES):
            return {"ok": False, "reason": "no_resources"}

        self._stop_event = threading.Event()
        self._pause_event.clear()
        self._paused_logged = False
        self._stop_logged = False
        self._current_hwnd = None
        self._left_stage_this_run = True
        self._thread = threading.Thread(
            target=self._run_fuel_test,
            args=(hwnd_getter, self._stop_event),
            daemon=True,
        )
        self._thread.start()
        return {"ok": True}

    def _run_fuel_test(self, hwnd_getter, stop_event: threading.Event) -> None:
        hwnd = hwnd_getter() if hwnd_getter else None
        if not hwnd or not wm.is_window(hwnd):
            self._log("[Fuel] No Roblox window found -- can't test Auto Fuel.")
            self._fuel_set_idle_status()
            return
        self._current_hwnd = hwnd
        try:
            wm.prevent_sleep()
            self._log("[Fuel] Manual Auto Fuel test -- running one pass now.")
            self._run_fuel_refill(hwnd, stop_event, force=True)
        finally:
            wm.allow_sleep()
            vision.close_mss()
            if not stop_event.is_set():
                self._log("[Fuel] Auto Fuel test finished.")
            self._fuel_set_idle_status()

    def _fuel_set_idle_status(self) -> None:
        """Clear the standalone test context from every Dashboard field."""
        self._set_status(
            current_task="-",
            current_repeat="-",
            map="-",
            action="Idle",
            mode="-",
            stage="-",
            difficulty="-",
            play_mode="-",
            macro="-",
        )
