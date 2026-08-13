"""Macro Operation blocks: Pre Start and Battle block execution -- placement,
upgrades, sells, waits, walks, settings.

Split out of core/runner.py mechanically -- a mixin providing part of
MacroRunner's behavior (see core/runner.py, which composes the mixins).
Methods here run with MacroRunner's full self: shared state and helpers
(_log, _coords, _checkpoint, _click_found_image, ...) resolve normally.
"""
import math
import sys
import threading
import time

from . import detect
from . import input_record
from . import keys
from . import paths as walk_paths
from . import vision
from . import window as wm
from .config import FIXED_WIN_H, FIXED_WIN_W  # the 1152x756 reference client area
from .runner_constants import *  # noqa: F401,F403 -- the shared constants namespace


class BlockOps:
    def _load_battle_blocks(self, task: dict) -> list:
        macro_name = task.get("macro")
        if not macro_name:
            return []
        from . import templates as tpl
        data = tpl.load_template(macro_name)
        blocks = data.get("blocks") or {}
        if isinstance(blocks, list):
            # Oldest flat-list format -- ui/app.js's loadSelectedTemplate()
            # migrates this into prestart/battle client-side the moment you
            # open it in Macro Manager, but never re-saves it to disk on its
            # own -- until you open + Save it again, this stays stuck.
            # Logged here too (already logged by _run_prestart_blocks for
            # Pre Start) so missing Battle blocks is never a silent no-op.
            self._log(f'[Macro] Template "{macro_name}" is saved in an old format -- '
                       f'open it in Macro Manager and Save again to run its Battle blocks.')
            return []
        if "battle" in blocks:
            battle = self._strip_auto_upgrade_for_expedition(blocks.get("battle") or [], task)
            prestart = blocks.get("prestart") or []
        else:
            # Three-phase legacy shape (before/during/after, from before Pre
            # Start/Battle existed) -- Battle-eligible content lived in
            # "during"+"after", the same combination ui/app.js's
            # migrateLegacyBlocks() uses when it migrates this shape
            # client-side. _run_prestart_blocks already has an equivalent
            # fallback to "before" for Pre Start; this was the missing half --
            # without it, an unmigrated template's Battle blocks just silently
            # never ran, which is exactly what got reported as "Battle blocks
            # aren't firing."
            legacy_battle = (blocks.get("during") or []) + (blocks.get("after") or [])
            if legacy_battle:
                self._log(f'[Macro] Template "{macro_name}" is saved in an old format -- running its Battle '
                           f'blocks from the legacy during/after lists. Open it in Creation and Save again '
                           f'to migrate it properly.')
            battle = self._strip_auto_upgrade_for_expedition(legacy_battle, task)
            prestart = blocks.get("before") or []
        # Battle place_unit numbering continues Pre Start's (ui/app.js numbers
        # place_unit blocks across BOTH phases as one list), so flatten Battle
        # starting one past Pre Start's static place_unit count -- computed
        # from the template, not from whether Pre Start actually ran, so the
        # "Test Battle blocks" path numbers the same as a real match. Flatten
        # also expands any detect then/else groups into the linear list the
        # tick engine walks (see core.detect).
        start = detect.flatten(prestart, 1)[1]
        return detect.flatten(battle, start)[0]

    def _load_loop_blocks(self, task: dict) -> dict:
        """The two looping phases' flattened block lists, keyed 'loop_a' /
        'loop_b'. These run DURING the match like Battle, but their whole
        list repeats (see _tick_loop_phases) -- for "watch for an image, then
        act" patterns a once-through list can't express. place_unit numbering
        continues past Pre Start + Battle so unit #N stays consistent with
        ui/app.js's listPlacedUnits (which walks all four phases in order)."""
        empty = {"loop_a": [], "loop_b": []}
        macro_name = task.get("macro")
        if not macro_name:
            return empty
        from . import templates as tpl
        data = tpl.load_template(macro_name)
        blocks = data.get("blocks") or {}
        if not isinstance(blocks, dict):
            return empty
        prestart = blocks.get("prestart") if "prestart" in blocks else (blocks.get("before") or [])
        battle = blocks.get("battle") if "battle" in blocks else ((blocks.get("during") or []) + (blocks.get("after") or []))
        # Continue the ordinal count past Pre Start then Battle, matching the UI.
        start = detect.flatten(prestart or [], 1)[1]
        start = detect.flatten(self._strip_auto_upgrade_for_expedition(battle or [], task), start)[1]
        out = {}
        for key in ("loop_a", "loop_b"):
            loop = self._strip_auto_upgrade_for_expedition(blocks.get(key) or [], task)
            flat, start = detect.flatten(loop, start)
            out[key] = flat
        return out

    def _tick_loop_phases(self, hwnd, stop_event: threading.Event, first_repeat: bool, macro_name: str = None) -> None:
        """Advance each looping phase by one block, restarting it from the top
        when it reaches the end -- so Loop A/B keep cycling for the whole match
        alongside Battle. Reuses _run_battle_blocks_tick by swapping its
        index/state in and out, so detect/_jump, place_unit, and Once all
        behave exactly as they do in Battle."""
        runtime = getattr(self, "_loop_runtime", None)
        if not runtime:
            return
        saved_index, saved_state = self._battle_block_index, self._battle_block_state
        for key in ("loop_a", "loop_b"):
            rt = runtime.get(key)
            if not rt or not rt["blocks"]:
                continue
            self._battle_block_index, self._battle_block_state = rt["index"], rt["state"]
            try:
                self._run_battle_blocks_tick(
                    hwnd, stop_event, rt["blocks"], first_repeat, macro_name,
                    persistent_detects=rt.setdefault("completed_detects", set()))
                if self._battle_block_index >= len(rt["blocks"]):
                    self._battle_block_index = 0  # reached the end -> loop restarts
                    self._battle_block_state = {}
            finally:
                rt["index"], rt["state"] = self._battle_block_index, self._battle_block_state
            if self._checkpoint(stop_event):
                break
        self._battle_block_index, self._battle_block_state = saved_index, saved_state

    def _run_battle_blocks_tick(self, hwnd, stop_event: threading.Event, battle_blocks: list, first_repeat: bool,
                                  macro_name: str = None, persistent_detects=None) -> None:
        """Advances the Battle-phase block list by one step, called once per
        poll of _wait_for_match_result's Victory/Defeat loop instead of
        running the whole list to completion up front -- Upgrade Unit can
        need several separate attempts spread out over the match (see
        _run_upgrade_unit_tick's not_upgradeable/retry handling), so this
        has to interleave with the result check rather than block on it.

        self._battle_block_index/self._battle_block_state (reset once per
        match in _play_one_match) track which block is current and whatever
        per-block progress it's made (e.g. an Upgrade block's remaining
        `times` budget and next-retry time) across calls.
        """
        while self._battle_block_index < len(battle_blocks):
            block = battle_blocks[self._battle_block_index]
            btype = block.get("type")
            # Control ops that core.detect.flatten injected for detect
            # then/else groups. _jump is pure index bookkeeping (skipping the
            # else branch after then ran) -- no game action, so it never
            # costs a tick. detect evaluates its condition once and jumps into
            # the branch NOT taken when the condition is false.
            if btype == "_jump":
                self._battle_block_index += block.get("_offset", 1)
                continue
            if btype == "detect":
                detect_index = self._battle_block_index
                if persistent_detects is not None and detect_index in persistent_detects:
                    # A looped Detect already found its condition (or used up
                    # its configured search attempts) earlier in this match.
                    # Skip the complete Then/Else construct when Loop A/B
                    # comes around again so the action cannot repeat while the
                    # matched image remains visible.
                    self._battle_block_index += block.get("_end_offset", 1)
                    self._battle_block_state = {}
                    return
                loop_enabled, max_attempts, loop_interval = detect.loop_settings(block)
                detect_state = self._battle_block_state.setdefault("detect_loop", {})
                if loop_enabled:
                    next_check = detect_state.get("next_check", 0.0)
                    if next_check and time.time() < next_check:
                        return
                found, matches = detect.evaluate(self, hwnd, block)
                self._log_detect_outcome(block, found, matches, self._battle_block_index + 1, "Battle")
                if loop_enabled and not found:
                    detect_state["attempts"] = detect_state.get("attempts", 0) + 1
                    if max_attempts and detect_state["attempts"] >= max_attempts:
                        if persistent_detects is not None:
                            persistent_detects.add(detect_index)
                        self._log(f'[Macro] Detect block #{self._battle_block_index + 1} reached its '
                                  f'{max_attempts}-search limit -- taking Else.')
                        self._battle_block_index += block.get("_else_offset", 1)
                        self._battle_block_state = {}
                        return
                    detect_state["next_check"] = time.time() + loop_interval
                    return
                if loop_enabled and persistent_detects is not None:
                    persistent_detects.add(detect_index)
                self._battle_block_index += 1 if found else block.get("_else_offset", 1)
                self._battle_block_state = {}
                return
            # place_unit numbering is now the block's own static _ordinal
            # (stamped by flatten over the whole prestart+battle tree, so a
            # not-taken detect branch never shifts anyone's number) -- no
            # runtime counter to advance here.
            if block.get("once") and not first_repeat:
                # Breaks a quick-place chain: the previous placement held Shift
                # because THIS block was the next same-hotkey place_unit, but
                # it never runs, so nothing downstream would release it. Left
                # held, the next Place Unit block skips its own hotkey and
                # places the previous unit instead. Releasing here costs that
                # one chain its speed-up and nothing else.
                self._release_quick_place_shift()
                self._log(f'[Macro] Skipping Battle block #{self._battle_block_index + 1} -- '
                           f'marked "Once" and this isn\'t the first repeat.')
                self._battle_block_index += 1
                self._battle_block_state = {}
                continue

            if btype == "upgrade_unit":
                done = self._run_upgrade_unit_tick(hwnd, stop_event, block, self._battle_block_index + 1)
            elif btype == "sell_unit":
                done = self._run_sell_unit_tick(hwnd, stop_event, block, self._battle_block_index + 1)
                self._battle_block_state = {}
            elif btype == "target_priority":
                done = self._run_target_priority_tick(hwnd, stop_event, block, self._battle_block_index + 1)
                self._battle_block_state = {}
            elif btype == "auto_upgrade_unit":
                done = self._run_auto_upgrade_unit_tick(hwnd, stop_event, block, self._battle_block_index + 1)
                self._battle_block_state = {}
            elif btype == "place_unit":
                # Mid-battle placement (a reinforcement dropped in later,
                # not a Pre Start starter) -- same pixel-search-place/verify
                # logic Pre Start uses, one-shot like Sell Unit. Continues
                # the SAME #ordinal count Pre Start's place_unit blocks left
                # off at, matching ui/app.js's listPlacedUnits() (which
                # numbers place_unit blocks across both phases as one list),
                # so Upgrade/Sell/Auto Upgrade Unit blocks targeting a
                # unit placed here by #index still resolve correctly.
                # (The counting itself happens above, before the "once"
                # skip -- a skipped block still owns its number.)
                left, top, _, _ = wm.get_window_rect_screen(hwnd)
                next_index = self._battle_block_index + 1
                next_block = battle_blocks[next_index] if next_index < len(battle_blocks) else None
                next_is_same_unit = bool(
                    next_block and next_block.get("type") == "place_unit"
                    and block.get("hotkey") and next_block.get("hotkey") == block.get("hotkey"))
                self._run_place_unit_block(hwnd, stop_event, left, top, block, self._battle_block_index + 1,
                                             macro_name, block.get("_ordinal", next_index),
                                             next_is_same_unit=next_is_same_unit)
                done = True
                self._battle_block_state = {}
            elif btype == "wait_ms":
                self._run_wait_ms_tick(stop_event, block, self._battle_block_index + 1)
                done = True
                self._battle_block_state = {}
            elif btype == "walk":
                self._run_walk_block_tick(stop_event, block, self._battle_block_index + 1)
                done = True
                self._battle_block_state = {}
            elif btype == "record":
                self._run_record_macro_tick(hwnd, stop_event, block, self._battle_block_index + 1)
                done = True
                self._battle_block_state = {}
            elif btype == "wait_wave":
                done = self._run_wait_wave_tick(hwnd, block, self._battle_block_index + 1)
            elif btype == "setting_change":
                self._run_setting_block(hwnd, stop_event, block, self._battle_block_index + 1)
                done = True
                self._battle_block_state = {}
            elif btype == "click":
                self._run_click_block(hwnd, stop_event, block, self._battle_block_index + 1)
                done = True
                self._battle_block_state = {}
            elif btype == "send_key":
                self._run_send_key_tick(block, self._battle_block_index + 1)
                done = True
                self._battle_block_state = {}
            elif btype == "leave_at_minute":
                self._run_leave_at_minute_tick(hwnd, stop_event, block, self._battle_block_index + 1)
                done = True
                self._battle_block_state = {}
            else:
                self._log(f'[Macro] Skipping Battle block #{self._battle_block_index + 1} '
                           f'("{btype}") -- not runnable in Battle yet.')
                done = True
                self._battle_block_state = {}

            if done:
                self._battle_block_index += 1
                self._battle_block_state = {}
            # Not done (an Upgrade block still has budget left, or is
            # waiting out its retry cooldown) -- stay on this same block and
            # pick back up here on the next poll tick, rather than blocking
            # the whole loop (and the Victory/Defeat check) on it now.
            return

    def _run_leave_at_minute_tick(self, hwnd, stop_event: threading.Event, block: dict, block_num: int) -> None:
        """Leave the match once it's been running `minutes` minutes.

        Passive until then: on each rotation of the Battle block list it checks
        the battle clock (self._battle_started_at, set per match in
        _play_one_match) and, once the configured minute is reached, leaves to
        the lobby -- clicks nav_todalobby, then the Return confirmation -- and
        sets self._battle_leave_requested so _wait_for_match_result stops
        watching for Victory/Defeat and reports "left" (see there). A failed
        leave-button search just retries on the next rotation."""
        try:
            minutes = float(block.get("params", {}).get("minutes") or DEFAULT_LEAVE_AT_MINUTES)
        except (TypeError, ValueError):
            minutes = DEFAULT_LEAVE_AT_MINUTES
        minutes = max(0.0, minutes)
        started = self._battle_started_at or time.time()
        elapsed_min = (time.time() - started) / 60.0
        if elapsed_min < minutes:
            return  # not yet -- let the rest of the Battle blocks keep running
        self._log(f'Battle block #{block_num} (Leave at Minute): {minutes:g} min reached '
                   f'-- leaving to the lobby.')
        if self._leave_match_to_lobby(hwnd, stop_event):
            self._battle_leave_requested = True

    def _leave_match_to_lobby(self, hwnd, stop_event: threading.Event) -> bool:
        """Leave a live match straight to the lobby: click the in-match To
        Lobby button (nav_todalobby), then the Return to Lobby confirmation.
        Mirrors the Infinite wave-limit exit (_leave_infinite_at_wave_limit),
        which clicks leave_stage instead -- this block uses nav_todalobby."""
        self._release_quick_place_shift()
        self._set_status(action="Leaving to lobby (Leave at Minute)...")
        if not self._click_and_verify_gone(
                hwnd, stop_event, "nav_todalobby", NAV_CLICK_TIMEOUT, success_name="return"):
            self._log('[Macro] "nav_todalobby" not found -- can\'t leave for Leave at Minute (will retry).')
            return False
        self._click_return_to_lobby_if_found(hwnd, stop_event)
        return not self._checkpoint(stop_event)

    def _run_click_block(self, hwnd, stop_event: threading.Event, block: dict, block_num: int,
                           phase_label: str = "Battle") -> None:
        """Click block (Macro Manager > Setup > Click): one raw click at the
        block's fixed (x, y) -- the same 1152x756 window-client coords Place
        Unit's picker writes, so the Set button's map/Roblox-screen picker
        works for this block unchanged. For any button/UI element no
        dedicated block covers -- deliberately no image search or
        verification: it clicks where told, whatever is (or isn't) there,
        which is exactly what makes it a useful escape hatch."""
        label = f"{phase_label} block #{block_num} (Click)"
        params = block.get("params", {})
        try:
            x, y = int(params.get("x") or 0), int(params.get("y") or 0)
        except (TypeError, ValueError):
            self._log(f"[Macro] {label}: bad x/y -- skipping.")
            return
        if not x and not y:
            # (0, 0) is the unset default straight from the palette -- a
            # deliberate top-left-corner click is not a real use case, but a
            # forgotten Set button absolutely is.
            self._log(f"[Macro] {label}: no position set -- skipping.")
            return
        self._log(f"[Macro] {label}: clicking ({x}, {y}).")
        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        self._mouse.click(left + x, top + y)

    def _placed_unit_click_point(self, block: dict, label: str):
        index = block.get("params", {}).get("index")
        try:
            index = int(index)
        except (TypeError, ValueError):
            self._log(f'[Macro] {label}: no unit selected -- skipping.')
            return None
        pos = self._placed_unit_positions.get(index)
        if pos is None:
            self._log(f'[Macro] {label}: unit #{index} was never placed this match (or Pre Start hasn\'t '
                       f'placed it yet) -- skipping.')
            return None
        return pos

    def _run_upgrade_unit_tick(self, hwnd, stop_event: threading.Event, block: dict, block_num: int) -> bool:
        """One attempt: click the unit, look for upgradeable/not_upgradeable.
        Returns True once this block is DONE (times budget used up, or the
        unit/position couldn't be resolved at all) -- False means try again
        later (see UPGRADE_RETRY_WAIT), still holding this block's spot in
        _run_battle_blocks_tick's loop."""
        label = f'Battle block #{block_num} (Upgrade Unit)'
        state = self._battle_block_state
        if "remaining" not in state:
            try:
                state["remaining"] = max(1, int(block.get("params", {}).get("times") or 1))
            except (TypeError, ValueError):
                state["remaining"] = 1
            state["next_attempt"] = 0.0

        if time.time() < state["next_attempt"]:
            return False  # still waiting out the retry cooldown from a previous not_upgradeable

        pos = self._placed_unit_click_point(block, label)
        if pos is None:
            return True

        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        self._mouse.click(left + self._coords["unit_info_reset_x"], top + self._coords["unit_info_reset_y"])
        time.sleep(0.1)

        self._set_status(action=f"Upgrading unit ({state['remaining']} left)...")
        self._mouse.click(left + pos[0], top + pos[1])
        time.sleep(BATTLE_BLOCK_CLICK_SETTLE)
        if self._checkpoint(stop_event):
            return True

        # Waits for the info panel to actually finish loading instead of a
        # single check right after BATTLE_BLOCK_CLICK_SETTLE (0.3s) -- that
        # was reported as consistently too fast right after a unit was just
        # placed (the panel can still be settling), landing on neither
        # image and burning a full UPGRADE_RETRY_WAIT (5s) for nothing.
        # Polling for EITHER one to show up (whichever the panel actually
        # ends up in) is the real "wait until it's loaded" this needs,
        # not just a longer fixed sleep.
        # The two states are the same glyph in two colours, and greyscale
        # matching cannot tell them apart reliably -- a dim (unaffordable)
        # button reached only 0.839 against the 0.90 threshold on a real
        # frame, so it matched NEITHER template and fell through to the
        # "neither" branch below for most of a run. find_upgrade_state
        # locates by template at a relaxed threshold and decides the state
        # by colour; see core.vision for the measurements.
        deadline = time.time() + UPGRADE_PANEL_LOAD_TIMEOUT
        found_name, upgrade_match = None, None
        while True:
            found_name, upgrade_match = vision.find_upgrade_state(hwnd)
            if found_name is not None or time.time() >= deadline:
                break
            if self._checkpoint(stop_event):
                return True
            time.sleep(0.15)
        if found_name == "not_upgradeable":
            not_upgrade_match, upgrade_match = upgrade_match, None
        else:
            not_upgrade_match = None
        if upgrade_match is not None:
            self._log(f'{label}: found Upgradeable (score {upgrade_match["score"]:.2f}) -- pressing T '
                       f'({state["remaining"]} left after this).')
            self._keyboard.tap(ord("T"))
            time.sleep(BATTLE_BLOCK_CLICK_SETTLE)
            if self._checkpoint(stop_event):
                return True
            # Reset click, same corner as before selecting the unit -- closes
            # the info panel the upgrade click left open, so the next thing
            # that runs (another attempt on this same unit, or whatever
            # Battle block comes after it) doesn't have to fight a leftover
            # panel/tooltip still covering the screen.
            self._mouse.click(left + self._coords["unit_info_reset_x"], top + self._coords["unit_info_reset_y"])
            state["remaining"] -= 1
            state["next_attempt"] = 0.0
            # Gold is clearly coming in, so the give-up count starts over --
            # a slow stage should never be mistaken for a maxed unit.
            state["idle_attempts"] = 0
            return state["remaining"] <= 0

        if not_upgrade_match is not None:
            # A fully-upgraded unit is visually identical to one you cannot
            # afford -- both show the same dimmed button -- so this block had
            # no way to tell "wait for gold" from "there is nothing left to
            # buy", and waited forever on the second. Every Battle block
            # behind it waits too, so one finished unit stalls the whole
            # match. Observed live: 14 consecutive retries on a maxed unit
            # until the run was stopped by hand.
            #
            # Reading the "x / y" counter on the button would distinguish the
            # two properly and say which it was; that is worth doing and is
            # not this. This just stops one block hanging the queue.
            state["idle_attempts"] = state.get("idle_attempts", 0) + 1
            if state["idle_attempts"] >= UPGRADE_MAX_IDLE_ATTEMPTS:
                self._log(f'{label}: still not upgradeable after {state["idle_attempts"]} attempts '
                           f'-- the unit is probably fully upgraded (or gold is not coming). '
                           f'Moving on.')
                return True
            self._log(f'{label}: not upgradeable yet (score {not_upgrade_match["score"]:.2f}, '
                       f'green {not_upgrade_match.get("green_fraction", 0) * 100:.0f}%) -- '
                       f'waiting {UPGRADE_RETRY_WAIT:.0f}s and retrying '
                       f'({state["idle_attempts"]}/{UPGRADE_MAX_IDLE_ATTEMPTS}).')
        else:
            self._log(f'{label}: neither "upgradeable" nor "not_upgradeable" found on the info panel '
                       f'(within {UPGRADE_PANEL_LOAD_TIMEOUT:.0f}s) -- waiting {UPGRADE_RETRY_WAIT:.0f}s '
                       f'and retrying.')
        state["next_attempt"] = time.time() + UPGRADE_RETRY_WAIT
        return False

    def _run_sell_unit_tick(self, hwnd, stop_event: threading.Event, block: dict, block_num: int) -> bool:
        """One-shot: click the unit, press X. Always "done" after one try --
        no retry/budget concept like Upgrade Unit has."""
        label = f'Battle block #{block_num} (Sell Unit)'
        pos = self._placed_unit_click_point(block, label)
        if pos is None:
            return True

        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        self._mouse.click(left + self._coords["unit_info_reset_x"], top + self._coords["unit_info_reset_y"])
        time.sleep(0.1)

        self._set_status(action="Selling unit...")
        self._mouse.click(left + pos[0], top + pos[1])
        time.sleep(BATTLE_BLOCK_CLICK_SETTLE)
        if self._checkpoint(stop_event):
            return True

        self._log(f'{label}: clicked unit at {pos} -- pressing X to sell.')
        self._keyboard.tap(ord("X"))
        return True

    def _run_target_priority_tick(self, hwnd, stop_event: threading.Event, block: dict, block_num: int,
                                   phase_label: str = "Battle") -> bool:
        """One-shot: click the unit, open unit info panel, tap R to set/cycle target priority.
        Always returns True when completed."""
        label = f'{phase_label} block #{block_num} (Target Priority)'
        pos = self._placed_unit_click_point(block, label)
        if pos is None:
            return True

        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        self._mouse.click(left + self._coords["unit_info_reset_x"], top + self._coords["unit_info_reset_y"])
        time.sleep(0.1)

        priority = str(block.get("params", {}).get("priority") or "Boss")
        self._set_status(action=f"Setting target priority ({priority})...")
        self._mouse.click(left + pos[0], top + pos[1])
        time.sleep(BATTLE_BLOCK_CLICK_SETTLE)
        if self._checkpoint(stop_event):
            return True

        self._log(f'{label}: clicked unit at {pos} -- pressing R to set target priority to {priority}.')
        self._keyboard.tap(ord("R"))
        time.sleep(BATTLE_BLOCK_CLICK_SETTLE)

        self._reset_unit_info_panel(hwnd)
        return True

    def _run_wait_ms_tick(self, stop_event: threading.Event, block: dict, block_num: int,
                            phase_label: str = "Battle") -> None:
        """Just waits -- no unit/click involved. Slept in small chunks
        (checking _checkpoint between each) rather than one bare
        time.sleep(), so Pause/Stop still cuts in promptly during a long
        configured wait instead of having to sit through the whole thing."""
        try:
            ms = int(block.get("params", {}).get("ms") or 0)
        except (TypeError, ValueError):
            ms = 0
        ms = max(0, ms)
        self._log(f'{phase_label} block #{block_num} (Wait): waiting {ms}ms.')
        self._set_status(action=f"Waiting {ms}ms...")
        deadline = time.time() + ms / 1000.0
        while time.time() < deadline:
            if self._checkpoint(stop_event):
                return
            time.sleep(min(0.1, deadline - time.time()))

    def _run_walk_block_tick(self, stop_event: threading.Event, block: dict, block_num: int,
                              phase_label: str = "Battle") -> None:
        """One-shot: replays a recorded walk path -- the same core.paths
        record/load/replay system the pinned Pre Start Walk Path row
        already uses (see _run_prestart), just picked by name here instead
        of by map. Picks up wherever the player currently is; no position
        tracking needed, same as every other Battle block that just fires
        an action rather than needing to know where a unit was placed.

        Runs in either phase (phase_label): Pre Start allows several of these
        so a routine can walk between multiple starter-placement spots before
        the match begins, not just the single pinned Walk Path."""
        path_name = block.get("params", {}).get("path") or ""
        label = f'{phase_label} block #{block_num} (Walk)'
        if not path_name:
            self._log(f'{label}: no path selected -- skipping.')
            return
        self._log(f'{label}: walking path "{path_name}"...')
        self._set_status(action=f'Walking "{path_name}"...')
        data = walk_paths.load_path(path_name)
        events = data.get("events", [])
        if not events:
            self._log(f'{label}: path "{path_name}" has no recorded movement -- skipping.')
            return
        sprint = bool(block.get("sprint"))
        walk_paths.replay_events(events, self._keyboard, stop_event, sprint=sprint)
        self._log(f'{label}: walk finished{" (sprinting)" if sprint else ""}.')

    def _run_record_macro_tick(self, hwnd, stop_event: threading.Event, block: dict, block_num: int,
                                 phase_label: str = "Battle") -> None:
        """One-shot: replays a recorded mouse+keyboard input sequence (see
        core.input_record) -- the general-purpose counterpart of the Walk
        block above, for any multi-step click/type/scroll sequence no other
        block covers, recorded once via the block's own Record/Stop button
        instead of built up one Click/Send Key block at a time.

        Runs in either phase, same as Walk -- Pre Start can chain several
        before the match begins, Battle/Loop can fire one mid-match."""
        name = block.get("params", {}).get("recording") or ""
        label = f'{phase_label} block #{block_num} (Record)'
        if not name:
            self._log(f'{label}: no recording selected -- skipping.')
            return
        self._log(f'{label}: replaying "{name}"...')
        self._set_status(action=f'Replaying "{name}"...')
        data = input_record.load_recording(name)
        events = data.get("events", [])
        if not events:
            self._log(f'{label}: recording "{name}" has no captured input -- skipping.')
            return
        input_record.replay_events(events, self._mouse, self._keyboard, hwnd, stop_event)
        self._log(f'{label}: replay finished.')

    def _run_wait_wave_tick(self, hwnd, block: dict, block_num: int) -> bool:
        """Waits until the current wave has reached OR already passed the
        configured target -- not exact equality, so a wave that ticks over
        between polls (or was already past target the first time this is
        checked) still counts as done instead of waiting forever for a
        number that will never be read again. Checked periodically (see
        WAIT_WAVE_POLL_INTERVAL), not every single Battle-tick poll --
        each OCR read is several real Tesseract subprocess spawns. Capture
        the Roblox window directly so OCR never needs a screen grab that can
        compose recording overlays and flash the user's display.
        Returns True once done (target reached/passed, or the block's own
        target can't be resolved at all); False to keep waiting.
        """
        label = f'Battle block #{block_num} (Wait for Wave)'
        state = self._battle_block_state
        try:
            target = int(block.get("params", {}).get("wave") or 1)
        except (TypeError, ValueError):
            self._log(f'{label}: no target wave set -- skipping.')
            return True

        if "next_check" not in state:
            state["next_check"] = 0.0
        if time.time() < state["next_check"]:
            return False

        try:
            from core import wave as wave_module
            image = vision.capture_window_region_bgr(hwnd, self._wave_region)
            if image is None or image.size == 0:
                raise RuntimeError("Roblox window capture returned no pixels")
            current, maximum = wave_module.read_wave(image)
        except Exception as exc:
            self._log(f'{label}: OCR failed ({exc}) -- retrying in {WAIT_WAVE_POLL_INTERVAL:.0f}s.')
            state["next_check"] = time.time() + WAIT_WAVE_POLL_INTERVAL
            return False

        if current is None:
            state.pop("wave_target_confirmation", None)
            # Some Expedition gamemodes have no waves at all -- the payload
            # ones count enemies around the objective, and their HUD shows
            # "<n> enemies" where the badge would be. Waiting for a number
            # that will never exist strands every block behind this one, which
            # is exactly the placements it tends to be put in front of.
            #
            # A reward card is proof the battle is genuinely under way (they
            # drop for kills), so it releases the block once the fighting has
            # visibly started. Story/Raid keep waiting for a real number:
            # their badge always exists, so an unreadable one there is a
            # detection problem worth surfacing rather than working around.
            since_card = time.time() - self._last_reward_card_at
            if (self._is_expedition_match and self._last_reward_card_at
                    and since_card >= WAIT_WAVE_NO_COUNTER_SETTLE):
                self._log(f"{label}: no wave counter on this gamemode, but the battle is "
                          f"under way (an upgrade card was picked {since_card:.0f}s ago) -- "
                          f"treating the wait as done.")
                return True
            self._log(f"{label}: couldn't read the wave counter -- retrying in {WAIT_WAVE_POLL_INTERVAL:.0f}s.")
            state["next_check"] = time.time() + WAIT_WAVE_POLL_INTERVAL
            return False

        wave_text = (
            f"{current}/{maximum}"
            if maximum is not None
            else f"{current} (unlimited)"
        )
        if current >= target:
            # Never let one OCR frame unlock later blocks such as Sell Unit.
            # The HUD's blue wave icon has been reported as an extra leading
            # digit (4/15 -> 24/15); read_wave rejects that impossible pair,
            # while this second reading also protects against a plausible
            # one-frame misread such as 4/15 -> 14/15.
            confirmation = state.get("wave_target_confirmation")
            if (
                confirmation
                and confirmation["current"] == current
                and confirmation["maximum"] == maximum
            ):
                confirmation["count"] += 1
            else:
                confirmation = {
                    "current": current,
                    "maximum": maximum,
                    "count": 1,
                }
                state["wave_target_confirmation"] = confirmation
            if confirmation["count"] < 2:
                self._log(
                    f"{label}: wave {wave_text} reached target {target} -- "
                    f"confirming on the next read."
                )
                state["next_check"] = time.time() + WAIT_WAVE_POLL_INTERVAL
                return False
            self._log(f'{label}: wave {wave_text} -- reached (or already past) target {target}.')
            return True

        state.pop("wave_target_confirmation", None)
        self._log(f'{label}: wave {wave_text}, waiting for {target}.')
        self._set_status(action=f"Waiting for wave {target} (currently {current})...")
        state["next_check"] = time.time() + WAIT_WAVE_POLL_INTERVAL
        return False

    def _run_auto_upgrade_unit_tick(self, hwnd, stop_event: threading.Event, block: dict, block_num: int) -> bool:
        """One-shot: select the unit and cycle auto-upgrade to its configured
        priority, either through the info-panel button or the user's matching
        in-game hotkey. Always "done" after one try -- setting a priority
        isn't a repeated action the way Upgrade Unit's clicks are."""
        label = f'Battle block #{block_num} (Auto Upgrade Unit)'
        pos = self._placed_unit_click_point(block, label)
        if pos is None:
            return True

        params = block.get("params", {})
        priority = str(params.get("priority") or "None")
        if priority == "None":
            steps = AUTO_UPGRADE_MAX_PRIORITY + 1
        else:
            try:
                steps = max(1, min(AUTO_UPGRADE_MAX_PRIORITY, int(priority)))
            except ValueError:
                steps = 1

        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        self._set_status(action="Setting auto-upgrade priority...")
        self._mouse.click(left + pos[0], top + pos[1])
        time.sleep(AUTO_UPGRADE_CLICK_SETTLE)
        if self._checkpoint(stop_event):
            return True

        if str(params.get("input") or "click").lower() == "hotkey":
            hotkey = str((self._get_hotkeys() or {}).get("game_auto_upgrade") or "")
            vk = keys.key_name_to_vk(hotkey)
            if vk is None:
                self._log(
                    f"{label}: Hotkey input selected, but Auto Upgrade is unbound in "
                    "Settings > Hotkeys -- skipping."
                )
                return True

            if priority == "None":
                self._log(
                    f"{label}: holding Auto Upgrade hotkey {hotkey.upper()} "
                    "to clear it back to off."
                )
                self._keyboard.key_down(vk)
                try:
                    time.sleep(AUTO_UPGRADE_CLEAR_HOLD)
                finally:
                    self._keyboard.key_up(vk)
            else:
                self._log(
                    f"{label}: pressing Auto Upgrade hotkey {hotkey.upper()} "
                    f"{steps}x to set priority {steps}."
                )
                for n in range(steps):
                    self._keyboard.tap(vk)
                    if n < steps - 1:
                        time.sleep(AUTO_UPGRADE_STEP_DELAY)
                    if self._checkpoint(stop_event):
                        return True
            time.sleep(AUTO_UPGRADE_CLICK_SETTLE)
            self._mouse.click(
                left + self._coords["unit_info_reset_x"],
                top + self._coords["unit_info_reset_y"],
            )
            return True

        # Wait for the info panel to actually finish rendering rather than
        # checking once, AUTO_UPGRADE_CLICK_SETTLE after the click. That single
        # check was reported failing on units placed while a wave spawns -- the
        # panel was simply slower than 0.6s that frame, and the block gave up on
        # it with "not found -- skipping" even though it appeared right after.
        # This is the same poll-to-a-deadline _run_upgrade_unit_tick already
        # does for upgradeable/not_upgradeable; a panel that is already up
        # still costs exactly one search, so nothing gets slower in the normal
        # case. Hotkey input never reaches here -- it deliberately does not
        # depend on finding this button at all.
        deadline = time.time() + AUTO_UPGRADE_PANEL_LOAD_TIMEOUT
        priority_match, priority_name = None, None
        while True:
            try:
                priority_match, priority_name = vision.find_image_any(hwnd, PRIORITY_UPGRADE_IMAGE_NAMES)
            except vision.TemplateNotFound as exc:
                self._log(f'{label}: {exc}')
                return True
            if priority_match is not None or time.time() >= deadline:
                break
            if self._checkpoint(stop_event):
                return True
            time.sleep(AUTO_UPGRADE_PANEL_POLL_INTERVAL)
        if priority_match is None:
            self._log(f'{label}: "priority_upgrade" not found on the info panel '
                       f'(within {AUTO_UPGRADE_PANEL_LOAD_TIMEOUT:.0f}s) -- skipping.')
            return True

        debug_path = self._debug_save(hwnd, priority_name, priority_match)
        suffix = f" Debug: {debug_path}" if debug_path else ""

        # This control is a CYCLING BUTTON, not a context menu.
        #
        # It used to be driven as a menu: right-click the icon, then click a
        # row at priority_upgrade's height x a multiplier below it. No menu
        # ever opens, so that second click landed on empty space -- observed
        # live as "priority 6 didn't apply, it missed and didn't activate".
        # It also only ever clicked ONCE regardless of the priority asked
        # for, so even the intent was wrong.
        #
        # What it actually is: each left click advances the priority by one,
        # so priority N is N clicks on the icon itself, and one click past
        # the last priority wraps it back to off.
        cx = left + priority_match["cx"]
        cy = top + priority_match["cy"]

        if priority == "None":
            clicks = steps
            self._log(f'{label}: found "{priority_name}" (score {priority_match["score"]:.2f}) -- '
                       f'clicking it {clicks}x to cycle back to off.{suffix}')
        else:
            clicks = steps
            self._log(f'{label}: found "{priority_name}" (score {priority_match["score"]:.2f}) -- '
                       f'clicking it {clicks}x for priority {clicks}.{suffix}')

        for n in range(clicks):
            self._mouse.click(cx, cy)
            # Between clicks, not after the last one -- the control has to
            # register each step separately or several land as one.
            if n < clicks - 1:
                time.sleep(AUTO_UPGRADE_STEP_DELAY)
            if self._checkpoint(stop_event):
                return True
        time.sleep(AUTO_UPGRADE_CLICK_SETTLE)

        self._mouse.click(left + self._coords["unit_info_reset_x"], top + self._coords["unit_info_reset_y"])
        return True

    def _run_prestart_detect(self, hwnd, stop_event: threading.Event, block: dict, block_num: int):
        """Evaluate a Detect block, optionally polling it until it resolves.

        Returns True for Then, False for Else, and None when Stop interrupts
        an in-progress self-loop. This is deliberately local to Detect: the
        surrounding Pre Start/Battle/Loop scheduling remains unchanged.
        """
        loop_enabled, max_attempts, loop_interval = detect.loop_settings(block)
        attempts = 0
        while True:
            found, matches = detect.evaluate(self, hwnd, block)
            self._log_detect_outcome(block, found, matches, block_num, "Pre Start")
            if found or not loop_enabled:
                return found
            attempts += 1
            if max_attempts and attempts >= max_attempts:
                self._log(f'[Macro] Detect block #{block_num} reached its '
                          f'{max_attempts}-search limit -- taking Else.')
                return False
            if self._checkpoint(stop_event):
                return None
            self._interruptible_sleep(loop_interval, stop_event)
            if stop_event.is_set():
                return None

    def _run_prestart_blocks(self, hwnd, stop_event: threading.Event, task: dict, first_repeat: bool = True,
                               default_walk_paths: dict = None) -> None:
        # The task's Macro Operation (Creation > template) is what actually
        # places starter units and flips settings -- this is the piece that
        # was never wired up: the field existed on every Task card, but
        # nothing ever read it. Runs after camera+walk and before Start Game
        # is pressed, same as Pre Start blocks are laid out in Creation.
        # A synthesized Auto Walk Path block, used everywhere below a real
        # one is missing: walking is MANDATORY now, not opt-in -- Auto mode
        # resolves the map's Default Auto Walk entry (Settings > Debug >
        # Pathing; ships with known-good paths for Fairy King Forest,
        # King's Tomb and Spirit City Act 3) and quietly does nothing for a
        # map without one, so forcing it on can never walk somewhere wrong,
        # only fix the "template/task without the block never walks" hole.
        # once=True matches how the editor now pins it (and what the walk
        # does anyway -- _run_walk_path_block itself only walks on the
        # first entry into a stage).
        auto_walk_block = {"type": "walk_path", "params": {}, "once": True, "mode": "auto", "pathName": ""}

        macro_name = task.get("macro")
        if not macro_name:
            self._log("[Macro] No Macro Operation set on this task -- running just the default Auto walk.")
            self._run_walk_path_block(hwnd, stop_event, task, default_walk_paths or {},
                                        auto_walk_block, first_repeat)
            return

        from . import templates as tpl
        data = tpl.load_template(macro_name)
        blocks = data.get("blocks") or {}
        if isinstance(blocks, list):
            self._log(f'[Macro] Template "{macro_name}" is saved in an old format -- '
                       f'open it in Macro Manager and Save again to run its Pre Start blocks.')
            # Its blocks can't run, but the mandatory Auto walk still can.
            self._run_walk_path_block(hwnd, stop_event, task, default_walk_paths or {},
                                        auto_walk_block, first_repeat)
            return
        prestart_blocks = blocks.get("prestart") if "prestart" in blocks else blocks.get("before")
        prestart_blocks = self._strip_auto_upgrade_for_expedition(prestart_blocks or [], task)

        # Walk Path used to be saved as a separate top-level blocks["walk"]
        # config instead of a real block in this list -- ui/app.js's own
        # Creation UI migrates that into a real walk_path block the moment
        # a template's opened there, but a template that's never been
        # reopened+resaved since that change is still sitting on disk in
        # the OLD shape, and this runner has no other path left that reads
        # blocks["walk"] anymore (confirmed from a real report: Challenge's
        # "Kings Tomb" template silently stopped walking Auto -- it had
        # never been touched in Creation since the update). Migrated here
        # too, the same way (a synthesized block at the very top, where it
        # always effectively ran before), so a template someone never
        # happens to open in the editor still walks correctly.
        legacy_walk = blocks.get("walk")
        if not any(b.get("type") == "walk_path" for b in prestart_blocks):
            # No walk block at all (a template saved back when the block was
            # removable, or hand-edited) gets the plain synthesized Auto one
            # -- same mandatory-walk rule as the no-macro case above.
            prestart_blocks = [{
                "type": "walk_path", "params": {}, "once": True,
                "mode": "custom" if legacy_walk and legacy_walk.get("mode") == "custom" else "auto",
                "pathName": (legacy_walk.get("pathName") or "") if legacy_walk else "",
            }] + prestart_blocks

        # Flatten detect then/else groups into the linear list, stamping each
        # place_unit with its static _ordinal -- the same numbering
        # ui/app.js's listPlacedUnits() produces (place_unit blocks across
        # both phases as one list, then before else), so "unit #N" means the
        # same unit in the editor and at runtime no matter which branch runs.
        prestart_blocks, _ = detect.flatten(prestart_blocks, 1)
        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        self._log(f'[Macro] Running Pre Start blocks from "{macro_name}"...')
        self._set_status(action=f'Running "{macro_name}" Pre Start blocks...')
        self._last_unit_ordinal = 0
        self._quick_place_shift_down = False
        try:
            # An index loop (not enumerate) so detect/_jump control blocks can
            # jump over the branch not taken. `step` numbers only real,
            # non-control blocks for the log, so the numbers a user sees don't
            # count the synthetic jumps.
            idx, step = 0, 0
            while idx < len(prestart_blocks):
                if self._checkpoint(stop_event):
                    return
                block = prestart_blocks[idx]
                btype = block.get("type")
                if btype == "_jump":
                    idx += block.get("_offset", 1)
                    continue
                step += 1
                if btype == "detect":
                    found = self._run_prestart_detect(hwnd, stop_event, block, step)
                    if found is None:
                        return
                    idx += 1 if found else block.get("_else_offset", 1)
                    continue
                if block.get("once") and not first_repeat:
                    # "Once" (see the block's Once chip in Creation) means only
                    # the task's FIRST entry into this stage runs it -- e.g. a
                    # starter placement that shouldn't be re-placed (and would
                    # just get rejected as a duplicate/waste a click) on every
                    # repeat of the same stage.
                    #
                    # Skipping it also breaks any quick-place chain it was part
                    # of. The finally: below guarantees Shift never escapes this
                    # function, but that fires after the whole list -- far too
                    # late for the next Place Unit block, which would otherwise
                    # run with Shift still down and place the PREVIOUS unit on
                    # its tile. Release it here, where the chain actually breaks.
                    self._release_quick_place_shift()
                    self._log(f'[Macro] Skipping block #{step} -- marked "Once" and this isn\'t the first repeat.')
                    idx += 1
                    continue
                next_block = prestart_blocks[idx + 1] if idx + 1 < len(prestart_blocks) else None
                self._run_prestart_single_block(hwnd, stop_event, task, default_walk_paths or {},
                                                  block, step, macro_name, first_repeat, next_block)
                time.sleep(0.2)  # brief gap between blocks so the game UI can settle
                idx += 1
        finally:
            # Safety net -- a "Once"-skipped block right after the last
            # quick-place placement (or the list just ending mid-chain)
            # would otherwise leave Shift stuck down for good, since
            # next_is_same_unit's own block never actually runs to release
            # it. Whatever else happens, Shift never leaves this function
            # still held.
            self._release_quick_place_shift()

    def _run_prestart_single_block(self, hwnd, stop_event: threading.Event, task: dict, default_walk_paths: dict,
                                     block: dict, i: int, macro_name: str, first_repeat: bool, next_block: dict) -> None:
        """Runs ONE non-control Pre Start block. Split out of
        _run_prestart_blocks so both the top-level list and a detect block's
        then/else branches dispatch through the exact same code."""
        btype = block.get("type")
        if btype == "place_unit":
            next_is_same_unit = bool(
                next_block and next_block.get("type") == "place_unit"
                and block.get("hotkey") and next_block.get("hotkey") == block.get("hotkey"))
            self._run_place_unit_block(hwnd, stop_event, *wm.get_window_rect_screen(hwnd)[:2], block, i, macro_name,
                                         block.get("_ordinal", i), next_is_same_unit=next_is_same_unit, verify=False)
        elif btype == "setting_change":
            self._run_setting_block(hwnd, stop_event, block, i)
        elif btype == "auto_upgrade_unit":
            self._run_auto_upgrade_unit_tick(hwnd, stop_event, block, i)
        elif btype == "walk_path":
            self._run_walk_path_block(hwnd, stop_event, task, default_walk_paths or {}, block, first_repeat)
        elif btype == "walk":
            self._run_walk_block_tick(stop_event, block, i, phase_label="Pre Start")
        elif btype == "record":
            self._run_record_macro_tick(hwnd, stop_event, block, i, phase_label="Pre Start")
        elif btype == "click":
            self._run_click_block(hwnd, stop_event, block, i, phase_label="Pre Start")
        elif btype == "wait_ms":
            self._run_wait_ms_tick(stop_event, block, i, phase_label="Pre Start")
        elif btype == "send_key":
            self._run_send_key_tick(block, i, phase_label="Pre Start")
        elif btype == "target_priority":
            self._run_target_priority_tick(hwnd, stop_event, block, i, phase_label="Pre Start")
        else:
            self._log(f'[Macro] Skipping block #{i} ("{btype}") -- not runnable in Pre Start yet.')

    def _log_detect_outcome(self, block: dict, found: bool, matches: list, num: int, phase_label: str) -> None:
        """Report a Detect block's result to the Process Log: whether it
        matched, which branch that takes, and where it matched (the whole
        point of the block is being able to read that back)."""
        label = f"{phase_label} block #{num} (Detect)"
        what = self._detect_condition_label(block)
        branch = "Then" if found else "Else"
        if matches:
            where = ", ".join(f"({m['cx']}, {m['cy']}) score {m['score']:.2f}" for m in matches[:8])
            more = f" (+{len(matches) - 8} more)" if len(matches) > 8 else ""
            self._log(f'{label}: {what} -- FOUND at {where}{more}. Running {branch} branch.')
        else:
            verdict = "FOUND" if found else "not found"
            self._log(f'{label}: {what} -- {verdict}. Running {branch} branch.')

    @staticmethod
    def _detect_condition_label(block: dict) -> str:
        """A short human description of what a Detect block is looking for,
        for the log line."""
        mode = block.get("mode") or "single"
        if mode == "expr":
            return f'condition `{(block.get("expr") or "").strip() or "(empty)"}`'
        if mode == "multi":
            names = [str(n) for n in (block.get("images") or []) if n]
            joiner = " OR " if block.get("logic") == "or" else " AND "
            return "images " + (joiner.join(f'"{n}"' for n in names) if names else "(none set)")
        return f'image "{block.get("image") or "(none set)"}"'

    def _run_walk_path_block(self, hwnd, stop_event: threading.Event, task: dict, default_walk_paths: dict,
                               block: dict, first_repeat: bool) -> None:
        """Walk Path block -- Auto (the map's own default_walk_paths entry)
        or a specific recorded Custom path (block["mode"]/block["pathName"],
        same shape the old separate pinned row used to keep at the template
        level). Only makes sense the FIRST time a task enters a stage --
        once you're standing where the walk leaves you, repeating the same
        walk on every repeat would just walk you away from that spot again
        for no reason -- so this checks first_repeat itself regardless of
        the block's own "Once" toggle, same hardcoded skip the old fixed
        pre-step always had."""
        if not first_repeat:
            self._log('[Macro] Repeat of the same stage -- skipping the Walk Path block (already walked on entry).')
            return

        map_name = task.get("map")
        if block.get("mode") == "custom" and block.get("pathName"):
            path_name = block["pathName"]
        else:
            # A Raid map's Acts (and Event's) can each need a different walk
            # (e.g. Spirit City Act 3, or each Event villain -- see ACT_ORDER/
            # EVENT_ACT_ORDER) -- looked up as "<map> Act<n>" first, falling
            # back to the plain map-name entry other Acts/Story share, so only
            # the Acts that actually need a different walk need their own
            # default_walk_paths entry. Event ships "Event Act1"/"Event Act2"
            # -> Villian1/Villian2 (see Assets/default_walk_paths.json).
            path_name = None
            if map_name:
                if task.get("mode") in ("raid", "event"):
                    path_name = default_walk_paths.get(f"{map_name} Act{task.get('stage')}")
                path_name = path_name or default_walk_paths.get(map_name)
        if not path_name:
            self._log(f'[Macro] No default walk path set for "{map_name}" -- skipping walk.'
                       if map_name else "[Macro] No map set -- skipping walk.")
            return

        self._log(f'[Macro] Walking path "{path_name}"...')
        self._set_status(action=f'Walking "{path_name}"...')
        data = walk_paths.load_path(path_name)
        events = data.get("events", [])
        if not events:
            self._log(f'[Macro] Walk path "{path_name}" has no recorded movement -- skipping.')
            return
        sprint = bool(block.get("sprint"))
        walk_paths.replay_events(events, self._keyboard, stop_event, sprint=sprint)
        self._log(f'[Macro] Walk finished{" (sprinting)" if sprint else ""}.')

    def _release_quick_place_shift(self) -> None:
        if self._quick_place_shift_down:
            self._keyboard.key_up(keys.VK_SHIFT)
            self._quick_place_shift_down = False

    def _capture_place_search_region(self, hwnd, left: int, top: int, region: tuple):
        """Capture the frame that contains Roblox's placement highlight.

        On macOS the highlight is part of the composed Metal frame and can be
        missing from CGWindowListCreateImage even though the rest of the
        window is captured correctly. The screen capture is intentional for
        this small, visible-only scan; Windows keeps the window-content path
        to avoid the display-flash regression that motivated the v0.18 change.
        """
        if sys.platform == "darwin":
            from core.ocr import capture_region
            x, y, width, height = (int(value) for value in region)
            return capture_region(left + x, top + y, width, height)
        return vision.capture_window_region_bgr(hwnd, region)

    def _scan_place_search_box(self, hwnd, left: int, top: int, orig_x: int, orig_y: int):
        """One capture of the PLACE_SEARCH_BOX_SIZE x PLACE_SEARCH_BOX_SIZE
        region around (orig_x, orig_y) -- window-client coords -- scanned in
        memory for a pixel at/near 0xffffff (white, within
        PLACE_VALID_PIXEL_TOLERANCE per channel). Returns the (dx, dy) offset
        of whichever valid pixel is CLOSEST to (orig_x, orig_y), or None if
        nothing valid was found anywhere in the box. Windows reads Roblox's
        window contents to avoid display flashes; macOS uses a small screen
        capture because its transient placement highlight can be missing from
        CGWindowListCreateImage.

        The box is CLAMPED to the game window. Centering it blindly meant a
        spot within half a box of an edge captured pixels from outside the
        game entirely -- on Windows the docked game sits inside this app's
        own frame, so the neighbouring pixels are the macro's control panel,
        which is near-white in the Light theme and reads as a perfectly good
        placement tile. The spiral escalation already refuses to stop within
        PLACE_SPIRAL_MARGIN of an edge for the same reason; this is the
        initial scan finally agreeing with it.

        Clamping shifts the box, so the "closest" test and the returned
        offset are both measured from where the caller actually asked about,
        not from the middle of the captured region."""
        import numpy as np
        size = PLACE_SEARCH_BOX_SIZE
        half = size // 2
        # Top-left of the box, pulled back inside the window if centering it
        # would hang over an edge. max(0, ...) second so a window somehow
        # narrower than the box degrades to "start at 0" rather than negative.
        box_x = max(0, min(orig_x - half, FIXED_WIN_W - size))
        box_y = max(0, min(orig_y - half, FIXED_WIN_H - size))
        patch = self._capture_place_search_region(hwnd, left, top, (box_x, box_y, size, size))
        if patch is None or patch.size == 0:
            return None
        b, g, r = patch[:, :, 0].astype(int), patch[:, :, 1].astype(int), patch[:, :, 2].astype(int)
        floor = 255 - PLACE_VALID_PIXEL_TOLERANCE
        valid_mask = (r >= floor) & (g >= floor) & (b >= floor)
        ys, xs = np.where(valid_mask)
        if len(xs) == 0:
            return None
        # Where the requested spot sits inside the (possibly shifted) box.
        cx, cy = orig_x - box_x, orig_y - box_y
        dists = (xs - cx) ** 2 + (ys - cy) ** 2
        best = int(np.argmin(dists))
        return int(xs[best]) - cx, int(ys[best]) - cy

    def _find_valid_place_spot(self, hwnd, stop_event: threading.Event, left: int, top: int,
                                 orig_x: int, orig_y: int, name: str):
        """Moves onto (orig_x, orig_y) -- window-client coords -- then
        repeatedly wiggles the cursor a little and rescans a small box
        around it (see _scan_place_search_box) until a valid tile turns up
        or PLACE_SEARCH_WIGGLE_TIMEOUT runs out. The wiggling isn't
        cosmetic -- reported (and confirmed from a real run) that a single
        move-then-capture consistently found nothing even on spots that
        WOULD have read as valid a moment later: the placement-mode
        highlight overlay apparently needs to actually see the cursor
        moving/hovering there before it renders at all, not just land on a
        coordinate. Returns the (x, y) window-client offset it settled on,
        or None if nothing valid ever showed up in time."""
        self._mouse.move_to(left + orig_x, top + orig_y)
        time.sleep(PLACE_PIXEL_SEARCH_SETTLE)

        deadline = time.time() + PLACE_SEARCH_WIGGLE_TIMEOUT
        wiggle_idx = 0
        while True:
            if self._checkpoint(stop_event):
                return None
            found = self._scan_place_search_box(hwnd, left, top, orig_x, orig_y)
            if found is not None:
                dx, dy = found
                cx, cy = orig_x + dx, orig_y + dy
                if (dx, dy) != (0, 0):
                    self._mouse.move_to(left + cx, top + cy)
                    time.sleep(PLACE_PIXEL_SEARCH_SETTLE)
                    self._log(f'[Macro] Place Unit "{name}": aligned to a valid tile at offset ({dx}, {dy}).')
                return cx, cy
            if time.time() >= deadline:
                # Nothing valid right AT the spot -- widen the hunt instead
                # of giving up: the 38px scan box physically can't see a
                # tile the game moved further than ~19px away.
                return self._spiral_search_place_spot(hwnd, stop_event, left, top, orig_x, orig_y, name)
            wx, wy = PLACE_SEARCH_WIGGLE_OFFSETS[wiggle_idx % len(PLACE_SEARCH_WIGGLE_OFFSETS)]
            self._mouse.nudge(wx, wy)
            wiggle_idx += 1
            time.sleep(PLACE_PIXEL_SEARCH_SETTLE)

    def _spiral_search_place_spot(self, hwnd, stop_event: threading.Event, left: int, top: int,
                                    orig_x: int, orig_y: int, name: str):
        """The in-place search's escalation: walk the cursor outward in
        rings around the saved spot (8 compass stops per PLACE_SPIRAL_RADII
        ring, nearest ring first), rescanning the box around each stop
        until a valid tile shows up, the rings run out, or
        PLACE_SPIRAL_TIMEOUT burns down. The cursor genuinely travels --
        the placement highlight only renders where the cursor actually
        hovers, so scanning distant boxes without moving would read
        unhighlighted tiles and find nothing. Returns the settled (x, y)
        window-client offset or None, same contract as
        _find_valid_place_spot."""
        self._log(f'[Macro] Place Unit "{name}": no valid tile right at ({orig_x}, {orig_y}) -- '
                   f'searching outward around it.')
        deadline = time.time() + PLACE_SPIRAL_TIMEOUT
        for radius in PLACE_SPIRAL_RADII:
            for i in range(8):
                if self._checkpoint(stop_event) or time.time() >= deadline:
                    return None
                angle = i * math.pi / 4
                px = int(orig_x + radius * math.cos(angle))
                py = int(orig_y + radius * math.sin(angle))
                if not (PLACE_SPIRAL_MARGIN <= px <= FIXED_WIN_W - PLACE_SPIRAL_MARGIN
                        and PLACE_SPIRAL_MARGIN <= py <= FIXED_WIN_H - PLACE_SPIRAL_MARGIN):
                    continue
                self._mouse.move_to(left + px, top + py)
                self._mouse.nudge()  # the highlight needs real relative motion to render
                time.sleep(PLACE_PIXEL_SEARCH_SETTLE)
                found = self._scan_place_search_box(hwnd, left, top, px, py)
                if found is not None:
                    dx, dy = found
                    cx, cy = px + dx, py + dy
                    self._mouse.move_to(left + cx, top + cy)
                    time.sleep(PLACE_PIXEL_SEARCH_SETTLE)
                    self._log(f'[Macro] Place Unit "{name}": found a valid tile at ({cx}, {cy}) '
                               f'({radius}px out from the saved spot).')
                    return cx, cy
        return None

    def _run_place_unit_block(self, hwnd, stop_event: threading.Event, left: int, top: int, block: dict,
                                index: int, macro_name: str, unit_ordinal: int = None,
                                next_is_same_unit: bool = False, verify: bool = True) -> None:
        params = block.get("params") or {}
        name = params.get("name") or f"#{index}"
        hotkey = block.get("hotkey")
        orig_x, orig_y = params.get("x"), params.get("y")
        self._set_status(action=f'Placing unit "{name}"...')

        if not (orig_x or orig_y):
            self._log(f'[Macro] Place Unit "{name}" has no position set -- skipping.')
            # Every other early return below honours this guard; this one used
            # to return before reaching any of them. If this block was a
            # quick-place chain member, Shift stayed held with nothing left to
            # release it, and the NEXT Place Unit block -- a different unit --
            # took the "Shift is down, same unit is still selected" path,
            # skipped its own hotkey, and put THIS unit on that unit's tile.
            if not next_is_same_unit:
                self._release_quick_place_shift()
            return
        orig_x, orig_y = int(orig_x), int(orig_y)

        # Quick place: a run of consecutive Place Unit blocks for the SAME
        # unit (matched by hotkey) holds Left Shift down from right before
        # the first one is clicked through the last one -- while it's held,
        # the same unit stays selected, so every placement after the first
        # skips Z/the hotkey press entirely and just places straight into
        # the next spot. self._quick_place_shift_down being already True
        # here means this call IS one of those continuations.
        # Whether THIS placement is part of a quick-place run at all (either
        # continuing one, or about to start one that continues after it) --
        # used below to skip the unit_exist verify step, which otherwise
        # breaks the whole point of quick-place: a click, then wait, then
        # (if not immediately confirmed) ANOTHER click and up to
        # PLACE_UNIT_VERIFY_TIMEOUT more seconds, before the next hover-and-
        # click can even start. The pre-click pixel-white confirmation is
        # already solid evidence the placement landed -- good enough for a
        # fast consecutive run, even without also re-confirming after.
        is_quick_place = self._quick_place_shift_down or next_is_same_unit
        # verify=False for every Pre Start placement, not just quick-place
        # chains (see _run_prestart_blocks/_run_battle_blocks_tick's own
        # calls) -- the wait-for-unit_exist-then-maybe-double-click-to-
        # recheck step only makes sense for a mid-battle reinforcement,
        # where confirming it actually landed matters more than speed.
        # Pre Start already trusts the pre-click pixel-white confirmation
        # for quick-place; this extends that same trust to every other
        # Pre Start placement too instead of just the chained ones.
        skip_verify = is_quick_place or not verify

        # "Keep Placing" (block toggle): keep re-doing the whole placement
        # until unit_exist confirms it landed, up to a cap. Handled by its
        # own self-contained method -- it always verifies (even in Pre Start,
        # which normally skips verification for speed) since it needs that
        # signal to know when to stop, and it never applies to a quick-place
        # chain (those can't verify mid-run).
        if bool(block.get("retryUntilPlaced")) and not is_quick_place:
            self._place_unit_retrying(hwnd, stop_event, left, top, name, hotkey,
                                       orig_x, orig_y, block, unit_ordinal)
            return

        if self._quick_place_shift_down:
            self._log(f'[Macro] Place Unit "{name}": quick-placing (Shift held, same unit as last).')
        else:
            # No hotkey (or one that isn't recognized) means nothing ever
            # gets selected -- the pixel search below would just be
            # hovering/clicking with no unit in hand at all, which is
            # exactly the "something's wrong" this was reported as during
            # quick-place chains. Skip the whole block outright instead of
            # only logging a warning and clicking anyway.
            if not hotkey:
                self._log(f'[Macro] Place Unit "{name}" has no hotkey set -- skipping this block.')
                return
            vk = keys.key_name_to_vk(hotkey)
            if vk is None:
                self._log(f'[Macro] Place Unit "{name}": hotkey "{hotkey}" isn\'t recognized -- '
                           f'skipping this block.')
                return

            # Z first, always -- clears whatever the cursor/UI was last doing
            # so the hotkey press right after it reliably starts a fresh
            # placement instead of potentially colliding with leftover state.
            self._keyboard.tap(ord("Z"))
            time.sleep(0.1)
            self._log(f'[Macro] Place Unit "{name}": pressing hotkey "{hotkey}" -- entering placing mode.')
            self._keyboard.tap(vk)
            time.sleep(PLACE_HOTKEY_SETTLE)

            if next_is_same_unit:
                self._log(f'[Macro] Place Unit "{name}": next placement is the same unit -- '
                           f'holding Shift for quick-place.')
                self._keyboard.key_down(keys.VK_SHIFT)
                self._quick_place_shift_down = True

        if block.get("ignoreHighlight"):
            # Skips the white-tile search entirely -- clicks the saved X/Y
            # directly, same as before the search existed at all. For a
            # spot where the highlight doesn't reliably show/detect,
            # searching for it is worse than just trusting the coordinate.
            self._mouse.move_to(left + orig_x, top + orig_y)
            time.sleep(PLACE_PIXEL_SEARCH_SETTLE)
            spot = (orig_x, orig_y)
        else:
            spot = self._find_valid_place_spot(hwnd, stop_event, left, top, orig_x, orig_y, name)
        if self._checkpoint(stop_event):
            self._release_quick_place_shift()
            return
        if spot is None:
            self._log(f'[Macro] Place Unit "{name}": no valid (white) tile found at ({orig_x}, {orig_y}) '
                       f'or within {PLACE_SPIRAL_RADII[-1]}px around it -- giving up on this block.')
            if not next_is_same_unit:
                self._release_quick_place_shift()
            return
        cur_x, cur_y = spot

        self._mouse.click(left + cur_x, top + cur_y)
        time.sleep(PLACE_UNIT_CLICK_SETTLE)
        if self._checkpoint(stop_event):
            self._release_quick_place_shift()
            return

        # max_placement_reached is optional (like nav_disband) -- a missing
        # image just means this check is silently skipped, not that the
        # block fails, since not everyone will have added it.
        try:
            limit_match = vision.find_image(hwnd, "max_placement_reached", threshold=MAX_PLACEMENT_THRESHOLD)
        except vision.TemplateNotFound:
            limit_match = None
        if limit_match is not None:
            self._log(f'[Macro] Place Unit "{name}": max placement limit reached -- skipping this block.')
            if not next_is_same_unit:
                self._release_quick_place_shift()
            return

        # Last of this quick-place run (or not part of one at all) --
        # release Shift now that the click that needed it is done.
        if not next_is_same_unit:
            self._release_quick_place_shift()

        if skip_verify:
            # No verify here -- see skip_verify's own comment above.
            # Position is still recorded, just without waiting on
            # unit_exist first; the white-pixel hit before the click is
            # what's trusted instead.
            reason = 'quick-place' if is_quick_place else 'Pre Start'
            self._log(f'[Macro] Place Unit "{name}": placed at ({cur_x}, {cur_y}) ({reason}).')
            if unit_ordinal is not None:
                self._placed_unit_positions[unit_ordinal] = (cur_x, cur_y)
            return

        # Verify: look for unit_exist FIRST, before clicking anything -- it
        # may already be visible with no extra input needed at all. Only if
        # it isn't there does this click once (not double-click, which risked
        # triggering something else entirely, like a sell/context menu) and
        # check again, up to PLACE_UNIT_VERIFY_ATTEMPTS times total.
        exists_match = None
        clicked_to_verify = False
        for verify_attempt in range(1, PLACE_UNIT_VERIFY_ATTEMPTS + 1):
            if self._checkpoint(stop_event):
                return
            if verify_attempt > 1:
                self._mouse.click(left + cur_x, top + cur_y)
                clicked_to_verify = True
                time.sleep(0.3)  # let the info panel actually render before checking for it
            try:
                exists_match = vision.wait_for_image(hwnd, "unit_exist", timeout=PLACE_UNIT_VERIFY_TIMEOUT)
            except vision.TemplateNotFound:
                exists_match = None
                break  # no unit_exist.png added -- retrying won't change that, stop wasting clicks
            if exists_match is not None:
                break
            self._log(f'[Macro] Place Unit "{name}": verify check {verify_attempt}/{PLACE_UNIT_VERIFY_ATTEMPTS} '
                       f'-- unit_exist not seen yet.')

        # Only reset the info panel if a verify click actually happened --
        # the plain search-first check above never opens anything, so there's
        # nothing to close if that's all it took.
        if clicked_to_verify:
            self._reset_unit_info_panel(hwnd)

        if exists_match is None:
            self._log(f'[Macro] Place Unit "{name}": placed at ({cur_x}, {cur_y}) but couldn\'t verify '
                       f'(no unit_exist match) -- add Assets/ui/unit_exist.png to enable this check.')
            return

        self._log(f'[Macro] Place Unit "{name}": verified placed at ({cur_x}, {cur_y}) '
                   f'(score {exists_match["score"]:.2f}).')
        if unit_ordinal is not None:
            self._placed_unit_positions[unit_ordinal] = (cur_x, cur_y)

    def _place_unit_retrying(self, hwnd, stop_event: threading.Event, left: int, top: int,
                               name: str, hotkey, orig_x: int, orig_y: int, block: dict,
                               unit_ordinal: int) -> None:
        """Place Unit with "Keep Placing" on: run the full select -> find
        spot -> click -> verify sequence and, if unit_exist doesn't confirm
        the unit landed, do the WHOLE thing again (re-select the unit, find
        a valid tile, click, re-verify) up to PLACE_RETRY_UNTIL_PLACED_
        ATTEMPTS times. Never a quick-place chain member (see the caller),
        so no Shift is ever held here."""
        if not hotkey:
            self._log(f'[Macro] Place Unit "{name}" has no hotkey set -- skipping this block.')
            return
        vk = keys.key_name_to_vk(hotkey)
        if vk is None:
            self._log(f'[Macro] Place Unit "{name}": hotkey "{hotkey}" isn\'t recognized -- skipping this block.')
            return

        n = PLACE_RETRY_UNTIL_PLACED_ATTEMPTS
        for attempt in range(1, n + 1):
            if self._checkpoint(stop_event):
                return
            # Select the unit fresh each attempt (Z-deselect first, as every
            # placement does).
            self._keyboard.tap(ord("Z"))
            time.sleep(0.1)
            self._keyboard.tap(vk)
            time.sleep(PLACE_HOTKEY_SETTLE)

            if block.get("ignoreHighlight"):
                self._mouse.move_to(left + orig_x, top + orig_y)
                time.sleep(PLACE_PIXEL_SEARCH_SETTLE)
                spot = (orig_x, orig_y)
            else:
                spot = self._find_valid_place_spot(hwnd, stop_event, left, top, orig_x, orig_y, name)
            if self._checkpoint(stop_event):
                return
            if spot is None:
                self._log(f'[Macro] Place Unit "{name}": no valid tile (attempt {attempt}/{n}) -- retrying.')
                continue
            cur_x, cur_y = spot

            self._mouse.click(left + cur_x, top + cur_y)
            time.sleep(PLACE_UNIT_CLICK_SETTLE)
            if self._checkpoint(stop_event):
                return

            try:
                limit_match = vision.find_image(hwnd, "max_placement_reached", threshold=MAX_PLACEMENT_THRESHOLD)
            except vision.TemplateNotFound:
                limit_match = None
            if limit_match is not None:
                self._log(f'[Macro] Place Unit "{name}": max placement limit reached -- skipping this block.')
                return

            # Verify: search for unit_exist first, click once to open the
            # info panel if not seen, up to PLACE_UNIT_VERIFY_ATTEMPTS.
            exists_match = None
            clicked_to_verify = False
            for va in range(1, PLACE_UNIT_VERIFY_ATTEMPTS + 1):
                if self._checkpoint(stop_event):
                    return
                if va > 1:
                    self._mouse.click(left + cur_x, top + cur_y)
                    clicked_to_verify = True
                    time.sleep(0.3)
                try:
                    exists_match = vision.wait_for_image(hwnd, "unit_exist", timeout=PLACE_UNIT_VERIFY_TIMEOUT)
                except vision.TemplateNotFound:
                    # No unit_exist.png -- "Keep Placing" has no stop signal
                    # without it, so trust this one placement rather than
                    # loop blindly re-placing a unit that may already be down.
                    if clicked_to_verify:
                        self._reset_unit_info_panel(hwnd)
                    self._log(f'[Macro] Place Unit "{name}": placed at ({cur_x}, {cur_y}) but "Keep Placing" '
                               f'needs Assets/ui/unit_exist.png to know when to stop -- treating as placed.')
                    if unit_ordinal is not None:
                        self._placed_unit_positions[unit_ordinal] = (cur_x, cur_y)
                    return
                if exists_match is not None:
                    break
            if clicked_to_verify:
                self._reset_unit_info_panel(hwnd)

            if exists_match is not None:
                self._log(f'[Macro] Place Unit "{name}": verified placed at ({cur_x}, {cur_y}) '
                           f'(score {exists_match["score"]:.2f}, attempt {attempt}/{n}).')
                if unit_ordinal is not None:
                    self._placed_unit_positions[unit_ordinal] = (cur_x, cur_y)
                return
            self._log(f'[Macro] Place Unit "{name}": not confirmed placed (attempt {attempt}/{n}) -- placing again.')

        self._log(f'[Macro] Place Unit "{name}": still not confirmed placed after {n} attempts -- giving up.')

    def _reset_unit_info_panel(self, hwnd) -> None:
        # Closes whatever info panel double-clicking a placed unit opened
        # (see the verify step above) -- Z first (same deselect pressed
        # before every placement), then a click on a near-empty corner of
        # the Roblox screen, (3, 3), well clear of any real UI so it can't
        # be mistaken for a live game action.
        self._keyboard.tap(ord("Z"))
        time.sleep(0.1)
        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        self._mouse.click(left + self._coords["unit_info_reset_x"], top + self._coords["unit_info_reset_y"])

    # Windows/Meta-style keys are blocked from the Setting block's custom
    # hotkey box -- letting a macro send these could minimize the game,
    # open the Start menu, or otherwise yank focus/input away from Roblox
    # entirely, which no in-game "setting" should ever be able to do.
    _BLACKLISTED_KEY_NAMES = {"win", "meta", "windows", "lwin", "rwin", "super", "cmd", "command"}

    _CUSTOM_KEY_DEFAULT_HOLD_MS = 500

    def _parse_custom_key_spec(self, text: str):
        """Parses a Setting block's custom-key text box (see
        _run_setting_block's "hotkey" kind) into (vk, hold_seconds).
        Supported syntax: "w" (a plain tap), "hold w" (held for
        _CUSTOM_KEY_DEFAULT_HOLD_MS), "hold w 800ms" (held for an explicit
        duration). Returns None for empty/blacklisted/unrecognized input so
        a bad spec is a logged skip, never a crash mid-run."""
        text = (text or "").strip().lower()
        if not text:
            return None
        parts = text.split()

        hold_seconds = None
        if parts[0] == "hold" and len(parts) >= 2:
            key_name = parts[1]
            hold_seconds = self._CUSTOM_KEY_DEFAULT_HOLD_MS / 1000.0
            if len(parts) >= 3 and parts[2].endswith("ms"):
                try:
                    hold_seconds = int(parts[2][:-2]) / 1000.0
                except ValueError:
                    pass  # keep the default rather than fail the whole spec over a bad number
        else:
            key_name = parts[0]

        if key_name in self._BLACKLISTED_KEY_NAMES:
            return None
        vk = keys.key_name_to_vk(key_name)
        if vk is None:
            return None
        return (vk, hold_seconds)

    def _run_send_key_tick(self, block: dict, block_num: int, phase_label: str = "Battle") -> None:
        """Send Key block: press (or hold) one keyboard key at this point in
        the sequence -- for any in-game key action no dedicated block covers
        (an ability, an interact key, a menu toggle). The key is captured in
        Creation (block["key"]); an optional hold_ms > 0 holds it that long
        instead of a quick tap. Blacklisted keys (Win/Meta, which could yank
        focus off Roblox) are refused, same as the Setting block's custom
        key. Never a movement key by design -- use a Walk block for pathing."""
        key_name = (block.get("key") or "").strip()
        params = block.get("params") or {}
        if not key_name:
            self._log(f'{phase_label} block #{block_num} (Send Key): no key set -- skipping.')
            return
        if key_name.lower() in self._BLACKLISTED_KEY_NAMES:
            self._log(f'{phase_label} block #{block_num} (Send Key): "{key_name}" is blacklisted -- skipping.')
            return
        vk = keys.key_name_to_vk(key_name)
        if vk is None:
            self._log(f'{phase_label} block #{block_num} (Send Key): key "{key_name}" isn\'t recognized -- skipping.')
            return
        try:
            hold_ms = max(0, int(params.get("hold_ms") or 0))
        except (TypeError, ValueError):
            hold_ms = 0
        if hold_ms > 0:
            self._log(f'{phase_label} block #{block_num} (Send Key): holding "{key_name}" for {hold_ms}ms.')
            self._keyboard.tap(vk, hold=hold_ms / 1000.0)
        else:
            self._log(f'{phase_label} block #{block_num} (Send Key): pressing "{key_name}".')
            self._keyboard.tap(vk)

    def _run_setting_block(self, hwnd, stop_event: threading.Event, block: dict, index: int) -> None:
        name = (block.get("params") or {}).get("name") or f"#{index}"
        kind = block.get("kind")
        value = block.get("value")

        if kind == "toggle":
            desired_on = str(value).lower() in ("on", "true", "1", "yes")
            self._set_status(action=f'Setting "{name}"...')
            search_box_pos = self._open_settings_search(hwnd, stop_event)
            if search_box_pos is None:
                self._log(f'[Macro] Setting "{name}": couldn\'t open Settings -- skipping.')
                return
            if self._checkpoint(stop_event):
                return
            self._search_and_set_toggle(hwnd, stop_event, search_box_pos, name, desired_on)
            if self._checkpoint(stop_event):
                return
            self._close_settings_if_open(hwnd, stop_event)
            return

        if kind == "hotkey":
            parsed = self._parse_custom_key_spec(value)
            if parsed is None:
                self._log(f'[Macro] Setting "{name}": custom key "{value}" is blacklisted or unrecognized -- '
                           f'skipping.')
                return
            vk, hold_seconds = parsed
            self._set_status(action=f'Setting "{name}"...')
            if hold_seconds is not None:
                self._log(f'[Macro] Setting "{name}": holding "{value}" for {hold_seconds * 1000:.0f}ms.')
                self._keyboard.tap(vk, hold=hold_seconds)
            else:
                self._log(f'[Macro] Setting "{name}": pressing "{value}".')
                self._keyboard.tap(vk)
            return

        self._log(f'[Macro] Setting "{name}" ({kind or "?"}) -- unsupported kind, skipping.')

