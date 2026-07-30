"""Auto Bounty: inspect the Event Bounty Board and run supported objectives."""
import math
import re
import threading
import time
from difflib import SequenceMatcher

from . import bounty
from . import vision
from . import window as wm
from .runner_constants import *  # noqa: F401,F403


class BountyOps:
    @staticmethod
    def _bounty_was_attempted(signature, attempted) -> bool:
        return any(bounty.same_signature(signature, previous) for previous in attempted)

    def _bounty_settings(self) -> dict:
        if self._get_bounty_settings is None:
            return {}
        try:
            return self._get_bounty_settings() or {}
        except Exception as exc:
            self._log(f"[Macro] Couldn't read Auto Bounty settings: {exc}")
            return {}

    @staticmethod
    def _line_named(lines: list, wanted: str):
        target = re.sub(r"\s+", " ", wanted).strip().lower()
        normalized = [
            (line, re.sub(r"\s+", " ", line.get("text", "")).strip().lower())
            for line in lines
        ]
        for line, text in normalized:
            if text == target:
                return line
        for line, text in normalized:
            if target in text:
                return line
        return None

    def _wait_ocr_line(self, hwnd, stop_event, text: str, timeout: float):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._checkpoint(stop_event):
                return None
            frame = vision.capture_game_bgr(hwnd)
            if frame is not None:
                line = self._line_named(bounty.ocr_windows.ocr_lines(frame), text)
                if line is not None:
                    return line
            time.sleep(0.35)
        return None

    def _wait_fuzzy_ocr_line(
            self, hwnd, stop_event, text: str, timeout: float,
            minimum_score: float = 0.68):
        target = re.sub(r"[^a-z0-9]", "", text.lower())
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._checkpoint(stop_event):
                return None
            frame = vision.capture_game_bgr(hwnd)
            if frame is not None:
                best, best_score = None, 0.0
                for line in bounty.ocr_windows.ocr_lines(frame):
                    candidate = re.sub(
                        r"[^a-z0-9]", "", line.get("text", "").lower())
                    score = SequenceMatcher(None, candidate, target).ratio()
                    if score > best_score:
                        best, best_score = line, score
                if best is not None and best_score >= minimum_score:
                    return best
            time.sleep(0.35)
        return None

    def _click_ref(self, hwnd, x: int, y: int, hold: float = 0.05) -> None:
        sx, sy = vision.ref_to_screen(hwnd, x, y)
        self._mouse.shuffle_click(sx, sy, hold=hold)

    def _bounty_board_is_open(self, frame) -> bool:
        if frame is None:
            return False
        lines = bounty.ocr_windows.ocr_lines(frame)
        if self._line_named(lines, "Bounty Board") is None:
            return False
        # The tiny heading is commonly OCRed as "Bountie LMt". The
        # dedicated saturated-counter reader validates an actual remaining /
        # total pair; dynamic bounty-card geometry is a secondary fallback.
        return (
            bounty.read_bounties_left(frame) is not None
            or bool(bounty.detect_card_scrolls(frame))
        )

    def _wait_bounty_board_open(self, hwnd, stop_event, timeout) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._checkpoint(stop_event):
                return False
            if self._bounty_board_is_open(vision.capture_game_bgr(hwnd)):
                return True
            time.sleep(0.3)
        return False

    def _open_bounty_board(self, hwnd, stop_event: threading.Event) -> bool:
        if self._bounty_board_is_open(vision.capture_game_bgr(hwnd)):
            self._log("[Macro] Bounty Board is already open -- resuming its scan.")
            return True
        if not self._ensure_lobby(hwnd, stop_event):
            return False
        self._set_status(action="Opening Events for Auto Bounty...")
        try:
            event = vision.wait_for_image(
                hwnd, "nav_event", timeout=EVENT_SCREEN_TIMEOUT, stop_event=stop_event)
        except vision.TemplateNotFound as exc:
            self._log(f"[Macro] Can't find Events: {exc}")
            return False
        event_line = None if event is not None else self._wait_ocr_line(
            hwnd, stop_event, "Events", BOUNTY_NAV_CLICK_VERIFY_TIMEOUT)
        if event is None and event_line is None:
            self._log('[Macro] Auto Bounty could not find the lobby "Events" button.')
            return False

        board_line = None
        for attempt in range(1, BOUNTY_NAV_CLICK_ATTEMPTS + 1):
            source = f'image score {event["score"]:.2f}' if event is not None else "Windows OCR"
            self._log(f"[Macro] Auto Bounty found Events ({source}) -- opening it "
                      f"(attempt {attempt}/{BOUNTY_NAV_CLICK_ATTEMPTS}).")
            wm.activate_window(hwnd)
            if event is not None:
                vision.shuffle_click_match(self._mouse, hwnd, event)
            else:
                self._click_ref(hwnd, event_line["cx"], event_line["cy"])
            board_line = self._wait_ocr_line(
                hwnd, stop_event, "Bounty Board", BOUNTY_NAV_CLICK_VERIFY_TIMEOUT)
            if board_line is not None:
                break
            try:
                event = vision.find_image(hwnd, "nav_event")
            except vision.TemplateNotFound:
                event = None
            event_line = None if event is not None else self._wait_ocr_line(
                hwnd, stop_event, "Events", 1.0)
            if event is None and event_line is None:
                break
        if board_line is None:
            self._log('[Macro] Auto Bounty could not find "Bounty Board" on the Events screen.')
            return False

        for attempt in range(1, BOUNTY_NAV_CLICK_ATTEMPTS + 1):
            wm.activate_window(hwnd)
            self._click_ref(hwnd, board_line["cx"], board_line["cy"])
            if self._wait_bounty_board_open(
                    hwnd, stop_event, BOUNTY_NAV_CLICK_VERIFY_TIMEOUT):
                return True
            self._log(f"[Macro] Bounty Board click did not register "
                      f"(attempt {attempt}/{BOUNTY_NAV_CLICK_ATTEMPTS}).")
            board_line = self._wait_ocr_line(hwnd, stop_event, "Bounty Board", 1.0)
            if board_line is None:
                break
        self._log("[Macro] Bounty Board did not finish opening.")
        return False

    def _leave_bounty_board(self, hwnd, stop_event) -> bool:
        for attempt in range(1, BOUNTY_NAV_CLICK_ATTEMPTS + 1):
            frame = vision.capture_game_bgr(hwnd)
            lines = bounty.ocr_windows.ocr_lines(frame) if frame is not None else []
            back = self._line_named(lines, "Back")
            if back is None:
                break
            self._log(f"[Macro] Leaving Bounty Board "
                      f"(attempt {attempt}/{BOUNTY_NAV_CLICK_ATTEMPTS}).")
            wm.activate_window(hwnd)
            self._click_ref(hwnd, back["cx"], back["cy"])
            try:
                lobby, _name = vision.wait_for_image_any(
                    hwnd, NAV_PLAY_IMAGE_NAMES,
                    timeout=BOUNTY_NAV_CLICK_VERIFY_TIMEOUT, stop_event=stop_event)
            except vision.TemplateNotFound:
                lobby = None
            if lobby is not None:
                return True
        return self._ensure_lobby(hwnd, stop_event)

    def _find_next_bounty(self, hwnd, stop_event, attempted: list):
        # Roblox remembers the board's horizontal position after leaving a
        # stage. Always return to the beginning so every pass audits cards in
        # a deterministic left-to-right order instead of starting wherever
        # the previous click happened to leave the carousel.
        sx, sy = vision.ref_to_screen(hwnd, *BOUNTY_SCROLL_HOVER)
        self._mouse.move_to(sx, sy)
        self._mouse.nudge()
        for _ in range(BOUNTY_HORIZONTAL_SCROLL_STEPS):
            self._mouse.scroll(-BOUNTY_HORIZONTAL_WHEEL_DELTA)
        self._interruptible_sleep(BOUNTY_SCROLL_SETTLE, stop_event)

        summon_sightings = []
        for scroll_no in range(BOUNTY_HORIZONTAL_SCROLL_STEPS + 1):
            if self._checkpoint(stop_event):
                return None
            frame = vision.capture_game_bgr(hwnd)
            if frame is not None:
                drags = bounty.detect_card_scrolls(frame)
                # Fully inspect one card (including its private scrollbar)
                # before considering the next card. Card positions are
                # detected from the current frame; no bounty/map coordinates
                # or assumed card ordering are baked in.
                for drag in sorted(drags, key=lambda item: item["card"][0]):
                    if self._checkpoint(stop_event):
                        return None
                    card_x, card_y, card_w, card_h = drag["card"]
                    for claim in bounty.detect_claim_buttons(frame, [drag]):
                        return claim
                    summon_sightings.extend(
                        bounty.detect_summon_objectives(frame, [drag]))
                    for objective in bounty.detect_objectives(frame):
                        if (card_x <= objective["cx"] <= card_x + card_w
                                and card_y <= objective["cy"] <= card_y + card_h
                                and not self._bounty_was_attempted(
                                    objective["signature"], attempted)):
                            return objective
                    if not drag.get("has_scrollbar", True):
                        # Every visible objective was already inspected and
                        # this card has no private bar, so there is no hidden
                        # content to reveal. Move directly to the next card.
                        continue
                    x1, y1 = vision.ref_to_screen(hwnd, drag["x"], drag["from_y"])
                    x2, y2 = vision.ref_to_screen(hwnd, drag["x"], drag["to_y"])
                    self._mouse.drag(x1, y1, x2, y2, duration=0.25)
                    self._interruptible_sleep(BOUNTY_SCROLL_SETTLE, stop_event)
                    frame = vision.capture_game_bgr(hwnd)
                    if frame is not None:
                        summon_sightings.extend(
                            bounty.detect_summon_objectives(frame, [drag]))
                        for objective in bounty.detect_objectives(frame):
                            if (card_x <= objective["cx"] <= card_x + card_w
                                    and card_y <= objective["cy"] <= card_y + card_h
                                    and not self._bounty_was_attempted(
                                        objective["signature"], attempted)):
                                return objective
                if not drags:
                    claims = bounty.detect_claim_buttons(frame, [])
                    if claims:
                        return claims[0]
                    for objective in bounty.detect_objectives(frame):
                        if not self._bounty_was_attempted(
                                objective["signature"], attempted):
                            return objective
                    summon_sightings.extend(
                        bounty.detect_summon_objectives(frame))
            if scroll_no == BOUNTY_HORIZONTAL_SCROLL_STEPS:
                break
            sx, sy = vision.ref_to_screen(hwnd, *BOUNTY_SCROLL_HOVER)
            self._mouse.move_to(sx, sy)
            self._mouse.nudge()
            self._mouse.scroll(BOUNTY_HORIZONTAL_WHEEL_DELTA)
            self._interruptible_sleep(BOUNTY_SCROLL_SETTLE, stop_event)
        available_summons = [
            item for item in summon_sightings
            if not self._bounty_was_attempted(item["signature"], attempted)
        ]
        if available_summons:
            # One summon advances every active summon objective. Running the
            # largest outstanding amount prevents 250 + 500 from becoming
            # 750 summons while still satisfying both cards.
            largest = max(
                available_summons,
                key=lambda item: (
                    item["remaining_summons"], item["target_summons"]))
            return {
                **largest,
                "signature": ("summon", largest["target_summons"], 0),
            }
        return None

    def _capture_summon_menu(self, hwnd):
        frame = vision.capture_game_bgr(hwnd)
        return bounty.detect_summon_menu(frame) if frame is not None else None

    def _wait_summon_menu(self, hwnd, stop_event, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._checkpoint(stop_event):
                return None
            menu = self._capture_summon_menu(hwnd)
            if menu is not None:
                return menu
            time.sleep(0.3)
        return None

    def _wait_lobby_summon(self, hwnd, stop_event, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._checkpoint(stop_event):
                return None
            frame = vision.capture_game_bgr(hwnd)
            target = (
                bounty.detect_lobby_summon(frame)
                if frame is not None else None
            )
            if target is not None:
                return target
            time.sleep(0.3)
        return None

    def _run_summon_bounty(
            self, hwnd, stop_event, objective: dict, settings: dict) -> bool:
        remaining = max(0, int(objective.get("remaining_summons") or 0))
        batches = min(
            BOUNTY_SUMMON_MAX_BATCHES_PER_START,
            int(math.ceil(remaining / BOUNTY_SUMMON_BATCH_SIZE)),
        )
        if batches <= 0:
            return True
        banner = settings.get("summon_banner") or "standard"
        currency = "Gems" if banner == "standard" else "Villain Coins"
        self._set_status(
            action=f"Summoning {batches * BOUNTY_SUMMON_BATCH_SIZE} on "
                   f"the {banner.title()} banner...")
        self._log(
            f"[Macro] Auto Bounty: {remaining} summons remain -- running "
            f"{batches} 50x click(s) on the {banner.title()} "
            f"banner using {currency}.")

        if not self._leave_bounty_board(hwnd, stop_event):
            self._log("[Macro] Auto Bounty could not leave the board for Summon.")
            return False

        summon_line = self._wait_lobby_summon(
            hwnd, stop_event, BOUNTY_SUMMON_NAV_TIMEOUT)
        if summon_line is None:
            self._log('[Macro] Auto Bounty could not find the lobby "Summon" button.')
            return False
        open_menu = None
        for attempt in range(1, BOUNTY_NAV_CLICK_ATTEMPTS + 1):
            wm.activate_window(hwnd)
            self._interruptible_sleep(BOUNTY_CLICK_FOCUS_SETTLE, stop_event)
            self._click_ref(
                hwnd, summon_line["cx"], summon_line["cy"], hold=0.1)
            open_menu = self._wait_fuzzy_ocr_line(
                hwnd, stop_event, "Open Menu", BOUNTY_NAV_CLICK_VERIFY_TIMEOUT)
            if open_menu is not None:
                break
            self._log(
                f"[Macro] Summon teleport click did not register "
                f"(attempt {attempt}/{BOUNTY_NAV_CLICK_ATTEMPTS}).")
        if open_menu is None:
            self._log("[Macro] Auto Bounty did not reach the Summon NPC.")
            return False

        menu = None
        for attempt in range(1, BOUNTY_NAV_CLICK_ATTEMPTS + 1):
            wm.activate_window(hwnd)
            self._interruptible_sleep(
                BOUNTY_CLICK_FOCUS_SETTLE, stop_event)
            self._keyboard.tap(ord("E"), hold=0.12)
            menu = self._wait_summon_menu(
                hwnd, stop_event, BOUNTY_NAV_CLICK_VERIFY_TIMEOUT)
            if menu is not None:
                break
            self._log(
                f"[Macro] Summon Open Menu key did not register "
                f"(attempt {attempt}/{BOUNTY_NAV_CLICK_ATTEMPTS}).")
        if menu is None:
            self._log("[Macro] Auto Bounty could not open the Summon menu.")
            return False

        tab = menu["tabs"].get(banner)
        if tab is None:
            self._log(
                f"[Macro] Auto Bounty could not locate the "
                f"{banner.title()} banner tab.")
            return False
        self._click_ref(hwnd, tab["cx"], tab["cy"], hold=0.08)
        self._interruptible_sleep(BOUNTY_SCROLL_SETTLE, stop_event)

        menu = self._wait_summon_menu(
            hwnd, stop_event, BOUNTY_SUMMON_NAV_TIMEOUT)
        if menu is None:
            self._log(
                "[Macro] Auto Bounty could not locate the Summon 50x "
                "button after selecting the banner.")
            return False
        button = menu["summon_50"]
        completed_batches = 0
        for batch_no in range(1, batches + 1):
            if self._checkpoint(stop_event):
                return False
            self._log(
                f"[Macro] Auto Bounty: clicking the center of Summon 50x "
                f"{batch_no}/{batches}.")
            wm.activate_window(hwnd)
            self._interruptible_sleep(BOUNTY_CLICK_FOCUS_SETTLE, stop_event)
            self._click_ref(hwnd, button["cx"], button["cy"], hold=0.1)
            self._interruptible_sleep(
                BOUNTY_SUMMON_ANIMATION_DELAY, stop_event)
            if self._checkpoint(stop_event):
                return False
            self._log(
                f"[Macro] Auto Bounty: dismissing Obtained Rewards "
                f"{batch_no}/{batches}.")
            # The result overlay explicitly accepts a click anywhere. Keep
            # the cursor on the already detected center of Summon 50x and
            # click that exact point again after the animation delay.
            wm.activate_window(hwnd)
            self._click_ref(hwnd, button["cx"], button["cy"], hold=0.1)
            completed_batches += 1
            self._interruptible_sleep(
                BOUNTY_SUMMON_MENU_SETTLE, stop_event)

        if completed_batches != batches:
            self._log(
                f"[Macro] Auto Bounty completed {completed_batches}/{batches} "
                "Summon 50x + reward-dismiss cycles.")
            self._save_debug_screenshot_unconditional(
                hwnd, "bounty_summon_failed")
        else:
            self._log(
                f"[Macro] Auto Bounty completed all {completed_batches} "
                "Summon 50x + reward-dismiss cycles -- returning to the "
                "board to verify shared progress.")
        # E closes the banner menu and leaves the player in the lobby. The
        # board rescan, not an animation heuristic, verifies actual progress.
        wm.activate_window(hwnd)
        self._keyboard.tap(ord("E"), hold=0.08)
        lobby = self._ensure_lobby(hwnd, stop_event)
        return completed_batches == batches and lobby

    def _claim_completed_bounty(
            self, hwnd, stop_event, claim: dict, webhook: dict) -> bool:
        """Click and verify one dynamically detected completed-card claim."""
        self._set_status(action="Claiming completed bounty...")
        for attempt in range(1, BOUNTY_NAV_CLICK_ATTEMPTS + 1):
            if self._checkpoint(stop_event):
                return False
            self._log(f"[Macro] Auto Bounty: claiming completed card "
                      f"(attempt {attempt}/{BOUNTY_NAV_CLICK_ATTEMPTS}).")
            wm.activate_window(hwnd)
            self._interruptible_sleep(BOUNTY_CLICK_FOCUS_SETTLE, stop_event)
            self._click_ref(hwnd, claim["cx"], claim["cy"], hold=0.1)
            self._interruptible_sleep(BOUNTY_SCROLL_SETTLE, stop_event)
            reward = None
            last_frame = None
            deadline = time.time() + BOUNTY_NAV_CLICK_VERIFY_TIMEOUT
            while time.time() < deadline and not self._checkpoint(stop_event):
                frame = vision.capture_game_bgr(hwnd)
                if frame is not None:
                    last_frame = frame
                    reward = bounty.read_reward_overlay(frame)
                    if reward is not None:
                        break
                time.sleep(0.25)
            if reward is None:
                # The reward animation can finish before OCR gets a clean
                # frame. If the original green claim control is now gone,
                # the click succeeded and the card is already disabled;
                # retrying its stale coordinates only clicks a dead button.
                if last_frame is not None:
                    still_available = any(
                        abs(button["cx"] - claim["cx"]) <= 30
                        and abs(button["cy"] - claim["cy"]) <= 30
                        for button in bounty.detect_claim_buttons(last_frame)
                    )
                    if not still_available:
                        self._log(
                            "[Macro] Auto Bounty: completed card claimed "
                            "(claim control is now disabled).")
                        return True
                continue

            screenshot_path = self._save_debug_screenshot_unconditional(
                hwnd, "bounty_reward")
            description = reward["description"]
            self._log(f"[Macro] Auto Bounty reward: {description}.")
            self._send_event_webhook(
                webhook,
                {"map": "Bounty Board"},
                "Auto Bounty Reward Claimed",
                f"Claimed **{description}**.",
                0xF4B942,
                screenshot_path,
                extra_fields=[{
                    "name": "Reward", "value": description, "inline": True,
                }],
            )

            # Claiming opens an "Obtained Rewards" overlay. It explicitly
            # requires another click before the reward is collected and
            # navigation can continue.
            wm.activate_window(hwnd)
            self._interruptible_sleep(BOUNTY_CLICK_FOCUS_SETTLE, stop_event)
            self._click_ref(
                hwnd, reward["close_cx"], reward["close_cy"], hold=0.1)
            close_deadline = time.time() + BOUNTY_NAV_CLICK_VERIFY_TIMEOUT
            while time.time() < close_deadline:
                if self._checkpoint(stop_event):
                    return False
                frame = vision.capture_game_bgr(hwnd)
                if frame is not None and bounty.read_reward_overlay(frame) is None:
                    self._log("[Macro] Auto Bounty: reward collected and overlay closed.")
                    return True
                time.sleep(0.25)
            self._log("[Macro] Auto Bounty reward overlay did not close.")
        self._log("[Macro] Auto Bounty could not claim the completed card.")
        return False

    def _read_bounty_destination_map(self, hwnd, stop_event, timeout=None):
        deadline = time.time() + (
            BOUNTY_DESTINATION_TIMEOUT if timeout is None else max(0.1, float(timeout)))
        while time.time() < deadline:
            if self._checkpoint(stop_event):
                return None
            frame = vision.capture_game_bgr(hwnd)
            if frame is not None:
                map_name = bounty.read_destination_map(frame)
                if map_name:
                    return map_name
            time.sleep(0.35)
        return None

    def _run_bounties(self, hwnd, stop_event: threading.Event, coords: dict,
                        default_walk_paths: dict, webhook: dict) -> bool:
        """Run supported board objectives once per Start."""
        settings = self._bounty_settings()
        if not settings.get("enabled"):
            return False
        if settings.get("setup_ready") is False:
            missing = ", ".join(settings.get("missing_maps") or [])
            invalid = ", ".join(
                f'{item.get("map")} ("{item.get("macro")}")'
                for item in (settings.get("invalid_maps") or []))
            details = "; ".join(part for part in (
                f"unassigned: {missing}" if missing else "",
                f"missing or old macros: {invalid}" if invalid else "",
            ) if part)
            self._log(
                "[Macro] Auto Bounty skipped: every Story map needs a saved "
                f"Macro Operation before it can run ({details}).")
            return False
        if settings.get("remaining") == 0:
            self._log(
                "[Macro] Auto Bounty: 0 bounties remain for this game day "
                "-- skipping the Bounty Board.")
            return True

        self._log("[Macro] Auto Bounty is enabled -- checking gameplay bounties "
                  "before Challenge and the Task Queue...")
        attempted = []
        objective_failures = []
        number = 0
        claims = 0
        board_open = False
        summon_progress = {}
        highest_summon_target = 0
        while number < BOUNTY_MAX_OBJECTIVES_PER_START:
            if self._checkpoint(stop_event):
                return True
            if not board_open:
                if not self._open_bounty_board(hwnd, stop_event):
                    self._leave_bounty_board(hwnd, stop_event)
                    return True
                board_open = True

            objective = self._find_next_bounty(hwnd, stop_event, attempted)
            if objective is None:
                frame = vision.capture_game_bgr(hwnd)
                remaining = (
                    bounty.read_bounties_left(frame)
                    if frame is not None else None
                )
                if remaining is not None:
                    left, total = remaining
                    self._set_bounty_remaining(left, total)
                    if left:
                        self._log(
                            f"[Macro] Auto Bounty: {left}/{total} bounties remain, "
                            "but none can currently be completed "
                            "(no supported untried objective was readable).")
                    else:
                        self._log(
                            f"[Macro] Auto Bounty: all bounties are complete "
                            f"({left}/{total} remaining).")
                else:
                    self._log(
                        "[Macro] Auto Bounty found no more supported untried objectives "
                        "(Clear Wave, Hard, and Summon are supported).")
                self._leave_bounty_board(hwnd, stop_event)
                self._log(
                    "[Macro] Auto Bounty pass finished -- moving on to Challenge "
                    "and the Task Queue.")
                self._set_status(action="Checking Challenge and Task Queue...")
                return True
            if objective["kind"] == "claim":
                if not self._claim_completed_bounty(
                        hwnd, stop_event, objective, webhook):
                    self._save_debug_screenshot_unconditional(
                        hwnd, "bounty_claim_failed")
                    self._leave_bounty_board(hwnd, stop_event)
                    return True
                claims += 1
                if claims >= BOUNTY_MAX_CLAIMS_PER_START:
                    self._log("[Macro] Auto Bounty reached its completed-card "
                              "claim safety limit for this Start.")
                    self._leave_bounty_board(hwnd, stop_event)
                    return True
                # A successful claim closes only the reward overlay; the
                # Bounty Board itself is still open. Rescan it in place so
                # every remaining card is audited left-to-right instead of
                # backing out, waiting for the outer run loop, and appearing
                # to stop after one reward.
                continue

            number += 1
            if objective["kind"] == "summon":
                label = f'Summon {objective["remaining_summons"]} more times'
                self._log(
                    f"[Macro] Auto Bounty #{number}: found {label}.")
                self._set_status(
                    current_task=f"Auto Bounty #{number}",
                    action=f"Opening {label}...",
                    mode="bounty", stage="-", difficulty="-", map="-", macro="-")
                target = int(objective["target_summons"])
                remaining = int(objective["remaining_summons"])
                if target < highest_summon_target:
                    self._log(
                        f"[Macro] Auto Bounty: ignoring the smaller Summon "
                        f"{target} objective because the shared "
                        f"{highest_summon_target} target already controls "
                        "this run.")
                    attempted.append(objective["signature"])
                    continue
                previous = summon_progress.get(target)
                if previous is not None and remaining >= previous:
                    self._log(
                        f"[Macro] Auto Bounty: Summon {target} made no "
                        "progress after the requested clicks -- skipping it "
                        "(check currency and inventory space).")
                    attempted.append(objective["signature"])
                    continue
                highest_summon_target = max(
                    highest_summon_target, target)
                summon_progress[target] = remaining
                self._run_summon_bounty(
                    hwnd, stop_event, objective, settings)
                board_open = False
                continue

            label = (f'Clear Wave {objective["target_wave"]}'
                     if objective["kind"] == "infinite" else "Hard difficulty")
            self._log(f"[Macro] Auto Bounty #{number}: found {label} -- opening its destination.")
            self._set_status(
                current_task=f"Auto Bounty #{number}", action=f"Opening {label}...",
                mode="bounty", stage="-", difficulty="-", map="-", macro="-")

            map_name = None
            for click_attempt in range(1, BOUNTY_NAV_CLICK_ATTEMPTS + 1):
                wm.activate_window(hwnd)
                # Roblox occasionally swallows input sent in the same tick
                # that its docked child window regains focus. A live click
                # on the exact detected Flower Forest point registered once
                # the window received this short focus-settle interval.
                self._interruptible_sleep(BOUNTY_CLICK_FOCUS_SETTLE, stop_event)
                if self._checkpoint(stop_event):
                    return True
                # Aim inside the lower half of the detected colored glyph
                # box. This remains entirely detection-derived, but avoids
                # the top edge of the very thin link hitbox seen in the live
                # Flower Forest miss.
                click_y = objective["cy"] + max(1, objective["h"] // 4)
                self._click_ref(
                    hwnd, objective["cx"], click_y, hold=0.1)
                map_name = self._read_bounty_destination_map(
                    hwnd, stop_event, BOUNTY_NAV_CLICK_VERIFY_TIMEOUT)
                if map_name:
                    break
                # The board heading is "Bounty Board". "Bounties" only
                # appears in the tiny "Bounties Left" counter, which OCR
                # commonly reads as e.g. "Bountie LMt". Using that counter
                # made us incorrectly conclude the screen had changed after
                # the very first missed click, so attempts 2 and 3 never ran.
                if self._wait_ocr_line(hwnd, stop_event, "Bounty Board", 1.0) is None:
                    break
                self._log(f"[Macro] Bounty objective click did not register "
                          f"(attempt {click_attempt}/{BOUNTY_NAV_CLICK_ATTEMPTS}).")
            if not map_name:
                failure = next(
                    (item for item in objective_failures
                     if bounty.same_signature(item["signature"], objective["signature"])),
                    None,
                )
                if failure is None:
                    failure = {"signature": objective["signature"], "count": 0}
                    objective_failures.append(failure)
                failure["count"] += 1
                exhausted = failure["count"] >= BOUNTY_OBJECTIVE_FAILURE_ATTEMPTS
                if exhausted:
                    attempted.append(objective["signature"])
                self._log(
                    "[Macro] Auto Bounty could not read the destination map -- "
                    + (f"giving up on this objective after {failure['count']} attempts for this Start."
                       if exhausted else
                       f"returning to the board to retry it "
                       f"({failure['count']}/{BOUNTY_OBJECTIVE_FAILURE_ATTEMPTS}).")
                )
                self._save_debug_screenshot_unconditional(hwnd, "bounty_destination_unreadable")
                self._recover_to_lobby(hwnd, stop_event)
                board_open = False
                continue

            # A successful objective click replaced the board with the
            # stage-detail panel. Every path from here returns to the lobby,
            # so the next objective must open a fresh board.
            board_open = False
            macro_name = ((settings.get("maps", {}).get(map_name) or {}).get("macro") or "")
            play_mode = settings.get("play_mode") or "solo"
            stage = "Infinite" if objective["kind"] == "infinite" else "1"
            task = {
                "mode": "story", "is_bounty": True, "map": map_name, "stage": stage,
                "difficulty": "Hard", "macro": macro_name, "play_mode": play_mode,
                "repeat": 1, "team": "", "equipment": "include",
                # Existing Infinite behavior leaves only after this wave is
                # complete, when the following wave begins.
                "infinite_wave_limit": objective.get("target_wave"),
            }
            self._log(f'[Macro] Auto Bounty #{number}: destination is "{map_name}" '
                      f'({stage}, Hard) -- running "{macro_name or "No Macro"}".')
            self._set_status(
                map=map_name, stage=stage, difficulty="Hard",
                play_mode=play_mode, macro=macro_name or "-")

            if objective["kind"] == "hard":
                self._select_difficulty(hwnd, "Hard", coords)
                self._interruptible_sleep(DIFFICULTY_CLICK_DELAY, stop_event)
            if not self._enter_selected_stage(hwnd, stop_event, task, "story", coords, webhook):
                self._log(f"[Macro] Auto Bounty #{number}: could not enter the selected stage.")
                self._recover_to_lobby(hwnd, stop_event)
                continue

            started = time.time()
            result = self._play_one_match(
                hwnd, stop_event, task, default_walk_paths, first_repeat=True, webhook=webhook)
            if result == "wave_limit":
                self._log(f"[Macro] Auto Bounty #{number}: wave {objective['target_wave']} "
                          "completed and the stage was exited.")
                continue
            if result is None:
                self._log(f"[Macro] Auto Bounty #{number}: battle did not finish cleanly.")
                self._recover_to_lobby(hwnd, stop_event)
                continue
            duration = self._format_duration(time.time() - started)
            if not self._handle_match_result(
                    hwnd, stop_event, task, result, duration, webhook, repeat=False):
                self._recover_to_lobby(hwnd, stop_event)

        self._log(f"[Macro] Auto Bounty reached its safety limit of "
                  f"{BOUNTY_MAX_OBJECTIVES_PER_START} objectives for this Start.")
        self._leave_bounty_board(hwnd, stop_event)
        return True
