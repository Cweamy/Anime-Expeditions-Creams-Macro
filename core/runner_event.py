"""Event mode (Summer Infinite & Fishing + Portal) navigation, as one mixin.

Event is reached straight from the lobby via its OWN event button (not the
Play -> gamemode -> map flow the other modes share) and has no map carousel or
difficulty picker -- the gamemode screen offers "Infinite & Fishing" (the kind
we run) and "Portal Mode" (reserved for later), and picking the card IS the
whole selection. This mixin holds that navigation (and any event-only game
rule later), split out of core/runner.py mechanically like the other *Ops
classes -- see core/runner.py, which composes the mixins (MacroRunner).
"""
import threading
import time

from . import keys
from .runner_constants import *  # noqa: F401,F403 -- the shared constants namespace


class EventOps:
    def _reach_event_act_selected(self, hwnd, stop_event: threading.Event, act: str,
                                    scroll_power: int = None, scroll_nudges: int = None) -> bool:
        """Lobby -> Event -> Summer nav -> Summer event gamemode -> chosen
        event kind card, as one restartable unit -- Event's equivalent of
        _reach_map_selected. Event has its OWN lobby entry (the nav_event
        button), not the Play -> gamemode -> map flow the other modes share,
        so there's no gamemode menu or map carousel here: click nav_event,
        click summer_nav, click the summer_event_gamemode card, then the
        chosen event kind's card (see _reach_event_kind_selected). On any
        failure it backs out to the lobby (_spam_back_until_gone) so the next
        attempt starts clean, same as the map path does.
        """
        kind = str(act)
        # The Summer event's gamemode screen offers an "Infinite & Fishing"
        # card (the one we run) and a "Portal Mode" card (reserved for later).
        # Validate the chosen kind up front so a bad task field fails cleanly
        # here instead of mid-navigation.
        if kind not in EVENT_KIND_ORDER:
            self._log(f'[Macro] Unknown Event kind "{kind}" -- expected one of {EVENT_KIND_ORDER}.')
            return False

        if not self._ensure_lobby(hwnd, stop_event):
            return False
        if self._checkpoint(stop_event):
            return False

        # nav_event: the lobby's Event button (its own nav entry, not under
        # Play). Each image click below is a wait-then-click with a
        # focus-safe verify via _click_found_image, and each screen animates
        # in, so a short settle follows before searching the next one.
        self._set_status(action="Clicking Event...")
        if self._click_found_image(hwnd, "nav_event", EVENT_SCREEN_TIMEOUT, stop_event) is None:
            self._spam_back_until_gone(hwnd, stop_event)
            return False
        if self._checkpoint(stop_event):
            return False
        time.sleep(SETTLE_DELAY)

        # (1) Click the Summer event's own nav entry from the event menu
        self._set_status(action="Clicking Summer Nav...")
        match = self._click_found_image(hwnd, "summer_nav", EVENT_SCREEN_TIMEOUT, stop_event)
        if match is None:
            self._spam_back_until_gone(hwnd, stop_event)
            return False
        if self._checkpoint(stop_event):
            return False
        time.sleep(SETTLE_DELAY)

        # (2) Then the summer_event_gamemode card (the button that opens the
        # Infinite & Fishing / Portal Mode picker) -- found and clicked by
        # image search. Its absence after the card click is the sign the card
        # click failed (spam back + retry from lobby).
        if self._click_found_image(hwnd, "summer_event_gamemode", EVENT_SCREEN_TIMEOUT, stop_event) is None:
            self._spam_back_until_gone(hwnd, stop_event)
            return False
        if self._checkpoint(stop_event):
            return False
        time.sleep(SETTLE_DELAY)

        # (3) Click the chosen event kind's card (Infinite & Fishing, or,
        # later, Portal Mode). _reach_event_kind_selected is the single place
        # that turns the user's selection into a click, so adding a new kind
        # later is a one-line addition to EVENT_KIND_IMAGES rather than a new
        # click path. `scroll_power`/`scroll_nudges` are no longer used here
        # (the kind cards sit above the fold) but stay on the signature to keep
        # the callers unchanged.
        if not self._reach_event_kind_selected(hwnd, stop_event, kind):
            self._spam_back_until_gone(hwnd, stop_event)
            return False
        # Portal picks + activates a specific Summer portal before entering
        # (Infinite goes straight to the stage) -- the extra step that makes
        # Portal "specialized". See _select_summer_portal.
        if kind == "portal":
            if not self._select_summer_portal(hwnd, stop_event, entry=True):
                self._spam_back_until_gone(hwnd, stop_event)
                return False
            if self._checkpoint(stop_event):
                return False
        # Let the stage/Enter-Matchmaking screen finish animating in before
        # the shared tail searches for its confirm button (same reason
        # _select_stage settles after its own click).
        time.sleep(SETTLE_DELAY)
        return not self._checkpoint(stop_event)

    def _reach_event_kind_selected(self, hwnd, stop_event: threading.Event, kind: str) -> bool:
        """Click the Summer event gamemode card for the chosen event kind.

        The gamemode screen shows an "Infinite & Fishing" card (the one we
        run) and a "Portal Mode" card (Tiered & Secret Portals, reserved for
        later). This is the ONE place that turns the user's selection into a
        click, so adding a new kind later is a one-line addition to
        EVENT_KIND_IMAGES rather than a new click path -- the kind is looked
        up there and the matching card clicked by image search. Mirrors
        _reach_tournament_selected's type-card lookup (TOURNAMENT_TYPE_IMAGES).

        Returns True once the kind's card is clicked (and the next screen is
        given a beat to animate in), or False after backing out to the lobby
        on an unknown kind or a card that never shows up -- same recovery the
        other nav methods use, so the retry loop starts clean.
        """
        kind = str(kind)
        kind_images = EVENT_KIND_IMAGES.get(kind)
        if kind_images is None or kind not in EVENT_KIND_ORDER:
            self._set_status(action=f"Unknown Event kind: {kind}")
            self._log(f'[Macro] Unknown Event kind "{kind}" -- expected one of {EVENT_KIND_ORDER}.')
            self._spam_back_until_gone(hwnd, stop_event)
            return False
        if isinstance(kind_images, str):
            kind_images = (kind_images,)

        self._set_status(action=f"Clicking {kind} event...")
        # Any candidate crop that matches wins; later entries are fallbacks
        # for a card that shows in more than one visual state.
        for candidate in kind_images:
            if self._click_found_image(hwnd, candidate, EVENT_SCREEN_TIMEOUT, stop_event) is not None:
                return not self._checkpoint(stop_event)

        self._log(f'[Macro] Could not find the "{kind}" event card.')
        self._spam_back_until_gone(hwnd, stop_event)
        return False

    def _select_summer_portal(self, hwnd, stop_event: threading.Event, entry: bool) -> bool:
        """Pick + activate a Summer portal on the portal picker screen.

        Reused at both ends of a Portal run: entry (entry=True, right after
        the Portal kind card) and post-victory (entry=False, after clicking
        the Victory screen's Select Portal button). Either way the steps are
        the same -- clear + type "Summer" in the search box, click the tier
        card (the `summer_portal` reference crop is tier-specific, so after
        the Summer filter it matches only that tier), then the confirm button
        (the `portal_activate` folder holds "Activate Portal" on entry and
        the "Select" button post-victory) -- only "get to the picker first"
        differs. On any failure it backs out to the lobby so the retry loop
        starts clean.
        """
        if not entry:
            self._set_status(action="Clicking Select Portal...")
            if self._click_found_image(hwnd, "select_new_portal", EVENT_SCREEN_TIMEOUT, stop_event) is None:
                self._spam_back_until_gone(hwnd, stop_event)
                return False
            if self._checkpoint(stop_event):
                return False
            time.sleep(SETTLE_DELAY)

        # Search the portal grid for Summer (other portals exist, and the box
        # may still hold a previous query) -- Ctrl+A to select, Delete to
        # clear, then type. Same search-box recipe the settings search uses.
        self._set_status(action="Searching Summer portals...")
        if self._click_found_image(hwnd, "portal_search", EVENT_SCREEN_TIMEOUT, stop_event, region=PORTAL_SEARCHES.get("search")) is None:
            self._spam_back_until_gone(hwnd, stop_event)
            return False
        self._keyboard.combo(keys.VK_CONTROL, ord("A"))
        self._keyboard.tap(keys.VK_DELETE)
        self._keyboard.type_text("summer")
        time.sleep(SETTLE_DELAY)
        if self._checkpoint(stop_event):
            return False

        # The tier card (tier-specific crop, see the docstring above).
        self._set_status(action="Selecting Summer portal tier...")
        if self._click_found_image(hwnd, "summer_portal", EVENT_SCREEN_TIMEOUT, stop_event, region=PORTAL_SEARCHES.get("portals")) is None:
            self._spam_back_until_gone(hwnd, stop_event)
            return False
        if self._checkpoint(stop_event):
            return False
        time.sleep(SETTLE_DELAY)

        # Confirm: "Activate Portal" on entry, the "Select" button post-victory
        # (both live in the portal_activate folder).
        self._set_status(action="Activating Summer portal..." if entry else "Confirming Summer portal...")
        if self._click_found_image(hwnd, "portal_activate", EVENT_SCREEN_TIMEOUT, stop_event) is None:
            self._spam_back_until_gone(hwnd, stop_event)
            return False
        return not self._checkpoint(stop_event)

    def _run_event_setup(self, hwnd, stop_event: threading.Event, task: dict,
                         scroll_power: int = None, scroll_nudges: int = None) -> bool:
        """Event's whole lobby -> nav -> kind-card entry as one callable, so
        _run_task_setup's event branch is a single call. Retried from the
        lobby like the other modes' map path -- a failed attempt leaves
        nothing safe to assume about where we ended up, so each attempt
        re-checks from scratch. Returns True once the kind card is clicked
        (ready for the shared confirm/Solo tail), False on stop or when every
        retry failed."""
        kind = task.get("stage") or "infinite"
        for attempt in range(1, MAP_SELECT_RETRY_ATTEMPTS + 1):
            if self._checkpoint(stop_event):
                return False
            if attempt > 1:
                self._log(f"[Macro] Retrying Event entry from the lobby "
                          f"(attempt {attempt}/{MAP_SELECT_RETRY_ATTEMPTS})...")
            if self._reach_event_act_selected(hwnd, stop_event, kind, scroll_power, scroll_nudges):
                return True
            if stop_event.is_set():
                return False
        self._log(f'[Macro] Couldn\'t reach the Event entry after {MAP_SELECT_RETRY_ATTEMPTS} '
                  f'attempts -- stopping.')
        return False
