"""Auto Crafting: an interleaved pass that farms sprites in the Crafting area.

Same shape as core/runner_challenge.py -- a mixin providing part of
MacroRunner's behavior (see core/runner.py, which composes the mixins), whose
methods run with MacroRunner's full self (shared state and helpers -- _log,
_checkpoint, _click_found_image, _ensure_lobby, _recover_to_lobby, _mouse,
_keyboard -- resolve normally).

The trigger is a running win counter, not the clock: every qualifying win
(Story on the Mastery stage, or any Challenge win -- see
_note_win_for_crafting) bumps it, and when it reaches the user's "every N"
setting the run loop pauses the queue for one crafting pass exactly the way it
pauses for a ready Challenge slot (see core/runner.py's crafting_wants_in).

Everything the crafter clicks is an image the user supplies under
Assets/ui/<name>/ (nav_area, nav_crafting, craft_menu, craft_max, craft_input,
craft_button, craft_insufficient, and one sprite_* icon per craftable). A
missing/unmatched image is treated as "skip this step/item" and logged, never
a crash -- so a half-set-up crafting config degrades gracefully instead of
stranding the whole run.
"""
import threading
import time

from . import keys
from . import vision
from . import window as wm
from .runner_constants import *  # noqa: F401,F403 -- the shared constants namespace


