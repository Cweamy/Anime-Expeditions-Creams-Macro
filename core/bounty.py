"""Visual parsing helpers for the Event Bounty Board.

The board renders supported objective destinations as colored text: green
links open Infinite and cyan links open a Story stage whose Hard difficulty
must be selected. Color finds the clickable label; OCR is used only to
validate the nearby objective and read its wave/map.
"""
import re
from collections import Counter
from difflib import SequenceMatcher

import cv2
import numpy as np

from . import ocr_windows
from . import vision

STORY_MAPS = ("School Grounds", "Rose Kingdom", "Fairy King Forest", "King's Tomb", "Flower Forest")
BOARD_REGION = (170, 150, 970, 470)
_GREEN_LO = np.array((35, 105, 75), dtype=np.uint8)
_GREEN_HI = np.array((90, 255, 255), dtype=np.uint8)
_CYAN_LO = np.array((82, 90, 70), dtype=np.uint8)
_CYAN_HI = np.array((118, 255, 255), dtype=np.uint8)
# Bounty destinations have now been observed rendering about 8% and 15%
# smaller/larger across two otherwise-correct 1080p/100%-scale computers.
# This fallback is deliberately local to the destination heading and keeps
# the normal 0.90 confidence floor; it does not change coordinates or widen
# matching globally.
_DESTINATION_SCALE_FACTORS = (1.0, 0.92, 1.08, 0.85, 1.15)
_DESTINATION_MATCH_THRESHOLD = 0.90


def _colored_components(board_bgr: np.ndarray, lower, upper, kind: str) -> list:
    hsv = cv2.cvtColor(board_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, lower, upper)
    joined = cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 2)))
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(joined)
    found = []
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        if area < 90 or w < 35 or h < 5 or h > 22:
            continue
        if np.count_nonzero(mask[y:y + h, x:x + w]) / float(w * h) > 0.75:
            continue
        glyphs = cv2.resize(mask[y:y + h, x:x + w], (17, 8), interpolation=cv2.INTER_AREA)
        bits = (glyphs[:, 1:] > glyphs[:, :-1]).flatten()
        visual_id = sum(int(value) << index for index, value in enumerate(bits))
        found.append({
            "kind": kind, "x": x, "y": y, "w": w, "h": h,
            "cx": x + w // 2, "cy": y + h // 2, "visual_id": visual_id,
        })
    return found


