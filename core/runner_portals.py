"""Portals: a mode-agnostic way to search for and activate a portal.

The Event kind reaches its portal picker through the event gamemode (see
EventOps._select_summer_portal). This mixin is the OTHER lead-in: from the
lobby, open the Inventory (nav_inv), switch to the Portals tab
(normal_portals_nav), and land on the same portal picker. The picker
selection itself is agnostic -- it drives off the PORTAL_SEARCHES regions
(search box + the portal-card list) so it works however the picker was
opened.
"""
import threading
import time

from . import keys
from . import vision
from .runner_constants import *  # noqa: F401,F403 -- the shared constants namespace


class PortalsOp:
    def _run_portal_selection_from_inventory(self, hwnd, stop_event: threading.Event,
                                              query: str = "summer") -> bool:
        """Lobby -> Inventory -> Portals tab -> search `query` -> click the
        tier card -> activate, as one restartable unit.

        The game-agnostic lead-in: unlike the Event kind (reached through the
        event gamemode), the portal picker here is opened from the Inventory's
        Portals tab. On any failure it backs out to the lobby so the retry
        loop starts clean, same as every other nav method.
        """
        if not self._ensure_lobby(hwnd, stop_event):
            return False
        if self._click_found_image(hwnd, "nav_inv", EVENT_SCREEN_TIMEOUT, stop_event) is None:
            self._spam_back_until_gone(hwnd, stop_event)
            return False
        if self._checkpoint(stop_event):
            return False
        time.sleep(SETTLE_DELAY)

        if self._click_found_image(hwnd, "normal_portals_nav", EVENT_SCREEN_TIMEOUT, stop_event) is None:
            self._spam_back_until_gone(hwnd, stop_event)
            return False
        if self._checkpoint(stop_event):
            return False
        time.sleep(SETTLE_DELAY)

        return self._select_portal_on_picker(hwnd, stop_event, query)

    def _select_portal_on_picker(self, hwnd, stop_event: threading.Event,
                                 query: str = "summer") -> bool:
        """Search an already-open portal picker for `query`, click the tier
        card, then activate -- driven by the PORTAL_SEARCHES regions (search
        box + portal-card list) so it's agnostic to how the picker was
        reached (event gamemode, or the Inventory Portals tab).
        """
        self._set_status(action="Selecting portal...")

        # Focus the search box (region center), clear any prior query, type.
        sx, sy, sw, sh = (int(v) for v in PORTAL_SEARCHES["search"])
        self._click_ref(hwnd, sx + sw // 2, sy + sh // 2)
        self._keyboard.combo(keys.VK_CONTROL, ord("A"))
        self._keyboard.tap(keys.VK_DELETE)
        self._keyboard.type_text(query)
        self._interruptible_sleep(SETTLE_DELAY, stop_event)
        if self._checkpoint(stop_event):
            return False

        # Find the tier card, boxed to the portal-card list region.
        px, py, pw, ph = (int(v) for v in PORTAL_SEARCHES["portals"])
        try:
            match = vision.find_image(hwnd, "summer_portal", region=(px, py, pw, ph))
        except vision.TemplateNotFound as exc:
            self._log(f"[Macro] {exc}")
            match = None
        if match is None:
            self._log(f'[Macro] No "{query}" portal card found in the portal list.')
            self._spam_back_until_gone(hwnd, stop_event)
            return False
        self._log(f'[Macro] Found the "{query}" portal card (score {match["score"]:.2f}) -- clicking it.')
        vision.click_match(self._mouse, hwnd, match)
        if self._checkpoint(stop_event):
            return False
        self._interruptible_sleep(SETTLE_DELAY, stop_event)

        # Confirm (Activate Portal on entry; the picker's Select button)
        # lives in the portal_activate folder.
        if self._click_found_image(hwnd, "portal_activate", EVENT_SCREEN_TIMEOUT, stop_event) is None:
            self._spam_back_until_gone(hwnd, stop_event)
            return False
        return not self._checkpoint(stop_event)

    def _select_portal_post_victory(self, hwnd, stop_event: threading.Event,
                                    query: str = "summer") -> bool:
        """Post-victory: click the Victory screen's "Select Portal" button,
        then pick the next portal with `query` -- the Portals-mode equivalent
        of EventOps._select_summer_portal(entry=False), reusing the agnostic
        picker (see _select_portal_on_picker).
        """
        self._set_status(action="Clicking Select Portal...")
        if self._click_found_image(hwnd, "select_new_portal", EVENT_SCREEN_TIMEOUT, stop_event) is None:
            self._spam_back_until_gone(hwnd, stop_event)
            return False
        if self._checkpoint(stop_event):
            return False
        time.sleep(SETTLE_DELAY)
        return self._select_portal_on_picker(hwnd, stop_event, query)