class CraftingOps:
    def _crafting_has_selected_item(self, settings: dict) -> bool:
        selected = any(
            it.get("enabled") for it in settings.get("items", []))
        if selected:
            self._crafting_invalid_logged = False
            return True
        if int(settings.get("count", 0)):
            self._set_crafting_count(0)
            settings["count"] = 0
        if not getattr(self, "_crafting_invalid_logged", False):
            self._log(
                "[Craft] Invalid Auto Crafting setup -- no sprites are selected. "
                "Skipping Auto Crafting until at least one sprite is enabled.")
            self._crafting_invalid_logged = True
        return False

    def _crafting_win_qualifies(self, task: dict, result: str) -> bool:
        """Whether this finished match counts toward the crafting trigger:
        only WINS, and only on the Mastery Story stage or in Challenge (the
        two farms the user crafts off of). Challenge runs come through as
        mode="story" with is_challenge set (see runner_challenge), so both are
        checked explicitly rather than by mode alone."""
        if result != "win":
            return False
        if task.get("is_challenge"):
            return True
        return task.get("mode") == "story" and task.get("stage") == "Mastery"

    def _note_win_for_crafting(self, task: dict, result: str) -> None:
        """Bump the persisted crafting win counter for a qualifying win.
        Called from _handle_match_result's win path -- the single choke point
        both Mastery and Challenge wins pass through. Only touches the counter
        while Auto Crafting is actually enabled, so a disabled feature never
        accumulates a stale count that would fire the moment it's turned on."""
        if self._get_crafting_settings is None:
            return
        try:
            settings = self._get_crafting_settings()
        except Exception:
            return
        if not settings.get("enabled"):
            return
        if not self._crafting_win_qualifies(task, result):
            return
        if not self._crafting_has_selected_item(settings):
            return
        count = int(settings.get("count", 0)) + 1
        self._set_crafting_count(count)
        every = max(CRAFT_EVERY_MIN, int(settings.get("every", CRAFT_DEFAULT_EVERY)))
        self._log(f"[Craft] Qualifying win {count}/{every} toward the next crafting pass.")

    def _crafting_wants_in(self, task: dict = None, result: str = None) -> bool:
        """Check whether a crafting pass is due right now
        -- enabled, at least one item enabled, and the win counter has reached
        the 'every N' threshold. Mirrors _challenge_has_ready_stage; used by
        _run_task's repeat loop to decide whether to pause the queue.

        When ``task`` and ``result`` are supplied, include the match that just
        finished in the projection. The actual counter is persisted moments
        later by _handle_match_result; projecting it here lets that same result
        click Leave Stage instead of unnecessarily starting one more repeat.
        An invalid no-sprite setup also clears stale progress immediately."""
        if self._get_crafting_settings is None:
            return False
        try:
            settings = self._get_crafting_settings()
        except Exception:
            return False
        if not settings.get("enabled"):
            return False
        if not self._crafting_has_selected_item(settings):
            return False
        every = max(CRAFT_EVERY_MIN, int(settings.get("every", CRAFT_DEFAULT_EVERY)))
        count = int(settings.get("count", 0))
        if task is not None and result is not None and self._crafting_win_qualifies(task, result):
            count += 1
        return count >= every

    def _run_crafting(self, hwnd, stop_event: threading.Event, force: bool = False) -> None:
        """One full crafting pass: lobby -> Area -> Crafting -> wait for the
        area to load -> E to open the menu -> craft each enabled sprite in
        priority order -> back to the lobby. Resets the win counter on the way
        out (whether or not every item succeeded -- the pass HAPPENED, and
        leaving the counter high would just re-fire it next repeat). Any step
        whose image isn't found logs and bails to lobby recovery rather than
        leaving the run stranded in the crafting area.

        force=True is the manual "Run now" test (Auto Crafting screen): it runs
        even when the feature is disabled, and it leaves the real win counter
        UNTOUCHED (a test shouldn't wipe the progress a real run accumulated)."""
        if self._get_crafting_settings is None:
            return
        try:
            settings = self._get_crafting_settings()
        except Exception as exc:
            self._log(f"[Craft] Couldn't read crafting settings: {exc}")
            return
        if not settings.get("enabled") and not force:
            return
        # In force/test mode leave the counter alone; otherwise a finished pass
        # (or an aborted one) clears it so the threshold has to be re-earned.
        reset = (lambda: None) if force else (lambda: self._set_crafting_count(0))
        items = [it for it in settings.get("items", []) if it.get("enabled")]
        if not items:
            self._log("[Craft] No sprites are enabled to craft -- nothing to do.")
            reset()
            return

        self._log(f"[Craft] {'Manual test' if force else 'Win threshold reached'} -- "
                   f"starting a crafting pass ({len(items)} item(s)).")
        self._set_status(current_task="Auto Crafting", action="Crafting...", mode="crafting",
                          map="-", stage="-", difficulty="-", macro="-", play_mode="-")

        if not self._ensure_lobby(hwnd, stop_event):
            return
        if self._checkpoint(stop_event):
            return

        # Lobby -> Area menu -> Crafting.
        self._set_status(action="Opening the Area menu...")
        if self._click_found_image(hwnd, "nav_area", CRAFT_AREA_TIMEOUT, stop_event) is None:
            self._log("[Craft] Couldn't open the Area menu -- skipping this crafting pass.")
            reset()
            return
        if self._checkpoint(stop_event):
            return
        time.sleep(SETTLE_DELAY)
        self._set_status(action="Entering Crafting...")
        if self._click_found_image(hwnd, "nav_crafting", CRAFT_AREA_TIMEOUT, stop_event) is None:
            self._log("[Craft] Couldn't find the Crafting option -- skipping this crafting pass.")
            reset()
            return
        if self._checkpoint(stop_event):
            return

        # Wait for the crafting area to finish loading. nav_play back on
        # screen is the "we're in the world again" signal (same use the user
        # described): the teleport is done and the E menu will actually open.
        self._set_status(action="Loading the crafting area...")
        if not self._crafting_wait_for(hwnd, "nav_play", CRAFT_LOAD_TIMEOUT, stop_event):
            self._log("[Craft] Crafting area never finished loading -- recovering to the lobby.")
            self._recover_to_lobby(hwnd, stop_event)
            reset()
            return
        if self._checkpoint(stop_event):
            return

        # Open the crafting menu with E and wait for it to fully load. Move
        # the cursor to the far right edge before pressing E so it doesn't hover
        # over crafting panel items or trigger tooltips that interfere with OCR.
        time.sleep(CRAFT_LOAD_SETTLE)
        self._mouse.move_to(*vision.ref_to_screen(hwnd, 1140, 378))
        time.sleep(0.1)
        self._log("[Craft] Pressing E to open the crafting menu.")
        self._keyboard.tap(ord("E"))
        time.sleep(SETTLE_DELAY)

        self._set_status(action="Opening the crafting menu...")
        if not self._crafting_wait_for(hwnd, "craft_menu", CRAFT_MENU_TIMEOUT, stop_event):
            # Fallback: if craft_menu isn't visible, check for a blocking UI
            # overlay (nav_closeui). If found, dismiss it and re-tap E.
            self._mouse.move_to(*vision.ref_to_screen(hwnd, 3, 3))
            time.sleep(0.1)
            close_match = self._crafting_find(hwnd, "nav_closeui", 1.0, stop_event)
            if close_match is not None:
                self._log("[Craft] Closing blocking UI overlay (nav_closeui) and retrying E.")
                vision.click_match(self._mouse, hwnd, close_match)
                time.sleep(SETTLE_DELAY)
                self._keyboard.tap(ord("E"))
                time.sleep(SETTLE_DELAY)

            if not self._crafting_wait_for(hwnd, "craft_menu", CRAFT_MENU_TIMEOUT, stop_event):
                self._log("[Craft] Crafting menu didn't open -- recovering to the lobby.")
                self._recover_to_lobby(hwnd, stop_event)
                reset()
                return

        # Craft each enabled sprite in priority (list) order.
        for item in items:
            if self._checkpoint(stop_event):
                break
            self._craft_one_item(hwnd, stop_event, item)

        # Pass done -- reset the counter (real runs only), close the menu,
        # return to the lobby.
        reset()
        if not stop_event.is_set():
            self._log("[Craft] Crafting pass finished -- returning to the lobby.")
            self._keyboard.tap(ord("E"))  # close the menu
            time.sleep(SETTLE_DELAY)
            self._recover_to_lobby(hwnd, stop_event)

    def _craft_one_item(self, hwnd, stop_event: threading.Event, item: dict) -> None:
        """Craft a single sprite: click its icon, set the amount (Max button
        or type a number into the quantity box), click Craft, then watch
        briefly for the insufficient-items warning. On any missing image or
        an insufficient warning it logs and returns so the next item still
        gets its turn -- the whole point of the priority list."""
        key = item.get("key")
        label = CRAFT_SPRITE_LABELS.get(key, key)
        amount = item.get("amount", "max")

        # Sprite icons only ever appear in the crafting panel -- search just
        # that region (see CRAFT_SPRITE_REGION) so a sprite is matched only
        # where it actually is, not elsewhere on screen.
        match = self._crafting_find(hwnd, key, CRAFT_ITEM_TIMEOUT, stop_event, region=CRAFT_SPRITE_REGION)
        if match is None:
            self._log(f'[Craft] "{label}" sprite not found -- skipping it.')
            return
        self._log(f'[Craft] Crafting {label} (amount: {amount})...')
        self._set_status(action=f"Crafting {label}...")
        vision.click_match(self._mouse, hwnd, match)
        time.sleep(SETTLE_DELAY)

        if str(amount).lower() == "max":
            if self._click_found_image(hwnd, "craft_max", CRAFT_ITEM_TIMEOUT, stop_event) is None:
                self._log(f'[Craft] Max button not found for {label} -- skipping it.')
                return
        else:
            input_match = self._crafting_find(hwnd, "craft_input", CRAFT_ITEM_TIMEOUT, stop_event)
            if input_match is None:
                self._log(f'[Craft] Quantity box not found for {label} -- skipping it.')
                return
            vision.click_match(self._mouse, hwnd, input_match)
            time.sleep(0.15)
            # Clear whatever's in the box, then type the number.
            self._keyboard.combo(keys.VK_CONTROL, ord("A"))  # select any existing text
            self._keyboard.tap(keys.VK_DELETE)
            self._keyboard.type_text(str(int(amount)))
        time.sleep(SETTLE_DELAY)

        if self._click_found_image(hwnd, "craft_button", CRAFT_ITEM_TIMEOUT, stop_event) is None:
            self._log(f'[Craft] Craft button not found for {label} -- skipping it.')
            return

        # Failsafe: insufficient items -> skip this item, keep going. A missing
        # craft_insufficient image just means the failsafe is off (find returns
        # None either way), never an error.
        time.sleep(SETTLE_DELAY)
        if self._crafting_present(hwnd, "craft_insufficient", CRAFT_INSUFFICIENT_TIMEOUT, stop_event):
            self._log(f'[Craft] Insufficient items for {label} -- skipping to the next item.')
        else:
            self._log(f'[Craft] {label} crafted.')

    # ── small image helpers (tolerate a not-yet-added template) ──

    def _crafting_wait_for(self, hwnd, name: str, timeout: float, stop_event) -> bool:
        """wait_for_image -> bool, swallowing TemplateNotFound (image the user
        hasn't added yet) as 'not there'."""
        try:
            return vision.wait_for_image(hwnd, name, timeout=timeout, stop_event=stop_event) is not None
        except vision.TemplateNotFound as exc:
            self._log(f"[Craft] {exc}")
            return False

    def _crafting_find(self, hwnd, name: str, timeout: float, stop_event, region: tuple = None):
        """wait_for_image returning the match (or None), swallowing
        TemplateNotFound the same way. When `region` is given the search is
        confined to it; wait_for_image already offsets the returned coords back
        to full-window space, so click_match still lands correctly."""
        try:
            return vision.wait_for_image(hwnd, name, region=region, timeout=timeout, stop_event=stop_event)
        except vision.TemplateNotFound as exc:
            self._log(f"[Craft] {exc}")
            return None

    def _crafting_present(self, hwnd, name: str, timeout: float, stop_event) -> bool:
        """Short poll for a transient image (the insufficient-items warning).
        A single find would race the popup's fade-in, so poll until timeout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if stop_event is not None and stop_event.is_set():
                return False
            try:
                if vision.find_image(hwnd, name) is not None:
                    return True
            except vision.TemplateNotFound:
                return False  # no failsafe image supplied -- treat as "no warning"
            time.sleep(0.2)
        return False

    # ── manual "Run now" test (Auto Crafting screen) ──

    def start_crafting_test(self, hwnd_getter, coords: dict = None) -> dict:
        """Run ONE crafting pass right now against Roblox as it is, without
        waiting on the win counter -- the Auto Crafting screen's "Run now"
        button. Goes through the same self._thread/self._stop_event machinery
        start()/start_debug_test use, so it shows as running and Stop (F2)
        actually cancels it. Mirrors start_debug_test."""
        if self.is_running():
            return {"ok": False, "reason": "already_running"}
        self._stop_event = threading.Event()
        self._pause_event.clear()
        self._paused_logged = False
        self._stop_logged = False
        self._coords = {**DEFAULT_COORDS, **(coords or {})}
        self._current_hwnd = None
        self._thread = threading.Thread(
            target=self._run_crafting_test, args=(hwnd_getter, self._stop_event), daemon=True)
        self._thread.start()
        return {"ok": True}

    def _run_crafting_test(self, hwnd_getter, stop_event: threading.Event) -> None:
        hwnd = hwnd_getter() if hwnd_getter else None
        if not hwnd or not wm.is_window(hwnd):
            self._log("[Craft] No Roblox window found -- can't test crafting.")
            self._set_status(action="Idle")
            return
        self._current_hwnd = hwnd
        # Same focus dance a real Start does -- clicks/keys must reach Roblox,
        # not whatever had focus when the button was pressed.
        wm.show_window(hwnd)
        if not wm.activate_window(hwnd):
            self._log("[Craft] Couldn't confirm Roblox took focus -- continuing anyway.")
        try:
            wm.prevent_sleep()
            self._log("[Craft] Manual crafting test -- running one pass now.")
            self._run_crafting(hwnd, stop_event, force=True)
        finally:
            wm.allow_sleep()
            vision.close_mss()
            if not stop_event.is_set():
                self._log("[Craft] Crafting test finished.")
            self._set_status(action="Idle")