def detect_objectives(frame_bgr: np.ndarray, ocr_lines=None) -> list:
    """Return supported, incomplete visible objectives in reference coordinates."""
    bx, by, bw, bh = BOARD_REGION
    board = frame_bgr[by:by + bh, bx:bx + bw]
    if board.size == 0:
        return []
    lines = ocr_lines if ocr_lines is not None else ocr_windows.ocr_lines(board)
    links = (
        _colored_components(board, _GREEN_LO, _GREEN_HI, "infinite")
        + _colored_components(board, _CYAN_LO, _CYAN_HI, "hard")
    )
    objectives = []
    board_hsv = cv2.cvtColor(board, cv2.COLOR_BGR2HSV)
    green_mask = cv2.inRange(board_hsv, _GREEN_LO, _GREEN_HI)
    for link in links:
        x1 = max(0, link["x"] - 18)
        x2 = min(board.shape[1], link["x"] + link["w"] + 18)
        y1 = min(board.shape[0], link["y"] + link["h"] + 2)
        # Completion is rendered in two forms depending on the card: a
        # filled progress strip directly under the objective and a broad
        # green check button near the card footer. Include both without
        # relying on the check's exact fixed coordinate (cards scroll).
        y2 = min(board.shape[0], y1 + 90)
        below = green_mask[y1:y2, x1:x2]
        if below.size:
            count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(below)
            completed = any(
                int(stats[i, cv2.CC_STAT_WIDTH]) >= max(50, int(link["w"] * 0.7))
                and int(stats[i, cv2.CC_STAT_HEIGHT]) >= 3
                for i in range(1, count)
            )
            if completed:
                continue

        # Individual 0/1 objectives render their completed progress as a
        # long saturated amber/red fill. The empty state is a black track.
        # This is more reliable than OCR for the tiny "1/1 (100%)" text and
        # remains per-objective (unlike the whole-card footer check).
        sx1 = max(0, link["x"] - 20)
        sx2 = min(board.shape[1], link["x"] + link["w"] + 20)
        sy1 = min(board.shape[0], link["y"] + link["h"] + 1)
        sy2 = min(board.shape[0], sy1 + 30)
        status = board[sy1:sy2, sx1:sx2]
        if status.size:
            status_hsv = cv2.cvtColor(status, cv2.COLOR_BGR2HSV)
            filled = cv2.inRange(
                status_hsv,
                np.array((0, 100, 100), dtype=np.uint8),
                np.array((35, 255, 255), dtype=np.uint8),
            )
            # The tiny black "1/1 (100%)" text cuts the otherwise solid
            # completion fill into several short pieces. Join only nearby
            # horizontal pieces inside this objective's narrow status strip.
            filled = cv2.morphologyEx(
                filled,
                cv2.MORPH_CLOSE,
                cv2.getStructuringElement(cv2.MORPH_RECT, (7, 3)),
            )
            count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(filled)
            if any(
                    int(stats[i, cv2.CC_STAT_WIDTH]) >= max(45, int(link["w"] * 0.65))
                    and int(stats[i, cv2.CC_STAT_HEIGHT]) >= 2
                    for i in range(1, count)):
                continue

        # A card can contain several objectives and only gets the large
        # footer check after ALL of them are complete. Each individual
        # objective still shows "1/1 (100%)" beneath its link, often on an
        # amber/red progress bar rather than a green one. Read that small
        # status strip directly so a completed Rose objective is skipped
        # even while another objective on the same card remains unfinished.
        progress_texts = []
        if ocr_lines is None:
            px1 = max(0, link["x"] - 35)
            px2 = min(board.shape[1], link["x"] + link["w"] + 35)
            py1 = max(0, link["y"] + link["h"] - 3)
            py2 = min(board.shape[0], py1 + 34)
            progress = board[py1:py2, px1:px2]
            if progress.size:
                for scale in (2, 3):
                    enlarged = cv2.resize(
                        progress, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                    read = ocr_windows.ocr_image(enlarged)
                    if read:
                        progress_texts.append(read)
        progress_text = " ".join(progress_texts)
        if (re.search(r"\b1\s*/\s*1\b", progress_text)
                or re.search(r"\b100\s*%?", progress_text)):
            continue

        # Do not click a destination merely because its colored link is
        # visible. Near the bottom of a scrollable card the link can appear
        # while its 0/1 progress strip is still clipped, so we cannot yet
        # know whether that particular objective is complete. Returning no
        # objective here lets _find_next_bounty scroll the card and inspect
        # it again. This is visual and card-relative; it does not depend on
        # a map name or a fixed card/click position.
        progress_visible = bool(re.search(r"\b\d+\s*/\s*\d+\b", progress_text))
        if status.size and not progress_visible:
            dark = cv2.inRange(cv2.cvtColor(status, cv2.COLOR_BGR2GRAY), 0, 45)
            dark = cv2.morphologyEx(
                dark,
                cv2.MORPH_CLOSE,
                cv2.getStructuringElement(cv2.MORPH_RECT, (7, 2)),
            )
            count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(dark)
            progress_visible = any(
                int(stats[i, cv2.CC_STAT_WIDTH]) >= max(45, int(link["w"] * 0.65))
                and int(stats[i, cv2.CC_STAT_HEIGHT]) >= 3
                for i in range(1, count)
            )
        if not progress_visible:
            continue

        nearby = []
        for line in lines:
            if abs(int(line["cy"]) - link["cy"]) <= 42 and (
                    int(line["x"]) < link["x"] + link["w"] + 45
                    and int(line["x"]) + int(line["w"]) > link["x"] - 45):
                nearby.append(line.get("text", ""))
        text = " ".join(nearby)
        local_texts = []
        if ocr_lines is None:
            x1, y1 = max(0, link["x"] - 35), max(0, link["y"] - 30)
            x2 = min(board.shape[1], link["x"] + link["w"] + 35)
            local = board[y1:link["y"] + 5, x1:x2]
            if local.size:
                for scale in (2, 3):
                    enlarged = cv2.resize(
                        local, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                    read = ocr_windows.ocr_image(enlarged)
                    if read:
                        local_texts.append(read)
                text = f"{text} {' '.join(local_texts)}"

        wave_value = None
        if link["kind"] == "infinite":
            votes = []
            for read in local_texts:
                numbers = re.findall(r"\d{1,3}", read)
                if numbers:
                    votes.append(int(numbers[-1]))
            clean = re.findall(r"clear\s*\W*(?:w(?:ave|ave|ve|e))?\s*(\d+)", text, re.I)
            votes.extend(int(value) for value in clean)
            if not votes and "clear" in text.lower():
                votes.extend(int(value) for value in re.findall(r"\d{1,3}", text))
            if votes:
                counts = Counter(votes)
                wave_value = max(counts, key=lambda value: (counts[value], value))
        if link["kind"] == "infinite" and wave_value is None:
            continue
        if link["kind"] == "hard" and "difficulty" not in text.lower():
            continue

        item = {
            **link,
            "x": bx + link["x"], "y": by + link["y"],
            "cx": bx + link["cx"], "cy": by + link["cy"],
            "text": re.sub(r"\s+", " ", text).strip(),
            "target_wave": wave_value,
        }
        item["signature"] = (item["kind"], item["target_wave"], item["visual_id"])
        objectives.append(item)
    return sorted(objectives, key=lambda item: (item["cx"], item["cy"]))


def same_signature(left: tuple, right: tuple) -> bool:
    """Whether two sightings are the same objective despite tiny resampling."""
    if left[:2] != right[:2]:
        return False
    return (int(left[2]) ^ int(right[2])).bit_count() <= 3


def detect_card_scrolls(frame_bgr: np.ndarray) -> list:
    """Locate visible parchment cards and derive their internal scrollbar drags."""
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    parchment = cv2.inRange(
        hsv, np.array((5, 25, 90), np.uint8), np.array((30, 180, 255), np.uint8))
    parchment = cv2.morphologyEx(
        parchment, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(parchment)
    drags = []
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        if not (280 <= x <= 1135 and 150 <= y <= 675):
            continue
        if not (165 <= w <= 220 and 175 <= h <= 245 and area >= 15000):
            continue
        drags.append({
            "x": x + w - 31,
            "from_y": y + 58,
            "to_y": y + h - 58,
            "card": (x, y, w, h),
        })
    return sorted(drags, key=lambda item: item["x"])


def detect_claim_buttons(frame_bgr: np.ndarray, cards=None) -> list:
    """Return dynamically located claim buttons on fully completed cards."""
    cards = detect_card_scrolls(frame_bgr) if cards is None else cards
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, _GREEN_LO, _GREEN_HI)
    claims = []
    for item in cards:
        x, y, w, h = item["card"]
        # Card actions live in its footer. Restricting detection to that
        # footer keeps green destination labels from being mistaken for the
        # broad green check/claim button.
        fy1 = max(0, y + h - 55)
        fy2 = min(frame_bgr.shape[0], y + h - 5)
        footer = green[fy1:fy2, x:x + w]
        if footer.size == 0:
            continue
        footer = cv2.morphologyEx(
            footer,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3)),
        )
        count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(footer)
        for i in range(1, count):
            rx, ry, rw, rh, area = (int(v) for v in stats[i])
            if rw < 55 or rh < 14 or area < 500:
                continue
            claims.append({
                "kind": "claim",
                "x": x + rx,
                "y": fy1 + ry,
                "w": rw,
                "h": rh,
                "cx": x + rx + rw // 2,
                "cy": fy1 + ry + rh // 2,
                "card": item["card"],
            })
    return sorted(claims, key=lambda item: item["cx"])


