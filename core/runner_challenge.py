"""Regular Challenge: readiness/rotation windows, the pre-queue pass, entering and
playing the 3 stage slots.

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


class ChallengeOps:
    def _detect_current_challenge_map(self, hwnd) -> str:
        """Regular Challenge is Story's own flow with the game picking a
        random one of CHALLENGE_STORY_MAPS for you -- this is the "which one
        did it land on" check, tried against each map's reference image
        (Assets/ui/<map>.png, a different purpose from Assets/maps/<map>.png's
        map-CARD search) in turn. Returns the matched map name, or None if
        none of them were found (not yet on a recognizable Challenge screen,
        or the wrong screen entirely)."""
        try:
            match, map_name = vision.find_image_any(hwnd, CHALLENGE_STORY_MAPS)
        except vision.TemplateNotFound:
            return None
        if match is not None:
            debug_path = self._debug_save(hwnd, map_name, match)
            suffix = f" Debug: {debug_path}" if debug_path else ""
            self._log(f'[Macro] Challenge map detected: "{map_name}" (score {match["score"]:.2f}).{suffix}')
            return map_name
        return None

    def _challenge_has_ready_stage(self) -> bool:
        """Quick side-effect-free check for whether Challenge automation
        has at least one enabled, not-yet-capped stage slot ready to run
        right now -- used by _run_task's repeat loop to decide whether to
        pause a task's repeats and go run Challenge before continuing
        (see challenge_wants_in there), not just once at the very start of
        a Start press. Same enabled/cap/ready checks _run_challenges itself
        makes per slot, just without actually running anything."""
        if self._get_challenge_settings is None:
            return False
        try:
            challenge = self._get_challenge_settings()
        except Exception:
            return False
        if not challenge.get("enabled"):
            return False
        cap = challenge.get("cap", 0)
        for slot in CHALLENGE_STAGE_SLOTS:
            info = challenge.get("stages", {}).get(slot) or {}
            if not info.get("enabled"):
                continue
            if cap and info.get("count", 0) >= cap:
                continue
            if info.get("ready"):
                return True
        return False

    def _run_challenges(self, hwnd, stop_event: threading.Event, coords: dict, scroll_power: int,
                          scroll_nudges: int, default_walk_paths: dict, reward_region: dict, stats_region: dict,
                          webhook: dict) -> None:
        """Runs every ready (enabled, under today's cap, off its own
        cooldown) Regular Challenge stage slot once each, in #1/#2/#3
        order, then returns -- called once before the Task Queue ever
        starts (see _run), AND again between repeats of an in-progress
        task whenever _challenge_has_ready_stage says a slot's ready (see
        _run_task's repeat loop), not just that one time at the start
        anymore. Challenge is Story's own flow with the
        game picking a random one of CHALLENGE_STORY_MAPS for you instead
        of you picking it, so the actual battle (Pre Start, Start Game,
        Victory/Defeat, reward reading) reuses _play_one_match/
        _handle_match_result unchanged via a synthetic Story-shaped task --
        see _run_one_challenge_stage."""
        if self._get_challenge_settings is None:
            return
        try:
            challenge = self._get_challenge_settings()
        except Exception as exc:
            self._log(f"[Macro] Couldn't read Challenge settings: {exc}")
            return
        if not challenge.get("enabled"):
            return

        self._log("[Macro] Challenge is enabled -- running any ready stage(s) before the Task Queue...")
        cap = challenge.get("cap", 0)
        for slot in CHALLENGE_STAGE_SLOTS:
            if self._checkpoint(stop_event):
                return
            # Re-fetched every slot -- a stage just played updates its own
            # count/cooldown, and this whole pass can span several minutes.
            try:
                challenge = self._get_challenge_settings()
            except Exception as exc:
                self._log(f"[Macro] Couldn't read Challenge settings: {exc}")
                return
            info = challenge.get("stages", {}).get(slot) or {}
            if not info.get("enabled"):
                continue
            if cap and info.get("count", 0) >= cap:
                self._log(f'[Macro] Challenge #{slot} is at today\'s cap ({cap}) -- skipping.')
                continue
            if not info.get("ready"):
                # Already played this slot since the current :00/:30 window
                # opened -- "ready" is computed by get_challenge_settings
                # against that single fixed clock, same for all 3 slots.
                self._log(f'[Macro] Challenge #{slot} already played this window -- skipping.')
                continue

            play_mode = challenge.get("play_mode") or "solo"
            result = self._run_one_challenge_stage(hwnd, stop_event, slot, play_mode, challenge, coords,
                                                     scroll_power, scroll_nudges, default_walk_paths,
                                                     reward_region, stats_region, webhook)
            if self._checkpoint(stop_event):
                return
            if result == "win":
                self._mark_challenge_stage_played(slot)
            elif result == "loss":
                # A loss starts the same until-next-window cooldown a win
                # does -- the slot's rotated-in stage won't have changed
                # within this window, so an immediate retry just feeds it
                # the same losing matchup again -- but count_play=False
                # keeps it from eating one of the day's capped plays the
                # way a real completion does. The match already ran its
                # normal Leave Stage + Return to Lobby (see
                # _handle_match_result), so there's nothing left to
                # recover from here.
                self._mark_challenge_stage_played(slot, False)
                self._log(f'[Macro] Challenge #{slot} was a loss -- resting it until the next '
                           f':00/:30 window (daily count not used).')
            else:
                self._log(f'[Macro] Challenge #{slot} didn\'t complete cleanly -- recovering to the lobby.')
                # A quick, targeted Leave Stage + Return to Lobby is tried
                # FIRST, on every failed slot (not just handled differently
                # for the first one) -- most failures here still have Leave
                # Stage sitting right there on screen (a stuck detection
                # mid-battle, a follow-up click that never showed up), and
                # clicking straight through it is faster and more reliable
                # than immediately reaching for the heavier generic
                # _recover_to_lobby (menu-backing-out, map-search-failure
                # handling, ...) that's built for recovering from states
                # Leave Stage doesn't even apply to. Only falls through to
                # that heavier recovery if Leave Stage genuinely isn't there.
                if not self._click_and_verify_gone(hwnd, stop_event, "leave_stage", NAV_CLICK_TIMEOUT):
                    if not self._recover_to_lobby(hwnd, stop_event):
                        return
                else:
                    self._click_return_to_lobby_if_found(hwnd, stop_event)

        self._log("[Macro] Challenge pass finished -- moving on to the Task Queue.")

    def _run_one_challenge_stage(self, hwnd, stop_event: threading.Event, slot: str, play_mode: str,
                                   challenge: dict, coords: dict, scroll_power: int, scroll_nudges: int,
                                   default_walk_paths: dict, reward_region: dict, stats_region: dict,
                                   webhook: dict) -> str:
        """Returns "win", "loss", or None -- None covers both a genuine
        technical failure (never got into the stage, map never recognized,
        etc.) AND the run being stopped mid-way, same as _play_one_match's
        own result convention. Callers (_run_challenges) put the slot on
        its until-next-window cooldown for BOTH "win" and "loss" (a loss
        just doesn't consume a daily-cap count -- see
        mark_challenge_stage_played's count_play); only None leaves the
        slot ready, so a technical failure can be retried this window."""
        self._log(f"[Macro] Challenge #{slot}: entering ({play_mode})...")
        self._set_status(current_task=f"Challenge #{slot}", map="-", action="Entering Challenge...",
                          mode="challenge", stage="-", difficulty="-", play_mode=play_mode, macro="-")
        if not self._enter_challenge_stage(hwnd, stop_event, slot, play_mode, coords, webhook):
            return None
        if self._checkpoint(stop_event):
            return None

        self._log(f"[Macro] Challenge #{slot}: identifying the map...")
        self._set_status(action="Identifying Challenge map...")
        deadline = time.time() + CHALLENGE_MAP_DETECT_TIMEOUT
        detected_map = None
        while time.time() < deadline:
            if self._checkpoint(stop_event):
                return None
            detected_map = self._detect_current_challenge_map(hwnd)
            if detected_map:
                break
            time.sleep(MATCH_RESULT_POLL_INTERVAL)
        if not detected_map:
            self._log(f"[Macro] Challenge #{slot}: never recognized a map -- stopping.")
            return None

        macro_name = (challenge.get("maps", {}).get(detected_map) or {}).get("macro") or ""
        if macro_name:
            self._log(f'[Macro] Challenge #{slot} landed on "{detected_map}" -- running "{macro_name}".')
        else:
            self._log(f'[Macro] Challenge #{slot} landed on "{detected_map}" -- no Macro Operation assigned for it.')

        # mode="story" (not "challenge") deliberately -- this reuses the
        # EXACT SAME Pre Start/Start Game/Victory-Defeat pipeline a real
        # Story task uses (see _play_one_match/_handle_match_result), since
        # that's genuinely what Challenge's own battle is. is_challenge is
        # the marker other code checks when it actually needs to tell the
        # two apart (see _log_expected_rewards -- Challenge isn't in
        # stage_data.json under this map's Story entry, so that reference-
        # reward lookup would otherwise silently show the wrong data).
        task = {
            "mode": "story", "is_challenge": True, "map": detected_map, "difficulty": "Normal",
            "macro": macro_name, "play_mode": play_mode, "repeat": 1, "team": "", "equipment": "include",
        }
        self._set_status(map=detected_map, action="Battle...", difficulty=task["difficulty"], macro=macro_name or "-")
        battle_started = time.time()
        result = self._play_one_match(hwnd, stop_event, task, default_walk_paths, first_repeat=True,
                                        webhook=webhook)
        if result is None:
            return None
        duration = self._format_duration(time.time() - battle_started)

        # Challenge always leaves + returns to lobby afterward (repeat=
        # False) -- there's no "Repeat Stage" concept here, the next
        # attempt (if another slot is still ready) goes through the full
        # Challenge -> stage-slot navigation again, not a quick requeue.
        if not self._handle_match_result(hwnd, stop_event, task, result, duration, reward_region, stats_region,
                                           webhook, repeat=False):
            return None
        return None if self._checkpoint(stop_event) else result

    def _enter_challenge_stage(self, hwnd, stop_event: threading.Event, slot: str, play_mode: str, coords: dict,
                                 webhook: dict) -> bool:
        """Lobby -> Play -> Challenge -> stage slot #1/#2/#3 -> Solo/
        Matchmaking entry (through teleport-in) -- Regular Challenge's
        equivalent of _run_task_setup, except there's no map/difficulty to
        pick (the game assigns both at random), just a fixed-position
        stage row and a screen-load confirmation."""
        if not self._ensure_lobby(hwnd, stop_event):
            return False
        if self._checkpoint(stop_event):
            return False
        if not self._click_play(hwnd, stop_event):
            return False
        if self._checkpoint(stop_event):
            return False
        if not self._click_gamemode(hwnd, stop_event, "challenge"):
            return False
        if self._checkpoint(stop_event):
            return False

        self._log("[Macro] Waiting for the Challenge screen to load...")
        self._set_status(action="Waiting for Challenge screen...")
        try:
            loaded_match = vision.wait_for_image(
                hwnd, "challenge_loaded", timeout=CHALLENGE_SCREEN_TIMEOUT, stop_event=stop_event)
        except vision.TemplateNotFound as exc:
            self._log(f"[Macro] Can't confirm the Challenge screen loaded: {exc}")
            return False
        if loaded_match is None:
            if not stop_event.is_set():
                self._log(f'[Macro] "challenge_loaded" not found within {CHALLENGE_SCREEN_TIMEOUT:.0f}s -- '
                           f"can't confirm the Challenge screen opened, stopping.")
            return False

        if slot not in CHALLENGE_STAGE_SLOTS:
            self._log(f'[Macro] Unknown Challenge stage slot "{slot}".')
            return False
        x, y = self._cxy(f"challenge_stage_{slot}")
        self._log(f'[Macro] Challenge screen loaded -- clicking stage slot #{slot} at ({x}, {y}).')
        self._set_status(action=f"Clicking Challenge #{slot}...")
        left, top, _, _ = wm.get_window_rect_screen(hwnd)
        self._mouse.click(left + x, top + y)
        if self._checkpoint(stop_event):
            return False

        challenge_task_stub = {"mode": "challenge", "is_challenge": True}
        if play_mode == "matchmaking":
            if not self._click_enter_matchmaking(hwnd, stop_event, coords, "challenge"):
                return False
            if self._checkpoint(stop_event):
                return False
            self._log(f"[Macro] Waiting for the lobby to fill (up to {MATCHMAKING_TELEPORT_TIMEOUT / 60:.0f} "
                       f"min) -- matchmaking has to find real players before it teleports in.")
            if not self._wait_teleport_in(hwnd, stop_event, webhook, challenge_task_stub,
                                            timeout=MATCHMAKING_TELEPORT_TIMEOUT):
                return False
        else:
            self._set_status(action="Clicking Select Stage...")
            if not self._click_and_verify_gone(hwnd, stop_event, "chal_select", CHALLENGE_SCREEN_TIMEOUT):
                self._log('[Macro] "chal_select" never showed up -- stopping.')
                return False
            if self._checkpoint(stop_event):
                return False
            self._log("[Macro] Solo mode -- clicking Start.")
            if not self._click_start_and_wait_teleport(hwnd, stop_event, webhook, challenge_task_stub):
                return False
        return not self._checkpoint(stop_event)

