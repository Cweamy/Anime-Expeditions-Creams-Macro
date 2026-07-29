"""Auto Bounty: inspect the Event Bounty Board and run supported objectives."""
import re
import threading
import time

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

    def _click_ref(self, hwnd, x: int, y: int) -> None:
        sx, sy = vision.ref_to_screen(hwnd, x, y)
        self._mouse.shuffle_click(sx, sy)

    def _open_bounty_board(self, hwnd, stop_event: threading.Event) -> bool:
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
            if self._wait_ocr_line(
                    hwnd, stop_event, "Bounties", BOUNTY_NAV_CLICK_VERIFY_TIMEOUT) is not None:
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
                    hwnd, NAV_PLAY_IMAGE_NAMES, region=NAV_PLAY_REGION,
                    timeout=BOUNTY_NAV_CLICK_VERIFY_TIMEOUT, stop_event=stop_event)
            except vision.TemplateNotFound:
                lobby = None
            if lobby is not None:
                return True
        return self._ensure_lobby(hwnd, stop_event)

    def _find_next_bounty(self, hwnd, stop_event, attempted: list):
        for scroll_no in range(BOUNTY_HORIZONTAL_SCROLL_STEPS + 1):
            if self._checkpoint(stop_event):
                return None
            frame = vision.capture_game_bgr(hwnd)
            if frame is not None:
                objectives = bounty.detect_objectives(frame)
                for objective in objectives:
                    if not self._bounty_was_attempted(objective["signature"], attempted):
                        return objective
                drags = bounty.detect_card_scrolls(frame)
                for drag in drags:
                    if self._checkpoint(stop_event):
                        return None
                    x1, y1 = vision.ref_to_screen(hwnd, drag["x"], drag["from_y"])
                    x2, y2 = vision.ref_to_screen(hwnd, drag["x"], drag["to_y"])
                    self._mouse.drag(x1, y1, x2, y2, duration=0.25)
                if drags:
                    self._interruptible_sleep(BOUNTY_SCROLL_SETTLE, stop_event)
                    frame = vision.capture_game_bgr(hwnd)
                    if frame is not None:
                        for objective in bounty.detect_objectives(frame):
                            if not self._bounty_was_attempted(objective["signature"], attempted):
                                return objective
            if scroll_no == BOUNTY_HORIZONTAL_SCROLL_STEPS:
                break
            sx, sy = vision.ref_to_screen(hwnd, *BOUNTY_SCROLL_HOVER)
            self._mouse.move_to(sx, sy)
            self._mouse.nudge()
            self._mouse.scroll(BOUNTY_HORIZONTAL_WHEEL_DELTA)
            self._interruptible_sleep(BOUNTY_SCROLL_SETTLE, stop_event)
        return None

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

        self._log("[Macro] Auto Bounty is enabled -- checking gameplay bounties "
                  "before Challenge and the Task Queue...")
        attempted = []
        objective_failures = []
        for number in range(1, BOUNTY_MAX_OBJECTIVES_PER_START + 1):
            if self._checkpoint(stop_event):
                return True
            if not self._open_bounty_board(hwnd, stop_event):
                self._leave_bounty_board(hwnd, stop_event)
                return True

            objective = self._find_next_bounty(hwnd, stop_event, attempted)
            if objective is None:
                self._log("[Macro] Auto Bounty found no more supported untried objectives "
                          "(Clear Wave and Hard are supported; Summon is not yet).")
                self._leave_bounty_board(hwnd, stop_event)
                return True
            label = (f'Clear Wave {objective["target_wave"]}'
                     if objective["kind"] == "infinite" else "Hard difficulty")
            self._log(f"[Macro] Auto Bounty #{number}: found {label} -- opening its destination.")
            self._set_status(
                current_task=f"Auto Bounty #{number}", action=f"Opening {label}...",
                mode="bounty", stage="-", difficulty="-", map="-", macro="-")

            map_name = None
            for click_attempt in range(1, BOUNTY_NAV_CLICK_ATTEMPTS + 1):
                wm.activate_window(hwnd)
                self._click_ref(hwnd, objective["cx"], objective["cy"])
                map_name = self._read_bounty_destination_map(
                    hwnd, stop_event, BOUNTY_NAV_CLICK_VERIFY_TIMEOUT)
                if map_name:
                    break
                if self._wait_ocr_line(hwnd, stop_event, "Bounties", 1.0) is None:
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
                continue

            attempted.append(objective["signature"])

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
