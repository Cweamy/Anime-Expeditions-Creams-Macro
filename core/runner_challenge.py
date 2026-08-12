"""Regular Challenge: readiness/rotation windows, the pre-queue pass, entering and
playing the 3 stage slots.

Split out of core/runner.py mechanically -- a mixin providing part of
MacroRunner's behavior (see core/runner.py, which composes the mixins).
Methods here run with MacroRunner's full self: shared state and helpers
(_log, _coords, _checkpoint, _click_found_image, ...) resolve normally.
"""
import difflib
import re
import threading
import time

import cv2

from . import ocr
from . import vision
from . import ocr_windows
from . import window as wm
from .runner_constants import *  # noqa: F401,F403 -- the shared constants namespace


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
        return self._detect_challenge_map_ocr(hwnd)

    def _challenge_map_ocr_crops(self, frame):
        """Daily Challenge map label crops, HUD-anchored first and fixed relative fallback second."""
        crops = []
        try:
            hud_match = vision.find_in_gray_multiscale(
                cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), "daily_challenge_hud")
        except vision.TemplateNotFound:
            hud_match = None
        if hud_match is not None:
            x = max(0, hud_match["x"] + hud_match["w"] - 5)
            y = max(0, hud_match["y"] - 7)
            crop = frame[y:min(frame.shape[0], y + 43), x:frame.shape[1]]
            if crop.size:
                crops.append(crop)

        h, w = frame.shape[:2]
        x1 = max(0, int(w * 0.58))
        y1 = max(0, int(h * 0.42))
        x2 = min(w, int(w * 0.98))
        y2 = min(h, int(h * 0.52))
        fallback = frame[y1:y2, x1:x2]
        if fallback.size:
            crops.append(fallback)
        return crops

    def _detect_challenge_map_ocr(self, hwnd) -> str:
        """Fallback for the tiny Daily Challenge map label shown in-game."""
        frame = vision.capture_game_bgr(hwnd)
        if frame is None:
            return None

        aliases = CHALLENGE_MAP_OCR_ALIASES

        try:
            pytesseract = ocr.get_pytesseract()
        except ocr.TesseractNotAvailable:
            pytesseract = None

        for crop in ChallengeOps._challenge_map_ocr_crops(self, frame):
            # The raw glyphs are only around 10px tall. Color upscaling preserves
            # their white fill and dark outline better than a global threshold.
            candidates = [
                cv2.resize(crop, None, fx=8, fy=8, interpolation=cv2.INTER_CUBIC),
                cv2.resize(crop, None, fx=8, fy=8, interpolation=cv2.INTER_LANCZOS4),
            ]
            candidates.extend(ocr.candidate_masks(crop, upscale=8))
            texts = [ocr.ocr_mask(pytesseract, candidate, "--psm 7") for candidate in candidates]
            for text in texts:
                # The label reads "<Map> - Act N", so every read carries the
                # same boilerplate word alongside the part that identifies the
                # map. Left in, it competes for the best score -- "act" sits
                # about as close to "east" as a garbled "Tornb" does to "tomb",
                # which collapses the runner-up margin below and rejects a read
                # that was perfectly legible. Score only the words that can
                # actually name a map.
                tokens = [t for t in re.findall(r"[a-z]+", text.lower())
                          if t not in CHALLENGE_MAP_OCR_STOPWORDS]
                scores = sorted(
                    (
                        max((difflib.SequenceMatcher(None, alias, token).ratio() for token in tokens), default=0),
                        map_name,
                    )
                    for map_name, alias in aliases.items()
                )
                best_score, best_map = scores[-1]
                runner_up = scores[-2][0]
                if best_score >= 0.65 and best_score - runner_up >= 0.12:
                    self._log(
                        f'[Macro] Challenge map OCR: "{text.strip()}" -> "{best_map}" (score {best_score:.2f}).')
                    return best_map
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
        daily = challenge.get("daily") or {}
        if daily.get("enabled") and daily.get("ready"):
            return True
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

    def _run_challenges(self, hwnd, stop_event: threading.Event, coords: dict,
                          default_walk_paths: dict, webhook: dict) -> None:
        """Runs a ready Daily Challenge first, then every ready Regular
        Challenge stage slot once each in #1/#2/#3 order. Called before
        the Task Queue ever
        starts (see _run), AND again between repeats of an in-progress
        task whenever _challenge_has_ready_stage says a slot's ready (see
        _run_task's repeat loop), not just that one time at the start
        anymore. Challenge is Story's own flow with the
        game picking a random one of CHALLENGE_STORY_MAPS for you instead
        of you picking it, so the actual battle (Pre Start, Start Game,
        Victory/Defeat) reuses _play_one_match/_handle_match_result
        unchanged via a synthetic Story-shaped task -- see
        _run_one_challenge_stage."""
        if self._get_challenge_settings is None:
            return
        try:
            challenge = self._get_challenge_settings()
        except Exception as exc:
            self._log(f"[Macro] Couldn't read Challenge settings: {exc}")
            return
        daily = challenge.get("daily") or {}
        if not challenge.get("enabled") and not daily.get("enabled"):
            return

        self._log("[Macro] Challenge is enabled -- running any ready stage(s) before the Task Queue...")
        if daily.get("enabled") and daily.get("ready"):
            play_mode = challenge.get("play_mode") or "solo"
            result = self._run_one_daily_challenge(
                hwnd, stop_event, play_mode, challenge, coords, default_walk_paths, webhook)
            if self._checkpoint(stop_event):
                return
            if result in ("win", "unavailable"):
                # The gray Unavailable state is the game's source of truth:
                # it means this account cannot claim Daily Challenge again
                # until the shared daily reset, even if local state was lost.
                self._mark_challenge_stage_played("daily")
            elif result == "loss":
                self._log("[Macro] Daily Challenge was a loss -- leaving it ready for another attempt today.")
            else:
                self._log("[Macro] Daily Challenge didn't complete cleanly -- recovering to the lobby.")
                if not self._recover_failed_challenge(hwnd, stop_event):
                    return

        # Daily can be enabled independently of the rotating Regular slots.
        if not challenge.get("enabled"):
            self._log("[Macro] Challenge pass finished -- moving on to the Task Queue.")
            return

        cap = challenge.get("cap", 0)
        # Freeze this pass's ordered work before starting it. All regular
        # slots share one :00/:30 readiness window; repeatedly deciding the
        # work between battles could otherwise let a boundary or settings
        # refresh turn a 1 -> 2 -> 3 interruption into several one-slot
        # interruptions. Once we leave a task for Challenge, finish every
        # slot that was eligible at that point, in numeric order, before
        # returning to the task.
        pending_slots = []
        for slot in CHALLENGE_STAGE_SLOTS:
            info = challenge.get("stages", {}).get(slot) or {}
            if not info.get("enabled"):
                continue
            if cap and info.get("count", 0) >= cap:
                self._log(f'[Macro] Challenge #{slot} is at today\'s cap ({cap}) -- skipping.')
                continue
            if not info.get("ready"):
                self._log(f'[Macro] Challenge #{slot} already played this window -- skipping.')
                continue
            pending_slots.append(slot)

        for slot in pending_slots:
            if self._checkpoint(stop_event):
                return
            # Refresh map/mode configuration, but do not re-decide the slot
            # list: completing 1 must lead to 2 then 3 from this same pass.
            try:
                challenge = self._get_challenge_settings()
            except Exception as exc:
                self._log(f"[Macro] Couldn't read Challenge settings: {exc}")
                return

            play_mode = challenge.get("play_mode") or "solo"
            result = self._run_one_challenge_stage(hwnd, stop_event, slot, play_mode, challenge, coords,
                                                     default_walk_paths, webhook)
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
                if not self._recover_failed_challenge(hwnd, stop_event):
                    return

        self._log("[Macro] Challenge pass finished -- moving on to the Task Queue.")

    def _recover_failed_challenge(self, hwnd, stop_event: threading.Event) -> bool:
        """Prefer the direct stage exit, then fall back to generic recovery."""
        if not self._click_and_verify_gone(
                hwnd, stop_event, "leave_stage", NAV_CLICK_TIMEOUT, success_name="return"):
            return self._recover_to_lobby(hwnd, stop_event)
        self._click_return_to_lobby_if_found(hwnd, stop_event)
        return not self._checkpoint(stop_event)

    def _run_one_challenge_stage(self, hwnd, stop_event: threading.Event, slot: str, play_mode: str,
                                   challenge: dict, coords: dict, default_walk_paths: dict,
                                   webhook: dict) -> str:
        """Returns "win", "loss", or None -- None covers both a genuine
        technical failure (never got into the stage, map never recognized,
        etc.) AND the run being stopped mid-way, same as _play_one_match's
        own result convention. Callers (_run_challenges) put the slot on
        its until-next-window cooldown for BOTH "win" and "loss" (a loss
        just doesn't consume a daily-cap count -- see
        mark_challenge_stage_played's count_play); only None leaves the
        slot ready, so a technical failure can be retried this window."""
        progress_task = {
            "mode": "challenge", "map": f"Challenge #{slot}",
            "stage": str(slot), "play_mode": play_mode,
        }
        self._send_progress_webhook(
            webhook,
            progress_task,
            f"Challenge #{slot} Started",
            f"Starting Challenge #{slot} ({play_mode}).",
            0x5865F2,
            extra_fields=[{"name": "Play Mode", "value": play_mode, "inline": True}],
            current_action=f"Challenge #{slot} -- entering ({play_mode})",
            next_phase="Identify the assigned map, then start the battle",
        )
        self._log(f"[Macro] Challenge #{slot}: entering ({play_mode})...")
        self._set_status(current_task=f"Challenge #{slot}", map="-", action="Entering Challenge...",
                          mode="challenge", stage="-", difficulty="-", play_mode=play_mode, macro="-")
        result = None
        try:
            if not self._enter_challenge_stage(hwnd, stop_event, slot, play_mode, coords, webhook):
                return None
            if self._checkpoint(stop_event):
                return None
            result = self._run_challenge_battle(
                hwnd, stop_event, f"Challenge #{slot}", play_mode, challenge, default_walk_paths, webhook)
            return result
        finally:
            self._send_challenge_progress_finished(
                webhook, progress_task, f"Challenge #{slot}", play_mode, result, stop_event)

    def _run_one_daily_challenge(self, hwnd, stop_event: threading.Event, play_mode: str,
                                  challenge: dict, coords: dict, default_walk_paths: dict,
                                  webhook: dict) -> str:
        progress_task = {
            "mode": "challenge", "map": "Daily Challenge",
            "stage": "Daily", "play_mode": play_mode,
        }
        self._send_progress_webhook(
            webhook,
            progress_task,
            "Daily Challenge Started",
            f"Starting Daily Challenge ({play_mode}).",
            0x5865F2,
            extra_fields=[{"name": "Play Mode", "value": play_mode, "inline": True}],
            current_action=f"Daily Challenge -- entering ({play_mode})",
            next_phase="Identify the assigned map, then start the battle",
        )
        self._log(f"[Macro] Daily Challenge: entering ({play_mode})...")
        self._set_status(current_task="Daily Challenge", map="-", action="Entering Daily Challenge...",
                          mode="challenge", stage="Daily", difficulty="-", play_mode=play_mode, macro="-")
        result = None
        try:
            entry = self._enter_daily_challenge_stage(hwnd, stop_event, play_mode, coords, webhook)
            if entry != "entered":
                result = entry
                return entry
            if self._checkpoint(stop_event):
                return None
            result = self._run_challenge_battle(
                hwnd, stop_event, "Daily Challenge", play_mode, challenge, default_walk_paths, webhook)
            return result
        finally:
            self._send_challenge_progress_finished(
                webhook, progress_task, "Daily Challenge", play_mode, result, stop_event)

    def _send_challenge_progress_finished(self, webhook: dict, task: dict, label: str,
                                           play_mode: str, result: str,
                                           stop_event: threading.Event) -> None:
        if result == "win":
            status, color = "Victory", 0x3FBF6F
        elif result == "loss":
            status, color = "Defeat", 0xE05A6D
        elif result == "unavailable":
            status, color = "Unavailable", 0xE8935A
        elif stop_event.is_set():
            status, color = "Stopped", 0xE8935A
        else:
            status, color = "Failed", 0xE05A6D
        self._send_progress_webhook(
            webhook,
            task,
            f"{label} Finished",
            f"{label} finished: **{status}**.",
            color,
            extra_fields=[
                {"name": "Status", "value": status, "inline": True},
                {"name": "Play Mode", "value": play_mode, "inline": True},
            ],
            current_action=f"{label} -- {status}",
            next_phase=self._next_challenge_progress(),
        )

    def _run_challenge_battle(self, hwnd, stop_event: threading.Event, label: str, play_mode: str,
                               challenge: dict, default_walk_paths: dict, webhook: dict) -> str:
        """Identify the assigned Story map and run the shared battle flow."""
        self._log(f"[Macro] {label}: identifying the map...")
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
            self._log(f"[Macro] {label}: never recognized a map -- stopping.")
            return None

        macro_name = (challenge.get("maps", {}).get(detected_map) or {}).get("macro") or ""
        if macro_name:
            self._log(f'[Macro] {label} landed on "{detected_map}" -- running "{macro_name}".')
        else:
            self._log(f'[Macro] {label} landed on "{detected_map}" -- no Macro Operation assigned for it.')

        # mode="story" (not "challenge") deliberately -- this reuses the
        # EXACT SAME Pre Start/Start Game/Victory-Defeat pipeline a real
        # Story task uses (see _play_one_match/_handle_match_result), since
        # that's genuinely what Challenge's own battle is. is_challenge is
        # the marker other code checks when it actually needs to tell the
        # two apart.
        is_daily = label == "Daily Challenge"
        task = {
            "mode": "story", "is_challenge": True, "is_daily_challenge": is_daily,
            "map": detected_map, "difficulty": "Hard" if is_daily else "Normal",
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
        if not self._handle_match_result(hwnd, stop_event, task, result, duration, webhook, repeat=False):
            return None
        return None if self._checkpoint(stop_event) else result

    def _enter_challenge_stage(self, hwnd, stop_event: threading.Event, slot: str, play_mode: str, coords: dict,
                                 webhook: dict) -> bool:
        """Lobby -> Play -> Challenge -> stage slot #1/#2/#3 -> Solo/
        Matchmaking entry (through teleport-in) -- Regular Challenge's
        equivalent of _run_task_setup, except there's no map/difficulty to
        pick (the game assigns both at random), just a fixed-position
        stage row and a screen-load confirmation."""
        if not self._open_challenge_screen(hwnd, stop_event):
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
        return self._enter_selected_challenge(hwnd, stop_event, play_mode, coords, webhook, daily=False)

    def _enter_daily_challenge_stage(self, hwnd, stop_event: threading.Event, play_mode: str,
                                      coords: dict, webhook: dict) -> str:
        """Enter Daily Challenge, or report its gray unavailable state."""
        if not self._open_challenge_screen(hwnd, stop_event):
            return None
        try:
            unavailable = vision.find_image(hwnd, "daily_challenge_unavailable", threshold=0.75)
        except vision.TemplateNotFound as exc:
            self._log(f"[Macro] Can't check Daily Challenge availability: {exc}")
            return None
        if unavailable is not None:
            self._log(
                f'[Macro] Daily Challenge is unavailable for this game day '
                f'(score {unavailable["score"]:.2f}) -- skipping.')
            # We opened a menu but did not enter a stage; return to the lobby
            # before Regular Challenge or the Task Queue continues.
            return "unavailable" if self._recover_to_lobby(hwnd, stop_event) else None

        self._set_status(action="Clicking Daily Challenge...")
        avail_match = self._click_found_image(
            hwnd, "daily_challenge_available", CHALLENGE_SCREEN_TIMEOUT, stop_event, threshold=0.75)
        if avail_match is None:
            if stop_event is not None and stop_event.is_set():
                return None
            # Fallback: click Daily Challenge tab on left sidebar
            tab_x, tab_y = self._cxy("daily_challenge_tab")
            self._log(f'[Macro] "daily_challenge_available" template missed -- using fallback tab click at ({tab_x}, {tab_y}).')
            left, top, _, _ = wm.get_window_rect_screen(hwnd)
            self._mouse.click(left + tab_x, top + tab_y)
            time.sleep(0.5)

        if self._checkpoint(stop_event):
            return None
        self._set_status(action="Selecting Daily Challenge stage...")
        stage_match = self._click_found_image(
            hwnd, "daily_challenge_stage", CHALLENGE_SCREEN_TIMEOUT, stop_event, threshold=0.75)
        if stage_match is None:
            if stop_event is not None and stop_event.is_set():
                return None
            # Fallback: click Daily Challenge stage card on right panel
            card_x, card_y = self._cxy("daily_challenge_stage")
            self._log(f'[Macro] "daily_challenge_stage" template missed -- using fallback card click at ({card_x}, {card_y}).')
            left, top, _, _ = wm.get_window_rect_screen(hwnd)
            self._mouse.click(left + card_x, top + card_y)
            time.sleep(0.5)

        if self._checkpoint(stop_event):
            return None
        if not self._enter_selected_challenge(hwnd, stop_event, play_mode, coords, webhook, daily=True):
            return None
        return "entered"

    def _open_challenge_screen(self, hwnd, stop_event: threading.Event) -> bool:
        """Lobby -> Play -> Challenge and wait for the panel to finish loading."""
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
        return True

    def _enter_selected_challenge(self, hwnd, stop_event: threading.Event, play_mode: str,
                                   coords: dict, webhook: dict, daily: bool) -> bool:
        """Use the shared Select Stage / matchmaking controls after selection."""
        challenge_task_stub = {
            "mode": "challenge", "is_challenge": True, "is_daily_challenge": daily}
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

