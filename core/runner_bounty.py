"""Auto Bounty: inspect the Event Bounty Board and run supported objectives."""
import math
import os
import re
import threading
import time
from difflib import SequenceMatcher

import numpy as np

from . import bounty
from . import vision
from . import window as wm
from .runner_constants import *  # noqa: F401,F403


class BountyOps:
    def _bounty_audit_is_enabled(self) -> bool:
        return bool(getattr(self, "_bounty_audit_enabled", False)) or (
            os.environ.get("AE_BOUNTY_AUDIT") == "1")

    def _bounty_audit(self):
        if not self._bounty_audit_is_enabled():
            return None
        recorder = getattr(self, "_bounty_audit_recorder", None)
        if recorder is None:
            from .bounty_audit import BountyAudit
            recorder = BountyAudit()
            self._bounty_audit_recorder = recorder
            self._log(f"[Audit] Auto Bounty trace started: {recorder.root}")
        return recorder

    def _audit_event(self, name: str, **data) -> None:
        recorder = self._bounty_audit()
        if recorder is not None:
            recorder.event(name, **data)

    def _audit_frame(self, label: str, frame, **data) -> None:
        recorder = self._bounty_audit()
        if recorder is not None:
            recorder.frame(label, frame, **data)

    def _close_bounty_audit(self) -> None:
        recorder = getattr(self, "_bounty_audit_recorder", None)
        if recorder is not None:
            recorder.close()
            self._bounty_audit_recorder = None

    def _debug_bounty_log(self, message: str) -> None:
        if self._bounty_detection_only():
            self._log(message)

    def _hover_bounty_ref(self, hwnd, x: int, y: int, label: str) -> None:
        """Hover a live-detected bounty target without clicking it."""
        sx, sy = vision.ref_to_screen(hwnd, int(x), int(y))
        wm.activate_window(hwnd)
        self._mouse.move_to(sx, sy)
        self._audit_event(
            "hover", label=label, reference=[int(x), int(y)],
            screen=[sx, sy])
        self._debug_bounty_log(
            f"[Debug]   hover {label}: ref=({int(x)},{int(y)}) "
            f"screen=({sx},{sy})")
        time.sleep(0.12)

    @staticmethod
    def _bounty_detection_only() -> bool:
        """Temporary live-replay mode: inspect, but never execute a bounty."""
        return os.environ.get("AE_BOUNTY_DETECTION_ONLY") == "1"

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

    def _ensure_mythic_bounty(
            self, hwnd, stop_event, frame, drag, card_no,
            ocr_lines=None) -> dict:
        """Reroll one live card until its card-local title reads Mythic.

        A successful reroll invalidates every objective coordinate collected
        from the previous card contents. The caller must rescan before using
        any objective or summon reference from that card.
        """
        settings = self._bounty_settings()
        try:
            max_rerolls = max(
                BOUNTY_MYTHIC_MIN_REROLLS,
                min(
                    BOUNTY_MYTHIC_MAX_REROLLS,
                    int(settings.get(
                        "mythic_max_rerolls", BOUNTY_MYTHIC_DEFAULT_REROLLS)),
                ),
            )
        except (TypeError, ValueError):
            max_rerolls = BOUNTY_MYTHIC_DEFAULT_REROLLS
        card = drag.get("card") or ()
        if (frame is None or not hasattr(frame, "shape")
                or len(card) < 4):
            return {"status": "unknown", "card": int(card_no), "rerolls": 0}

        rarity = bounty.read_card_rarity(frame, card, ocr_lines)
        self._audit_event(
            "mythic_rarity", card=card_no, rarity=rarity,
            reference_card=list(card), rerolls=0)
        if rarity == "mythic":
            return {"status": "ready", "card": int(card_no), "rerolls": 0}
        if rarity != "other":
            self._log(
                f"[Macro] Auto Mythic could not read the rarity of card "
                f"{card_no}; it will not click an unverified reroll button.")
            self._audit_event(
                "mythic_rarity_unreadable", card=card_no,
                reference_card=list(card))
            return {"status": "unknown", "card": int(card_no), "rerolls": 0}

        current_frame = frame
        for reroll_no in range(1, max_rerolls + 1):
            if self._checkpoint(stop_event):
                return {"status": "stopped", "card": int(card_no),
                        "rerolls": reroll_no - 1}
            buttons = bounty.detect_reroll_buttons(current_frame, [drag])
            if not buttons:
                self._log(
                    f"[Macro] Auto Mythic found a non-Mythic card {card_no}, "
                    "but its active gold reroll button was not detected; "
                    "leaving the card untouched.")
                self._audit_event(
                    "mythic_reroll_unavailable", card=card_no,
                    rerolls=reroll_no - 1, reference_card=list(card))
                return {"status": "unavailable", "card": int(card_no),
                        "rerolls": reroll_no - 1}
            button = buttons[0]
            self._audit_frame(
                f"card_{card_no}_mythic_before_reroll_{reroll_no}",
                current_frame, card=card_no, reroll=reroll_no,
                rarity=rarity, reroll_button=button)
            self._audit_event(
                "mythic_reroll_click", card=card_no, reroll=reroll_no,
                reference=[int(button["cx"]), int(button["cy"])],
                reroll_button=button)
            self._log(
                f"[Macro] Auto Mythic: card {card_no} is not Mythic; "
                f"rerolling ({reroll_no}/{max_rerolls}).")
            wm.activate_window(hwnd)
            self._interruptible_sleep(BOUNTY_CLICK_FOCUS_SETTLE, stop_event)
            self._click_ref(hwnd, button["cx"], button["cy"], hold=0.1)
            self._interruptible_sleep(BOUNTY_MYTHIC_REROLL_SETTLE, stop_event)

            deadline = time.time() + BOUNTY_MYTHIC_REROLL_VERIFY_TIMEOUT
            next_frame = None
            next_rarity = None
            while time.time() < deadline:
                if self._checkpoint(stop_event):
                    return {"status": "stopped", "card": int(card_no),
                            "rerolls": reroll_no}
                next_frame = vision.capture_game_bgr(hwnd)
                if (next_frame is not None
                        and hasattr(next_frame, "shape")):
                    next_rarity = bounty.read_card_rarity(next_frame, card)
                    self._audit_event(
                        "mythic_reroll_verify", card=card_no,
                        reroll=reroll_no, rarity=next_rarity,
                        reference_card=list(card))
                    if next_rarity is not None:
                        break
                self._interruptible_sleep(BOUNTY_MYTHIC_REROLL_POLL, stop_event)
            if next_rarity == "mythic":
                self._audit_frame(
                    f"card_{card_no}_mythic_after_reroll_{reroll_no}",
                    next_frame, card=card_no, reroll=reroll_no,
                    rarity=next_rarity)
                self._log(
                    f"[Macro] Auto Mythic: card {card_no} is Mythic after "
                    f"{reroll_no} reroll(s); rescanning its new objectives.")
                return {"status": "rerolled", "card": int(card_no),
                        "rerolls": reroll_no}
            if next_rarity != "other":
                self._log(
                    f"[Macro] Auto Mythic could not verify card {card_no} "
                    "after the reroll; leaving it unclaimed for a safe retry.")
                return {"status": "unknown", "card": int(card_no),
                        "rerolls": reroll_no}
            current_frame = next_frame
            rarity = next_rarity

        self._log(
            f"[Macro] Auto Mythic reached the {max_rerolls}-reroll "
            f"safety limit for card {card_no}; leaving it unclaimed.")
        self._audit_event(
            "mythic_reroll_limit", card=card_no,
            rerolls=max_rerolls, reference_card=list(card))
        return {"status": "exhausted", "card": int(card_no),
                "rerolls": max_rerolls}

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
        self._audit_event(
            "click", reference=[int(x), int(y)], screen=[sx, sy], hold=hold)
        self._mouse.shuffle_click(sx, sy, hold=hold)

    def _bounty_board_is_open(self, frame) -> bool:
        if frame is None:
            return False
        lines = bounty.ocr_windows.ocr_lines(frame)
        try:
            board_match = vision.find_frame_image(
                frame,
                bounty.BOUNTY_BOARD_IMAGE,
                threshold=bounty.BOUNTY_IMAGE_THRESHOLD,
                scale_factors=bounty.BOUNTY_IMAGE_SCALE_FACTORS,
            )
        except vision.TemplateNotFound:
            board_match = None
        if board_match is None and self._line_named(lines, "Bounty Board") is None:
            return False
        # The tiny heading is commonly OCRed as "Bountie LMt". The
        # dedicated saturated-counter reader validates an actual remaining /
        # total pair; dynamic bounty-card geometry is a secondary fallback.
        remaining = bounty.read_bounties_left(frame)
        # Keep the cheap counter result as a sufficient positive signal.  In
        # particular, test/capture shims may expose an OCR sentinel rather
        # than a NumPy frame, and card geometry cannot be evaluated on that.
        cards = [] if remaining is not None else bounty.detect_card_scrolls(frame)
        self._audit_event(
            "board_open_probe", remaining=remaining, ocr_lines=lines,
            cards=cards, image_match=board_match)
        return remaining is not None or bool(cards)

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

        board_match = None
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
            try:
                board_match = vision.wait_for_image(
                    hwnd, bounty.BOUNTY_BOARD_IMAGE,
                    threshold=bounty.BOUNTY_IMAGE_THRESHOLD,
                    timeout=BOUNTY_NAV_CLICK_VERIFY_TIMEOUT, stop_event=stop_event,
                    scale_factors=bounty.BOUNTY_IMAGE_SCALE_FACTORS)
            except vision.TemplateNotFound:
                board_match = None
            board_line = None if board_match is not None else self._wait_ocr_line(
                hwnd, stop_event, "Bounty Board", BOUNTY_NAV_CLICK_VERIFY_TIMEOUT)
            if board_match is not None or board_line is not None:
                break
            try:
                event = vision.find_image(hwnd, "nav_event")
            except vision.TemplateNotFound:
                event = None
            event_line = None if event is not None else self._wait_ocr_line(
                hwnd, stop_event, "Events", 1.0)
            if event is None and event_line is None:
                break
        if board_match is None and board_line is None:
            self._log('[Macro] Auto Bounty could not find "Bounty Board" on the Events screen.')
            return False

        for attempt in range(1, BOUNTY_NAV_CLICK_ATTEMPTS + 1):
            wm.activate_window(hwnd)
            if board_match is not None:
                vision.shuffle_click_match(self._mouse, hwnd, board_match)
            else:
                self._click_ref(hwnd, board_line["cx"], board_line["cy"])
            if self._wait_bounty_board_open(
                    hwnd, stop_event, BOUNTY_NAV_CLICK_VERIFY_TIMEOUT):
                return True
            self._log(f"[Macro] Bounty Board click did not register "
                      f"(attempt {attempt}/{BOUNTY_NAV_CLICK_ATTEMPTS}).")
            try:
                board_match = vision.find_image(
                    hwnd,
                    bounty.BOUNTY_BOARD_IMAGE,
                    threshold=bounty.BOUNTY_IMAGE_THRESHOLD,
                    scale_factors=bounty.BOUNTY_IMAGE_SCALE_FACTORS)
            except vision.TemplateNotFound:
                board_match = None
            board_line = None if board_match is not None else self._wait_ocr_line(
                hwnd, stop_event, "Bounty Board", 1.0)
            if board_match is None and board_line is None:
                break
        self._log("[Macro] Bounty Board did not finish opening.")
        return False

    def _bounty_scroll_hover(self, hwnd, frame=None):
        """Return a screen point from the live outer-scrollbar image."""
        frame = vision.capture_game_bgr(hwnd) if frame is None else frame
        try:
            match = vision.find_frame_image(
                frame,
                bounty.BOUNTY_BOARD_SCROLL_IMAGE,
                threshold=bounty.BOUNTY_IMAGE_THRESHOLD,
                scale_factors=bounty.BOUNTY_IMAGE_SCALE_FACTORS,
            )
        except vision.TemplateNotFound:
            match = None
        if match is not None:
            match = bounty.refine_board_scroll_match(frame, match)
            self._audit_event("outer_scroll_match", match=match)
            return vision.ref_to_screen(hwnd, match["cx"], match["cy"])
        # Compatibility fallback until the rendering-specific crop has been
        # captured through Image Manager. It is isolated here so bounty
        # parsing never depends on a fixed board coordinate.
        self._audit_event(
            "outer_scroll_match_missing", fallback_reference=list(BOUNTY_SCROLL_HOVER))
        return vision.ref_to_screen(hwnd, *BOUNTY_SCROLL_HOVER)

    @staticmethod
    def _card_scroll_matches(frame):
        if not hasattr(frame, "shape") or not hasattr(frame, "size") or frame.size == 0:
            return None
        try:
            # Match only inside each validated card's right edge. A full-board
            # sweep can find parchment/background lookalikes above a card and
            # then send a perfectly accurate drag to the wrong y position.
            cards = bounty.detect_card_scrolls(frame)
            matches = []
            for item in cards:
                x, y, w, h = item["card"]
                region = (max(0, x + w - 55), max(0, y + 20), 55, max(1, h - 40))
                matches.extend(vision.find_frame_images(
                    frame,
                    bounty.BOUNTY_CARD_SCROLL_IMAGE,
                    region=region,
                    threshold=bounty.BOUNTY_IMAGE_THRESHOLD,
                    scale_factors=bounty.BOUNTY_IMAGE_SCALE_FACTORS,
                ))
            return matches
        except vision.TemplateNotFound:
            return None

    def _inner_scrollbar_state(self, frame, drag):
        """Return the current live thumb match for one detected card."""
        if frame is None or not hasattr(frame, "shape"):
            return None
        try:
            matches = self._card_scroll_matches(frame)
        except Exception:
            return None
        x, y, w, h = drag["card"]
        candidates = [
            item for item in (matches or [])
            if x - 12 <= int(item.get("cx", -999)) <= x + w + 12
            and y + 25 <= int(item.get("cy", -999)) <= y + h - 25
        ]
        if candidates:
            try:
                return bounty.refine_card_scroll_match(
                    frame, max(candidates, key=lambda item: item.get("score", 0.0)))
            except Exception:
                return max(candidates, key=lambda item: item.get("score", 0.0))
        # A theme/layout variant can have no template match even though the
        # card-relative thumb is visible. Keep verification on the same
        # dynamic detector used by detect_card_scrolls so a real small move
        # is not reported as an unverified drag.
        try:
            return bounty._heuristic_card_scroll_match(frame, drag["card"])
        except Exception:
            return None

    def _refresh_card_drag(self, frame, card_box):
        """Re-detect one card after its private content has moved."""
        if frame is None or not hasattr(frame, "shape"):
            return None
        try:
            scrollbar_matches = self._card_scroll_matches(frame)
            cards = (
                bounty.detect_card_scrolls(frame)
                if scrollbar_matches is None else
                bounty.detect_card_scrolls(frame, scrollbar_matches)
            )
        except Exception:
            return None
        if not cards:
            return None
        x, y, w, h = (int(value) for value in card_box)
        return min(
            cards,
            key=lambda item: (
                abs(int(item["card"][0]) - x)
                + abs(int(item["card"][1]) - y),
                abs(int(item["card"][2]) - w)
                + abs(int(item["card"][3]) - h),
            ),
        )

    @staticmethod
    def _card_frame_delta(before, after, card) -> float | None:
        """Measure visible movement inside one card, when both captures exist."""
        if (before is None or after is None
                or not hasattr(before, "shape") or not hasattr(after, "shape")):
            return None
        x, y, w, h = (int(value) for value in card)
        before_crop = before[y:y + h, x:x + w]
        after_crop = after[y:y + h, x:x + w]
        if (before_crop.size == 0 or after_crop.size == 0
                or before_crop.shape != after_crop.shape):
            return None
        return float(np.mean(np.abs(
            before_crop.astype(np.int16) - after_crop.astype(np.int16))))

    @staticmethod
    def _card_scroll_edges(drag):
        """Return live-derived (top, bottom, tolerance) scrollbar bounds."""
        bar = drag.get("scrollbar_match") or {}
        top = int(drag.get("top_y", bar.get("track_top", drag["from_y"])))
        bottom = int(
            drag.get("bottom_y", bar.get("track_bottom", drag["to_y"])))
        thumb_h = int(bar.get("thumb_h", 0))
        # Template matching/refinement can move the estimated center by a few
        # pixels between frames. Treat a short remaining distance as already
        # being at the edge; real mid-track drags still need visual movement.
        tolerance = max(16, int(round(thumb_h * 0.12)))
        return top, bottom, tolerance

    def _perform_inner_scroll_drag(self, x1, y1, x2, y2, stop_event):
        """Drag with a real hover-in and a held, stepped pointer path."""
        # The generic drag helper is still the compatibility path for test
        # doubles and older mouse backends. The Windows Mouse implementation
        # has down/up primitives, so use the more deliberate choreography
        # there: settle on the thin thumb, nudge into it, hold, then move in
        # small real events instead of pressing immediately after a jump.
        if not all(hasattr(self._mouse, name)
                   for name in ("down", "up", "nudge")):
            self._audit_event(
                "mouse_drag_path", mode="backend_drag",
                points=[[int(x1), int(y1)], [int(x2), int(y2)]])
            self._mouse.drag(x1, y1, x2, y2, duration=0.45)
            return
        # Approach one pixel to the left, then nudge onto the exact thumb
        # center. While held, use relative moves: Roblox can acknowledge the
        # absolute arrival but ignore an absolute pointer path as a drag.
        self._mouse.move_to(x1 - 1, y1)
        time.sleep(0.08)
        self._mouse.nudge(1, 0)
        time.sleep(0.06)
        self._mouse.down()
        time.sleep(0.10)
        steps = max(18, int(abs(y2 - y1) / 3))
        step_delay = 0.42 / steps
        # SendInput relative motion is subject to the desktop's pointer
        # acceleration, so the cursor reaches roughly 70% of the requested
        # pixel delta on this setup. Compensate the path length; the card's
        # scrollbar clamps the final position at its own live track endpoint.
        relative_scale = 1.45
        last_command_x, last_command_y = x1, y1
        path = [[int(x1 - 1), int(y1)], [int(x1), int(y1)]]
        for index in range(1, steps + 1):
            if self._checkpoint(stop_event):
                self._mouse.up()
                return
            current_x = x1 + (x2 - x1) * index / steps
            current_y = y1 + (y2 - y1) * index / steps
            next_x, next_y = round(current_x), round(current_y)
            command_x = x1 + round((next_x - x1) * relative_scale)
            command_y = y1 + round((next_y - y1) * relative_scale)
            self._mouse.nudge(
                command_x - last_command_x, command_y - last_command_y)
            last_command_x, last_command_y = command_x, command_y
            if len(path) < 256:
                path.append([int(command_x), int(command_y)])
            time.sleep(step_delay)
        time.sleep(0.08)
        self._mouse.up()
        self._audit_event(
            "mouse_drag_path", mode="stepped_relative", points=path,
            requested_start=[int(x1), int(y1)],
            requested_end=[int(x2), int(y2)], relative_scale=relative_scale)

    def _drag_inner_scroll_verified(self, hwnd, frame, drag, card_no, stop_event):
        """Perform one card drag and verify that the UI accepted it.

        Detection alone is not enough for a thin scrollbar. This records
        focus/cursor evidence and compares the live thumb/card after the drag;
        one retry is allowed if Roblox did not visibly move the card.
        """
        from_y = int(drag["from_y"])
        to_y = int(drag.get("target_y", drag["to_y"]))
        target_name = drag.get("target_name", "bottom")
        remaining = abs(to_y - from_y)
        if remaining <= 16:
            self._debug_bounty_log(
                f"[Debug]   inner scrollbar card {card_no} already at "
                f"{target_name}: remaining={remaining}px")
            return False

        x1, y1 = vision.ref_to_screen(hwnd, drag["x"], from_y)
        x2, y2 = vision.ref_to_screen(hwnd, drag["x"], to_y)
        self._audit_event(
            "scroll_verification_start", card=card_no,
            target=target_name, reference_start=[int(drag["x"]), from_y],
            reference_end=[int(drag["x"]), to_y],
            screen_start=[x1, y1], screen_end=[x2, y2],
            drag=drag)
        before_state = self._inner_scrollbar_state(frame, drag)
        before_y = (int(before_state["cy"])
                    if before_state is not None else from_y)
        self._audit_frame(
            f"card_{card_no}_{target_name}_before", frame,
            card=card_no, target=target_name,
            scrollbar=before_state)

        for attempt in range(1, 3):
            if self._checkpoint(stop_event):
                return False
            wm.activate_window(hwnd)
            time.sleep(BOUNTY_CLICK_FOCUS_SETTLE)
            cursor_before = getattr(self._mouse, "position", lambda: None)()
            focus_before = bool(getattr(wm, "is_foreground", lambda _hwnd: True)(hwnd))
            self._perform_inner_scroll_drag(x1, y1, x2, y2, stop_event)
            self._interruptible_sleep(BOUNTY_SCROLL_SETTLE, stop_event)
            after = vision.capture_game_bgr(hwnd)
            # Unit-test doubles and platform capture shims may return an
            # opaque sentinel instead of a frame. The real Windows path always
            # reaches the visual checks below; keep the sentinel path as an
            # attempted drag so ordering tests can exercise the same loop.
            if after is not None and not hasattr(after, "shape"):
                self._debug_bounty_log(
                    f"[Debug]   inner drag attempt {attempt} card {card_no}: "
                    "capture unavailable; treating input as attempted")
                return True
            cursor_after = getattr(self._mouse, "position", lambda: None)()
            after_state = self._inner_scrollbar_state(after, drag)
            after_y = (int(after_state["cy"])
                       if after_state is not None else None)
            thumb_delta = (after_y - before_y) if after_y is not None else None
            card_delta = self._card_frame_delta(after, frame, drag["card"])
            card_delta_text = (
                f"{card_delta:.2f}" if card_delta is not None else "n/a")
            cursor_ok = bool(
                cursor_after is not None
                and abs(int(cursor_after[0]) - int(x2)) <= 4
                and abs(int(cursor_after[1]) - int(y2)) <= 4)
            expected_direction = 1 if to_y > from_y else -1
            visual_ok = bool(
                (thumb_delta is not None
                 and thumb_delta * expected_direction >= 4)
                or (card_delta is not None and card_delta >= 1.5))
            # If a capture was unavailable, the input evidence is the only
            # evidence possible; never call a real captured frame verified
            # without a visible change.
            # Relative input can finish a few pixels short in the OS cursor
            # readback even while Roblox has visibly moved the card. A real
            # thumb/card delta is stronger evidence than that readback; keep
            # focus mandatory, and use cursor position as the fallback only
            # when a post-drag capture is unavailable.
            verified = bool(
                focus_before
                and (visual_ok or (cursor_ok and after is None)))
            self._audit_frame(
                f"card_{card_no}_{target_name}_attempt_{attempt}", after,
                card=card_no, target=target_name, attempt=attempt,
                scrollbar=after_state)
            self._audit_event(
                "scroll_verification_attempt", card=card_no,
                target=target_name, attempt=attempt,
                screen_start=[x1, y1], screen_end=[x2, y2],
                focus_before=focus_before, cursor_before=cursor_before,
                cursor_after=cursor_after, before_thumb_y=before_y,
                after_thumb_y=after_y, thumb_delta=thumb_delta,
                card_delta=card_delta, cursor_ok=cursor_ok,
                visual_ok=visual_ok, registered=verified)
            self._debug_bounty_log(
                f"[Debug]   inner drag attempt {attempt} card {card_no}: "
                f"screen=({x1},{y1})->({x2},{y2}) "
                f"focus={focus_before} cursor_before={cursor_before} "
                f"cursor_after={cursor_after} thumb_delta={thumb_delta} "
                f"card_delta={card_delta_text} "
                f"registered={verified}")
            if verified:
                return True
            if attempt == 1:
                self._log(
                    f"[Macro] Auto Bounty inner scrollbar drag did not "
                    f"register for card {card_no}; retrying once.")
                if after_state is not None:
                    retry_from = int(after_state["cy"])
                    if abs(to_y - retry_from) <= 16:
                        self._debug_bounty_log(
                            f"[Debug]   retry skipped: card {card_no} "
                            f"reached {target_name} after the first drag")
                        return False
                    x1, y1 = vision.ref_to_screen(hwnd, drag["x"], retry_from)
            frame = after
            before_y = after_y if after_y is not None else before_y
        self._log(
            f"[Macro] Auto Bounty inner scrollbar drag failed for card "
            f"{card_no} after 2 attempts.")
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
        # Keep the original incremental carousel traversal. The image match
        # only replaces the fixed hover point; it must not change the number
        # or order of scroll/scan steps.
        self._audit_event(
            "board_scan_start", attempted=attempted,
            horizontal_steps=BOUNTY_HORIZONTAL_SCROLL_STEPS)
        frame = vision.capture_game_bgr(hwnd)
        wm.activate_window(hwnd)
        sx, sy = self._bounty_scroll_hover(hwnd, frame)
        self._mouse.move_to(sx, sy)
        self._mouse.nudge()
        for _ in range(BOUNTY_HORIZONTAL_SCROLL_STEPS):
            self._mouse.scroll(-BOUNTY_HORIZONTAL_WHEEL_DELTA)
        self._interruptible_sleep(BOUNTY_SCROLL_SETTLE, stop_event)

        summon_sightings = []
        detection_only = self._bounty_detection_only()
        mythic_only = bool(self._bounty_settings().get("mythic_only"))
        frame_ocr_lines = None
        if detection_only:
            self._log("[Debug] Auto Bounty detection-only replay enabled; "
                      "no objective or claim will be executed.")
        for scroll_no in range(BOUNTY_HORIZONTAL_SCROLL_STEPS + 1):
            if self._checkpoint(stop_event):
                return None
            frame = vision.capture_game_bgr(hwnd)
            frame_ocr_lines = None
            if frame is not None:
                self._audit_frame(
                    f"board_scan_step_{scroll_no + 1}_before", frame,
                    scroll_step=scroll_no + 1)
                if hasattr(frame, "shape"):
                    try:
                        frame_ocr_lines = bounty.ocr_windows.ocr_lines(frame)
                        self._audit_event(
                            "ocr_lines", stage="board_scan",
                            scroll_step=scroll_no + 1,
                            lines=frame_ocr_lines)
                    except Exception as exc:
                        frame_ocr_lines = None
                        self._audit_event(
                            "ocr_failed", stage="board_scan",
                            scroll_step=scroll_no + 1,
                            error=f"{type(exc).__name__}: {exc}")
                scrollbar_matches = self._card_scroll_matches(frame)
                drags = (
                    bounty.detect_card_scrolls(frame)
                    if scrollbar_matches is None else
                    bounty.detect_card_scrolls(frame, scrollbar_matches)
                )
                self._debug_bounty_log(
                    f"[Debug] Bounty scan step {scroll_no + 1}/"
                    f"{BOUNTY_HORIZONTAL_SCROLL_STEPS + 1}: "
                    f"{len(drags)} validated card(s), "
                    f"{len(scrollbar_matches or [])} inner scrollbar match(es).")
                self._audit_event(
                    "card_detection", scroll_step=scroll_no + 1,
                    scrollbar_matches=scrollbar_matches or [],
                    cards=drags)
                for card_no, detected in enumerate(drags, 1):
                    x, y, w, h = detected["card"]
                    bar = detected.get("scrollbar_match") or {}
                    self._debug_bounty_log(
                        f"[Debug]   card {card_no}: box=({x},{y},{w},{h}) "
                        f"inner_bar={detected.get('has_scrollbar', False)} "
                        f"drag=({detected.get('x')},{detected.get('from_y')}"
                        f"->{detected.get('to_y')}) "
                        f"match=({bar.get('cx')},{bar.get('cy')}) "
                        f"score={bar.get('score', 0.0):.3f}")
                if detection_only:
                    try:
                        shot = self._save_debug_screenshot_unconditional(
                            hwnd, f"bounty_scan_step_{scroll_no + 1}")
                        self._debug_bounty_log(
                            f"[Debug]   scan screenshot: {shot}")
                    except Exception as exc:
                        self._debug_bounty_log(
                            f"[Debug]   scan screenshot failed: {exc}")
                # Fully inspect one card (including its private scrollbar)
                # before considering the next card. Card positions are
                # detected from the current frame; no bounty/map coordinates
                # or assumed card ordering are baked in.
                for card_no, initial_drag in enumerate(
                        sorted(drags, key=lambda item: item["card"][0]), 1):
                    if self._checkpoint(stop_event):
                        return None
                    drag = self._refresh_card_drag(
                        frame, initial_drag["card"]) or initial_drag
                    self._debug_bounty_log(
                        f"[Debug]   beginning left-to-right card {card_no} "
                        f"at x={drag['card'][0]}")
                    claim_candidate = None
                    # A missing template match has already had a strict
                    # card-relative thumb heuristic applied in bounty.py. If
                    # that also finds nothing, do not wheel here: Roblox can
                    # route a wheel over a flat card to the outer carousel.
                    # Only a live scrollbar match is allowed to move this
                    # card; otherwise it is treated as a non-scrollable card.
                    scroll_mode = (
                        "drag" if drag.get("has_scrollbar", True) else "none")
                    self._audit_event(
                        "card_scroll_mode", card=card_no, mode=scroll_mode,
                        card_box=drag["card"])
                    bottom_scanned = scroll_mode == "none"
                    if scroll_mode == "drag":
                        top_y, _bottom_y, edge_tolerance = (
                            self._card_scroll_edges(drag))
                        if abs(int(drag["from_y"]) - top_y) > edge_tolerance:
                            top_drag = {
                                **drag,
                                "target_y": top_y,
                                "target_name": "top",
                            }
                            self._debug_bounty_log(
                                f"[Debug]   card {card_no} is not at top; "
                                f"rewinding thumb {drag['from_y']}->{top_y}")
                            if not self._drag_inner_scroll_verified(
                                    hwnd, frame, top_drag, card_no, stop_event):
                                self._log(
                                    f"[Macro] Auto Bounty could not verify "
                                    f"the top of card {card_no}; treating "
                                    "the unchanged viewport as an edge and "
                                    "continuing its inspection.")
                            frame = vision.capture_game_bgr(hwnd)
                            frame_ocr_lines = None
                            refreshed = self._refresh_card_drag(
                                frame, drag["card"])
                            if refreshed is None:
                                self._log(
                                    f"[Macro] Auto Bounty could not re-detect "
                                    f"card {card_no} after returning to its "
                                    "top; stopping the board scan safely.")
                                return None
                            drag = refreshed
                    for inner_pass in range(BOUNTY_MAX_INNER_CARD_PASSES):
                        if self._checkpoint(stop_event):
                            return None
                        card_x, card_y, card_w, card_h = drag["card"]
                        self._debug_bounty_log(
                            f"[Debug]   inspecting card {card_no} pass "
                            f"{inner_pass + 1}/{BOUNTY_MAX_INNER_CARD_PASSES}")
                        if detection_only:
                            self._hover_bounty_ref(
                                hwnd, card_x + card_w // 2, card_y + card_h // 2,
                                f"card {card_no} pass {inner_pass + 1}")
                        claims = bounty.detect_claim_buttons(frame, [drag])
                        for claim in claims:
                            self._debug_bounty_log(
                                f"[Debug]   claim candidate card={card_no} "
                                f"screen_ref=({claim['cx']},{claim['cy']})")
                            claim_candidate = claim
                            if detection_only:
                                self._hover_bounty_ref(
                                    hwnd, claim["cx"], claim["cy"],
                                    f"claim card {card_no}")
                        summons = bounty.detect_summon_objectives(frame, [drag])
                        summon_sightings.extend(summons)
                        if detection_only:
                            for summon in summons:
                                self._hover_bounty_ref(
                                    hwnd, summon["cx"], summon["cy"],
                                    f"summon card {card_no}")
                        objectives = [
                            objective for objective in bounty.detect_objectives(frame)
                            if card_x <= objective["cx"] <= card_x + card_w
                            and card_y <= objective["cy"] <= card_y + card_h
                        ]
                        self._audit_event(
                            "card_contents", card=card_no,
                            pass_number=inner_pass + 1,
                            claims=claims,
                            summons=summons, objectives=objectives,
                            scrollbar=drag.get("scrollbar_match"),
                            reference_card=list(drag["card"]))
                        for objective in objectives:
                            self._debug_bounty_log(
                                f"[Debug]   objective candidate card={card_no} "
                                f"pass={inner_pass + 1} "
                                f"kind={objective.get('kind')} "
                                f"target={objective.get('target_wave')} "
                                f"ref=({objective.get('cx')},{objective.get('cy')}) "
                                f"text={objective.get('text', '')!r}")
                            if detection_only:
                                self._hover_bounty_ref(
                                    hwnd, objective["cx"], objective["cy"],
                                    f"objective card {card_no} pass {inner_pass + 1}")
                            if (not detection_only
                                    and not self._bounty_was_attempted(
                                    objective["signature"], attempted)):
                                if mythic_only:
                                    mythic_result = self._ensure_mythic_bounty(
                                        hwnd, stop_event, frame, drag, card_no,
                                        frame_ocr_lines)
                                    mythic_status = mythic_result.get("status")
                                    if mythic_status == "rerolled":
                                        return {
                                            "kind": "mythic_rerolled",
                                            "card": card_no,
                                            "rerolls": mythic_result.get("rerolls", 0),
                                        }
                                    if mythic_status == "ready":
                                        return objective
                                    if mythic_status == "stopped":
                                        return None
                                    return {
                                        "kind": "mythic_blocked",
                                        "reason": f"mythic_{mythic_status}",
                                        "card": card_no,
                                        "rerolls": mythic_result.get("rerolls", 0),
                                    }
                                return objective
                        if scroll_mode == "none":
                            self._debug_bounty_log(
                                f"[Debug]   card {card_no} exhausted: "
                                "no validated private scrollbar")
                            bottom_scanned = True
                            break
                        if scroll_mode == "drag":
                            _top_y, bottom_y, edge_tolerance = (
                                self._card_scroll_edges(drag))
                            if abs(int(drag["from_y"]) - bottom_y) <= edge_tolerance:
                                self._debug_bounty_log(
                                    f"[Debug]   card {card_no} reached its "
                                    f"bottom ({drag['from_y']}≈{bottom_y})")
                                bottom_scanned = True
                                self._audit_event(
                                    "scroll_edge_verified", card=card_no,
                                    edge="bottom", current_y=drag["from_y"],
                                    target_y=bottom_y, tolerance=edge_tolerance)
                                break
                            bottom_drag = {
                                **drag,
                                "target_y": bottom_y,
                                "target_name": "bottom",
                            }
                            moved = self._drag_inner_scroll_verified(
                                hwnd, frame, bottom_drag, card_no, stop_event)
                            if not moved:
                                self._log(
                                    f"[Macro] Auto Bounty could not verify the "
                                    f"bottom of card {card_no}; treating the "
                                    "unchanged viewport as the bottom edge.")
                                bottom_scanned = True
                                break
                            next_frame = vision.capture_game_bgr(hwnd)
                            if next_frame is None:
                                self._debug_bounty_log(
                                    f"[Debug]   card {card_no} paused: no post-drag "
                                    "frame was available")
                                return None
                            frame = next_frame
                            frame_ocr_lines = None
                            refreshed = self._refresh_card_drag(
                                frame, drag["card"])
                            if refreshed is None:
                                self._log(
                                    f"[Macro] Auto Bounty could not re-detect "
                                    f"card {card_no}'s scrollbar after a verified "
                                    "drag; stopping the board scan safely.")
                                return None
                            else:
                                drag = refreshed
                    else:
                        self._debug_bounty_log(
                            f"[Debug]   card {card_no} reached the inner-pass "
                            "safety bound before its bottom was verified")
                        return None
                    if bottom_scanned and claim_candidate is not None:
                        self._debug_bounty_log(
                            f"[Debug]   card {card_no} verified top and bottom; "
                            "claim is now eligible")
                        if not detection_only:
                            return claim_candidate
                    if bottom_scanned and mythic_only and not detection_only:
                        mythic_result = self._ensure_mythic_bounty(
                            hwnd, stop_event, frame, drag, card_no,
                            frame_ocr_lines)
                        mythic_status = mythic_result.get("status")
                        if mythic_status == "rerolled":
                            return {
                                "kind": "mythic_rerolled",
                                "card": card_no,
                                "rerolls": mythic_result.get("rerolls", 0),
                            }
                        if mythic_status == "stopped":
                            return None
                        if mythic_status != "ready":
                            return {
                                "kind": "mythic_blocked",
                                "reason": f"mythic_{mythic_status}",
                                "card": card_no,
                                "rerolls": mythic_result.get("rerolls", 0),
                            }
                if not drags:
                    claims = bounty.detect_claim_buttons(frame, [])
                    if claims:
                        self._debug_bounty_log(
                            f"[Debug] claim candidate without validated card "
                            f"ref=({claims[0]['cx']},{claims[0]['cy']})")
                        if not detection_only:
                            return claims[0]
                    for objective in bounty.detect_objectives(frame):
                        self._debug_bounty_log(
                            f"[Debug]   unscoped objective candidate "
                            f"kind={objective.get('kind')} "
                            f"target={objective.get('target_wave')} "
                            f"ref=({objective.get('cx')},{objective.get('cy')}) "
                            f"text={objective.get('text', '')!r}")
                        if not self._bounty_was_attempted(
                                objective["signature"], attempted):
                            if not detection_only:
                                return objective
                    summon_sightings.extend(
                        bounty.detect_summon_objectives(frame))
            if scroll_no == BOUNTY_HORIZONTAL_SCROLL_STEPS:
                break
            frame = vision.capture_game_bgr(hwnd)
            wm.activate_window(hwnd)
            sx, sy = self._bounty_scroll_hover(hwnd, frame)
            self._mouse.move_to(sx, sy)
            self._mouse.nudge()
            self._mouse.scroll(BOUNTY_HORIZONTAL_WHEEL_DELTA)
            self._debug_bounty_log(
                f"[Debug] Outer scroll step {scroll_no + 1}: "
                f"hover_screen=({sx},{sy}), delta={BOUNTY_HORIZONTAL_WHEEL_DELTA}")
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
            result = {
                **largest,
                "signature": ("summon", largest["target_summons"], 0),
            }
            if detection_only:
                self._debug_bounty_log(
                    f"[Debug] summon candidate target={result['target_summons']} "
                    f"remaining={result['remaining_summons']} "
                    f"ref=({result['cx']},{result['cy']})")
                return None
            return result
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
            if objective and objective.get("kind") == "mythic_rerolled":
                # The board remains open, but every objective reference from
                # the pre-reroll card is stale. Scan the live board again.
                self._audit_event(
                    "mythic_rerolled_rescan", card=objective.get("card"),
                    rerolls=objective.get("rerolls", 0))
                continue
            if objective and objective.get("kind") == "mythic_blocked":
                self._log(
                    f"[Macro] Auto Mythic could not safely process card "
                    f"{objective.get('card')}; leaving it unclaimed.")
                self._audit_event(
                    "mythic_blocked", card=objective.get("card"),
                    reason=objective.get("reason"),
                    rerolls=objective.get("rerolls", 0))
                self._leave_bounty_board(hwnd, stop_event)
                self._set_status(
                    action="Auto Mythic stopped safely; card needs review...")
                return True
            if objective is None:
                frame = vision.capture_game_bgr(hwnd)
                remaining = (
                    bounty.read_bounties_left(frame)
                    if frame is not None else None
                )
                self._audit_frame("board_scan_finished", frame)
                self._audit_event(
                    "board_scan_finished", remaining=remaining,
                    attempted=attempted)
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
