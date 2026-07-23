"""Macro Operation blocks: Pre Start and Battle block execution -- placement,
upgrades, sells, waits, walks, settings.

Split out of core/runner.py mechanically -- a mixin providing part of
MacroRunner's behavior (see core/runner.py, which composes the mixins).
Methods here run with MacroRunner's full self: shared state and helpers
(_log, _coords, _checkpoint, _click_found_image, ...) resolve normally.
"""
import threading
import time

from . import camera
from . import keys
from . import paths as walk_paths
from . import stage_select
from . import vision
from . import window as wm
from .runner_constants import *  # noqa: F401,F403 -- the shared constants namespace
from .runner_constants import _exp_green, _exp_green_loose, _exp_red  # underscore names, skipped by *


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
            return self._strip_auto_upgrade_for_expedition(blocks.get("battle") or [], task)
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
        return self._strip_auto_upgrade_for_expedition(legacy_battle, task)

    def _run_battle_blocks_tick(self, hwnd, stop_event: threading.Event, battle_blocks: list, first_repeat: bool,
                                  macro_name: str = None) -> None:
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
            if block.get("once") and not first_repeat:
                self._log(f'[Macro] Skipping Battle block #{self._battle_block_index + 1} -- '
                           f'marked "Once" and this isn\'t the first repeat.')
                self._battle_block_index += 1
                self._battle_block_state = {}
                continue

            btype = block.get("type")
            if btype == "upgrade_unit":
                done = self._run_upgrade_unit_tick(hwnd, stop_event, block, self._battle_block_index + 1)
            elif btype == "sell_unit":
                done = self._run_sell_unit_tick(hwnd, stop_event, block, self._battle_block_index + 1)
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
                self._last_unit_ordinal += 1
                left, top, _, _ = wm.get_window_rect_screen(hwnd)
                next_index = self._battle_block_index + 1
                next_block = battle_blocks[next_index] if next_index < len(battle_blocks) else None
                next_is_same_unit = bool(
                    next_block and next_block.get("type") == "place_unit"
                    and block.get("hotkey") and next_block.get("hotkey") == block.get("hotkey"))
                self._run_place_unit_block(hwnd, stop_event, left, top, block, self._battle_block_index + 1,
                                             macro_name, self._last_unit_ordinal,
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
        try:
            upgrade_match, found_name = vision.wait_for_image_any(
                hwnd, ("upgradeable", "not_upgradeable"), timeout=UPGRADE_PANEL_LOAD_TIMEOUT, stop_event=stop_event)
        except vision.TemplateNotFound:
            upgrade_match, found_name = None, None
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
            return state["remaining"] <= 0

        if not_upgrade_match is not None:
            self._log(f'{label}: not upgradeable yet (score {not_upgrade_match["score"]:.2f}) -- '
                       f'waiting {UPGRADE_RETRY_WAIT:.0f}s and retrying.')
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

    def _run_walk_block_tick(self, stop_event: threading.Event, block: dict, block_num: int) -> None:
        """One-shot: replays a recorded walk path -- the same core.paths
        record/load/replay system the pinned Pre Start Walk Path row
        already uses (see _run_prestart), just picked by name here instead
        of by map. Picks up wherever the player currently is; no position
        tracking needed, same as every other Battle block that just fires
        an action rather than needing to know where a unit was placed."""
        path_name = block.get("params", {}).get("path") or ""
        label = f'Battle block #{block_num} (Walk)'
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
        walk_paths.replay_events(events, self._keyboard, stop_event)
        self._log(f'{label}: walk finished.')

    def _run_wait_wave_tick(self, hwnd, block: dict, block_num: int) -> bool:
        """Waits until the current wave has reached OR already passed the
        configured target -- not exact equality, so a wave that ticks over
        between polls (or was already past target the first time this is
        checked) still counts as done instead of waiting forever for a
        number that will never be read again. Checked periodically (see
        WAIT_WAVE_POLL_INTERVAL), not every single Battle-tick poll --
        each OCR read is several real Tesseract subprocess spawns.
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

        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        try:
            from core.ocr import capture_region
            from core import wave as wave_module
            image = capture_region(left + WAVE_REGION[0], top + WAVE_REGION[1], WAVE_REGION[2], WAVE_REGION[3])
            current, maximum = wave_module.read_wave(image)
        except Exception as exc:
            self._log(f'{label}: OCR failed ({exc}) -- retrying in {WAIT_WAVE_POLL_INTERVAL:.0f}s.')
            state["next_check"] = time.time() + WAIT_WAVE_POLL_INTERVAL
            return False

        if current is None:
            self._log(f"{label}: couldn't read the wave counter -- retrying in {WAIT_WAVE_POLL_INTERVAL:.0f}s.")
            state["next_check"] = time.time() + WAIT_WAVE_POLL_INTERVAL
            return False

        if current >= target:
            self._log(f'{label}: wave {current}/{maximum} -- reached (or already past) target {target}.')
            return True

        self._log(f'{label}: wave {current}/{maximum}, waiting for {target}.')
        self._set_status(action=f"Waiting for wave {target} (currently {current})...")
        state["next_check"] = time.time() + WAIT_WAVE_POLL_INTERVAL
        return False

    def _run_auto_upgrade_unit_tick(self, hwnd, stop_event: threading.Event, block: dict, block_num: int) -> bool:
        """One-shot: click the unit, right-click "priority_upgrade" (found
        on its info panel) to open its priority menu, click the configured
        priority row (or Disable for "None"), then a reset click. Always
        "done" after one try -- setting a priority isn't a repeated action
        the way Upgrade Unit's clicks are."""
        label = f'Battle block #{block_num} (Auto Upgrade Unit)'
        pos = self._placed_unit_click_point(block, label)
        if pos is None:
            return True

        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        self._set_status(action="Setting auto-upgrade priority...")
        self._mouse.click(left + pos[0], top + pos[1])
        time.sleep(AUTO_UPGRADE_CLICK_SETTLE)
        if self._checkpoint(stop_event):
            return True

        try:
            priority_match, priority_name = vision.find_image_any(hwnd, PRIORITY_UPGRADE_IMAGE_NAMES)
        except vision.TemplateNotFound as exc:
            self._log(f'{label}: {exc}')
            return True
        if priority_match is None:
            self._log(f'{label}: "priority_upgrade" not found on the info panel -- skipping.')
            return True

        debug_path = self._debug_save(hwnd, priority_name, priority_match)
        suffix = f" Debug: {debug_path}" if debug_path else ""
        self._log(f'{label}: found "{priority_name}" (score {priority_match["score"]:.2f}) -- '
                   f'right-clicking it.{suffix}')
        vision.right_click_match(self._mouse, hwnd, priority_match)
        time.sleep(AUTO_UPGRADE_CLICK_SETTLE)
        if self._checkpoint(stop_event):
            return True

        priority = str(block.get("params", {}).get("priority") or "None")
        if priority == "None":
            # The Disable row sits one row-height below Priority 6 -- the
            # last of the 6 priority rows, not a 7th priority.
            row_index = 6
            self._log(f'{label}: disabling auto-upgrade for this unit.')
        else:
            try:
                row_index = int(priority) - 1
            except ValueError:
                row_index = 0
            self._log(f'{label}: setting priority {priority}.')
        # Row positions computed off priority_upgrade's OWN matched w/h
        # (see the constants' own comment) instead of a second set of fixed
        # coordinates -- self-scaling if the icon itself ever renders at a
        # different size.
        row_height = priority_match["h"] * AUTO_UPGRADE_PRIORITY_ROW_HEIGHT_MULT
        row_x = priority_match["cx"] + priority_match["w"] * AUTO_UPGRADE_PRIORITY_X_OFFSET_MULT
        first_row_y = priority_match["cy"] + priority_match["h"] * AUTO_UPGRADE_PRIORITY_FIRST_ROW_MULT
        row_y = first_row_y + row_index * row_height
        self._mouse.click(left + int(row_x), top + int(row_y))
        time.sleep(AUTO_UPGRADE_CLICK_SETTLE)
        if self._checkpoint(stop_event):
            return True

        self._mouse.click(left + self._coords["unit_info_reset_x"], top + self._coords["unit_info_reset_y"])
        return True

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

        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        self._log(f'[Macro] Running {len(prestart_blocks)} Pre Start block(s) from "{macro_name}"...')
        self._set_status(action=f'Running "{macro_name}" Pre Start blocks...')
        # Separate from the generic #i below -- this only counts place_unit
        # blocks, matching ui/app.js's listPlacedUnits() numbering (the #1,
        # #2, ... the Upgrade/Sell Unit pickers show), so a template mixing
        # place_unit and setting_change blocks still numbers its units the
        # same way the UI does. self._last_unit_ordinal (not a local var)
        # since Battle-phase place_unit blocks (see _run_battle_blocks_tick)
        # continue this same count after Pre Start's blocks are done.
        self._last_unit_ordinal = 0
        self._quick_place_shift_down = False
        try:
            for i, block in enumerate(prestart_blocks, start=1):
                if self._checkpoint(stop_event):
                    return
                if block.get("type") == "place_unit":
                    self._last_unit_ordinal += 1
                if block.get("once") and not first_repeat:
                    # "Once" (see the block's Once chip in Creation) means only
                    # the task's FIRST entry into this stage runs it -- e.g. a
                    # starter placement that shouldn't be re-placed (and would
                    # just get rejected as a duplicate/waste a click) on every
                    # repeat of the same stage.
                    self._log(f'[Macro] Skipping block #{i} -- marked "Once" and this isn\'t the first repeat.')
                    continue
                btype = block.get("type")
                if btype == "place_unit":
                    next_block = prestart_blocks[i] if i < len(prestart_blocks) else None
                    next_is_same_unit = bool(
                        next_block and next_block.get("type") == "place_unit"
                        and block.get("hotkey") and next_block.get("hotkey") == block.get("hotkey"))
                    self._run_place_unit_block(hwnd, stop_event, left, top, block, i, macro_name,
                                                 self._last_unit_ordinal, next_is_same_unit=next_is_same_unit,
                                                 verify=False)
                elif btype == "setting_change":
                    self._run_setting_block(hwnd, stop_event, block, i)
                elif btype == "auto_upgrade_unit":
                    self._run_auto_upgrade_unit_tick(hwnd, stop_event, block, i)
                elif btype == "walk_path":
                    self._run_walk_path_block(hwnd, stop_event, task, default_walk_paths or {}, block, first_repeat)
                elif btype == "click":
                    self._run_click_block(hwnd, stop_event, block, i, phase_label="Pre Start")
                elif btype == "wait_ms":
                    self._run_wait_ms_tick(stop_event, block, i, phase_label="Pre Start")
                else:
                    self._log(f'[Macro] Skipping block #{i} ("{btype}") -- not runnable in Pre Start yet.')
                time.sleep(0.2)  # brief gap between blocks so the game UI can settle
        finally:
            # Safety net -- a "Once"-skipped block right after the last
            # quick-place placement (or the list just ending mid-chain)
            # would otherwise leave Shift stuck down for good, since
            # next_is_same_unit's own block never actually runs to release
            # it. Whatever else happens, Shift never leaves this function
            # still held.
            self._release_quick_place_shift()

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
            # A Raid map's Acts can need different walks (e.g. Spirit City
            # Act 3 -- see ACT_ORDER) -- looked up as "<map> Act<n>" first,
            # falling back to the plain map-name entry other Acts/Story
            # share, so only the Acts that actually need a different walk
            # need their own default_walk_paths entry.
            path_name = None
            if map_name:
                if task.get("mode") == "raid":
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
        walk_paths.replay_events(events, self._keyboard, stop_event)
        self._log("[Macro] Walk finished.")

    def _release_quick_place_shift(self) -> None:
        if self._quick_place_shift_down:
            self._keyboard.key_up(keys.VK_SHIFT)
            self._quick_place_shift_down = False

    def _scan_place_search_box(self, left: int, top: int, orig_x: int, orig_y: int):
        """One capture of the PLACE_SEARCH_BOX_SIZE x PLACE_SEARCH_BOX_SIZE
        region centered on (orig_x, orig_y) -- window-client coords --
        scanned in memory for a pixel at/near 0xffffff (white, within
        PLACE_VALID_PIXEL_TOLERANCE per channel). Returns the (dx, dy)
        offset of whichever valid pixel is CLOSEST to the center, or None
        if nothing valid was found anywhere in the box."""
        import numpy as np
        from core.ocr import capture_region
        half = PLACE_SEARCH_BOX_SIZE // 2
        patch = capture_region(left + orig_x - half, top + orig_y - half,
                                 PLACE_SEARCH_BOX_SIZE, PLACE_SEARCH_BOX_SIZE)
        b, g, r = patch[:, :, 0].astype(int), patch[:, :, 1].astype(int), patch[:, :, 2].astype(int)
        floor = 255 - PLACE_VALID_PIXEL_TOLERANCE
        valid_mask = (r >= floor) & (g >= floor) & (b >= floor)
        ys, xs = np.where(valid_mask)
        if len(xs) == 0:
            return None
        dists = (xs - half) ** 2 + (ys - half) ** 2
        best = int(np.argmin(dists))
        return int(xs[best]) - half, int(ys[best]) - half

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
            found = self._scan_place_search_box(left, top, orig_x, orig_y)
            if found is not None:
                dx, dy = found
                cx, cy = orig_x + dx, orig_y + dy
                if (dx, dy) != (0, 0):
                    self._mouse.move_to(left + cx, top + cy)
                    time.sleep(PLACE_PIXEL_SEARCH_SETTLE)
                    self._log(f'[Macro] Place Unit "{name}": aligned to a valid tile at offset ({dx}, {dy}).')
                return cx, cy
            if time.time() >= deadline:
                return None
            wx, wy = PLACE_SEARCH_WIGGLE_OFFSETS[wiggle_idx % len(PLACE_SEARCH_WIGGLE_OFFSETS)]
            self._mouse.nudge(wx, wy)
            wiggle_idx += 1
            time.sleep(PLACE_PIXEL_SEARCH_SETTLE)

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
            self._log(f'[Macro] Place Unit "{name}": no valid (white) tile found in the '
                       f'{PLACE_SEARCH_BOX_SIZE}x{PLACE_SEARCH_BOX_SIZE} box around ({orig_x}, {orig_y}) -- giving up.')
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