def read_reward_overlay(frame_bgr: np.ndarray) -> dict:
    """Read the post-claim "Obtained Rewards" overlay and its close target."""
    lines = ocr_windows.ocr_lines(frame_bgr)
    # The board's permanent help text also contains the word "rewards".
    # Require the centered overlay title instead of accepting that generic
    # substring, otherwise a successfully claimed (now disabled) card is
    # mistaken for an overlay that never closes.
    reward_line = None
    reward_score = 0.0
    for line in lines:
        normalized = re.sub(r"[^a-z]", "", line.get("text", "").lower())
        score = SequenceMatcher(None, normalized, "obtainedrewards").ratio()
        if 180 <= int(line.get("cy", 0)) <= 380 and score > reward_score:
            reward_line = line
            reward_score = score
    if reward_score < 0.72:
        reward_line = None
    if reward_line is None:
        return None
    close_line = next(
        (line for line in lines
         if "click" in line.get("text", "").lower()
         and "close" in line.get("text", "").lower()),
        None,
    )

    candidates = []
    lower_bound = int(reward_line["cy"]) + 25
    upper_bound = int(close_line["cy"]) - 15 if close_line else frame_bgr.shape[0] * 3 // 4
    for line in lines:
        text = line.get("text", "")
        if not (lower_bound <= int(line["cy"]) <= upper_bound):
            continue
        if not re.search(r"[A-Za-z]", text):
            continue
        x1 = max(0, int(line["x"]) - 30)
        y1 = max(0, int(line["y"]) - 20)
        x2 = min(frame_bgr.shape[1], int(line["x"]) + int(line["w"]) + 30)
        y2 = min(frame_bgr.shape[0], int(line["y"]) + int(line["h"]) + 20)
        crop = frame_bgr[y1:y2, x1:x2]
        reads = []
        for scale in (2, 3, 4):
            enlarged = cv2.resize(
                crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            read = ocr_windows.ocr_image(enlarged)
            if read:
                reads.append(read)
        reads.append(text)
        cleaned = [
            re.sub(r"[^A-Za-z0-9 ]+", " ", read).strip()
            for read in reads
        ]
        cleaned = [read for read in cleaned if re.search(r"[A-Za-z]{3}", read)]
        if cleaned:
            candidates.append((line, max(cleaned, key=lambda read: (
                " " in read, len(read)))))

    item_line, item_name = candidates[0] if candidates else (None, "Reward")
    quantity = None
    if item_line is not None:
        x1 = max(0, int(item_line["x"]) - 40)
        x2 = min(frame_bgr.shape[1], int(item_line["x"]) + int(item_line["w"]) + 10)
        y1 = max(0, int(item_line["y"]) - 85)
        y2 = max(y1 + 1, int(item_line["y"]) - 25)
        badge = frame_bgr[y1:y2, x1:x2]
        badge_reads = []
        for scale in (3, 4, 5):
            enlarged = cv2.resize(
                badge, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            read = ocr_windows.ocr_image(enlarged)
            if read:
                badge_reads.append(read.lower())
        for read in badge_reads:
            for token in re.findall(r"[0-9so]+x", read):
                normalized = token[:-1].replace("s", "5").replace("o", "0")
                if normalized.isdigit():
                    quantity = int(normalized)
                    break
            if quantity is not None:
                break

    description = f"{quantity}x {item_name}" if quantity is not None else item_name
    target = close_line or reward_line
    return {
        "item": item_name,
        "quantity": quantity,
        "description": description,
        "close_cx": int(target["cx"]),
        "close_cy": int(target["cy"]),
    }


def match_story_map(text: str) -> str:
    """Fuzzy-match an OCRed destination title to a supported Story map."""
    normalized = re.sub(r"[^a-z0-9]+", "", (text or "").lower())
    if not normalized:
        return None
    scored = []
    for name in STORY_MAPS:
        target = re.sub(r"[^a-z0-9]+", "", name.lower())
        if target in normalized:
            return name
        # OCR returns the entire stage-detail panel, not only its heading.
        # Comparing that long sentence directly with a short map name makes
        # one heading typo ("King's Tonb") look far less similar than it is.
        # Score same-length windows as well, so unrelated surrounding text
        # does not drown out a strong local map-name match.
        window_scores = (
            SequenceMatcher(
                None,
                normalized[start:start + len(target)],
                target,
            ).ratio()
            for start in range(max(1, len(normalized) - len(target) + 1))
        )
        score = max(
            SequenceMatcher(None, normalized, target).ratio(),
            max(window_scores),
        )
        scored.append((score, name))
    score, name = max(scored)
    return name if score >= 0.72 else None


def read_destination_map(frame_bgr: np.ndarray) -> str:
    """Read the Story stage-detail heading after a bounty link is clicked."""
    # Search bounds, not click coordinates. Kept above the board-card rows so
    # a click that only opened the bounty tooltip cannot falsely recognize
    # the same colored map name still sitting on the board.
    title = frame_bgr[120:340, 180:1020]
    read = match_story_map(ocr_windows.ocr_image(title))
    if read:
        return read

    title_gray = cv2.cvtColor(title, cv2.COLOR_BGR2GRAY)
    for scale in _DESTINATION_SCALE_FACTORS:
        for map_name in STORY_MAPS:
            try:
                variants = vision.load_template_grays(map_name)
            except vision.TemplateNotFound:
                continue
            for template_gray, mask in variants:
                if scale != 1.0:
                    h, w = template_gray.shape[:2]
                    width = max(1, round(w * scale))
                    height = max(1, round(h * scale))
                    interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
                    template_gray = cv2.resize(
                        template_gray, (width, height), interpolation=interpolation)
                    if mask is not None:
                        mask = cv2.resize(mask, (width, height), interpolation=cv2.INTER_NEAREST)
                if vision.find_in_gray(
                        title_gray, template_gray, _DESTINATION_MATCH_THRESHOLD, mask) is not None:
                    return map_name
    return None
