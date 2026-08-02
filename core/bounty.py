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
BOUNTY_COUNT_REGION = (950, 55, 202, 100)
SUMMON_LOBBY_REGION = (65, 385, 105, 85)
_SUMMON_CARD_SIZE = (210, 230)
_SUMMON_TARGET_MIN = 50
_SUMMON_TARGET_MAX = 1000
_CARD_RARITIES = (
    "common", "uncommon", "rare", "epic", "legendary", "mythic",
)
_GOLD_REROLL_LO = np.array((10, 105, 80), dtype=np.uint8)
_GOLD_REROLL_HI = np.array((35, 255, 255), dtype=np.uint8)


def _word_similarity(word: str, wanted: str) -> float:
    return SequenceMatcher(
        None,
        re.sub(r"[^a-z]", "", (word or "").lower()),
        wanted,
    ).ratio()


def _classify_card_rarity_texts(texts) -> str | None:
    """Classify rarity from OCR text already scoped to one card title."""
    best = []
    for raw in texts or []:
        words = re.findall(r"[A-Za-z]{3,14}", str(raw or ""))
        bounty_score = max(
            (_word_similarity(word, "bounty") for word in words),
            default=0.0,
        )
        for word in words:
            rarity, score = max(
                ((_name, _word_similarity(word, _name))
                 for _name in _CARD_RARITIES),
                key=lambda item: item[1],
            )
            title_bonus = 0.12 if bounty_score >= 0.50 else 0.0
            best.append((score + title_bonus, rarity, score))
    if not best:
        return None
    _score, rarity, word_score = max(best, key=lambda item: item[0])
    if word_score < 0.62:
        return None
    return "mythic" if rarity == "mythic" else "other"


def read_card_rarity(frame_bgr: np.ndarray, card, ocr_lines=None) -> str | None:
    """Read one bounty card's rarity as ``mythic``, ``other``, or ``None``."""
    if (frame_bgr is None or not hasattr(frame_bgr, "shape")
            or not card or len(card) < 4):
        return None
    x, y, w, h = (int(value) for value in card[:4])
    if w <= 0 or h <= 0:
        return None
    title_top = y + max(0, int(round(h * 0.06)))
    title_bottom = min(y + h, y + max(95, int(round(h * 0.42))))
    title_lines = []
    if ocr_lines is None:
        try:
            ocr_lines = ocr_windows.ocr_lines(frame_bgr)
        except Exception:
            ocr_lines = []
    for line in ocr_lines or []:
        try:
            cx = int(line.get("cx", line.get("x", 0)))
            cy = int(line.get("cy", line.get("y", 0)))
        except (TypeError, ValueError):
            continue
        if (x - 10 <= cx <= x + w + 10
                and title_top <= cy <= title_bottom):
            title_lines.append(line.get("text", ""))
    rarity = _classify_card_rarity_texts(title_lines)
    if rarity is not None:
        return rarity

    crop = frame_bgr[
        max(0, title_top):min(frame_bgr.shape[0], title_bottom),
        max(0, x):min(frame_bgr.shape[1], x + w),
    ]
    if crop.size == 0:
        return None
    for scale in (2, 3):
        enlarged = cv2.resize(
            crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        local_texts = []
        try:
            local_texts.extend(
                line.get("text", "")
                for line in ocr_windows.ocr_lines(enlarged)
            )
        except Exception:
            pass
        try:
            local_texts.append(ocr_windows.ocr_image(enlarged))
        except Exception:
            pass
        rarity = _classify_card_rarity_texts(local_texts)
        if rarity is not None:
            return rarity
    return None


def _extract_summon_targets(texts: list) -> list:
    """Return structurally valid targets from OCR variants of one card."""
    targets = []
    for raw in texts:
        for summon_word in re.finditer(r"[A-Za-z]{4,10}", raw):
            if _word_similarity(summon_word.group(), "summon") < 0.55:
                continue
            tail = raw[summon_word.end():summon_word.end() + 45]
            # Do not accept the first nearby number by itself: that turned a
            # mangled progress/currency string into 1,254. A target must be
            # followed by a recognizable form of "times".
            if not any(
                    _word_similarity(word, "times") >= 0.45
                    for word in re.findall(r"[A-Za-z]{3,10}", tail)):
                continue
            for token in re.findall(r"[0-9SOsoILl]{2,5}", tail):
                normalized = token.translate(str.maketrans({
                    "S": "5", "s": "5", "O": "0", "o": "0",
                    "I": "1", "L": "0", "l": "1",
                }))
                if not normalized.isdigit():
                    continue
                value = int(normalized)
                if (
                        _SUMMON_TARGET_MIN <= value <= _SUMMON_TARGET_MAX
                        and value % 50 == 0):
                    targets.append(value)
                    break
            break
    return targets


def _summon_progress_from_bar(
        card_bgr: np.ndarray, summon_cy: int, target: int):
    """Estimate summon progress from the bar directly below its OCR row."""
    if card_bgr is None or card_bgr.size == 0:
        return None
    hsv = cv2.cvtColor(card_bgr, cv2.COLOR_BGR2HSV)
    best_fill = None
    y1 = max(0, int(summon_cy) + 10)
    y2 = min(card_bgr.shape[0], int(summon_cy) + 27)
    minimum_width = max(70, int(card_bgr.shape[1] * 0.42))
    maximum_width = int(card_bgr.shape[1] * 0.80)
    for row_y in range(y1, y2):
        row = hsv[row_y]
        hue, saturation, value = row[:, 0], row[:, 1], row[:, 2]
        warm = (
            (saturation > 100)
            & (value > 80)
            & ((hue < 35) | (hue > 170))
        )
        dark = value < 75
        active = ((warm | dark).astype(np.uint8) * 255)[None, :]
        active = cv2.morphologyEx(
            active,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (7, 1)),
        )
        count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
            active)
        for index in range(1, count):
            x, _y, width, _height, _area = (
                int(value) for value in stats[index])
            if width < minimum_width or width > maximum_width:
                continue
            fill = float(np.count_nonzero(warm[x:x + width])) / width
            best_fill = fill if best_fill is None else max(best_fill, fill)
    if best_fill is None:
        return None
    # Summon progress advances in batches of 50. Rounding the visual fill
    # to that real unit absorbs outlined text cutting holes in the bar
    # (the live 50% bar measures about 46% at its cleanest scanline).
    steps = int(round((best_fill * target) / 50.0))
    return max(0, min(target, steps * 50))


def read_bounties_left(frame_bgr: np.ndarray):
    """Read the board's orange ``remaining / total`` counter."""
    x, y, w, h = BOUNTY_COUNT_REGION
    crop = frame_bgr[y:min(frame_bgr.shape[0], y + h),
                     x:min(frame_bgr.shape[1], x + w)]
    if crop.size == 0:
        return None

    # Windows OCR usually ignores the orange leading digit when the white
    # "Bounties Left" title is present. Isolating the saturated gold/orange
    # counter makes the complete "2 / 10" structure readable in one pass.
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    counter = cv2.inRange(
        hsv,
        np.array((3, 90, 90), dtype=np.uint8),
        np.array((35, 255, 255), dtype=np.uint8),
    )
    for scale in (5, 4, 3):
        enlarged = cv2.resize(
            counter, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        text = ocr_windows.ocr_image(enlarged)
        match = re.search(r"\b(\d{1,2})\s*/\s*(\d{1,2})\b", text)
        if not match:
            continue
        remaining, total = (int(value) for value in match.groups())
        if 0 <= remaining <= total:
            return remaining, total
    return None


def detect_summon_objectives(
        frame_bgr: np.ndarray, cards=None, ocr_lines=None) -> list:
    """Read incomplete ``Summon X times`` objectives from visible cards.

    Summon objectives have no colored destination link, so they cannot use
    :func:`detect_objectives`. Keep the OCR scoped to each dynamically found
    card and require the objective target to agree with a progress
    ``current/target`` denominator. This prevents unrelated currency counts
    and the board-wide bounty counter from becoming summon amounts.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return []
    cards = detect_card_scrolls(frame_bgr) if cards is None else cards
    lines = (
        ocr_windows.ocr_lines(frame_bgr)
        if ocr_lines is None else ocr_lines
    )
    found = []
    for card_index, item in enumerate(cards):
        x, y, w, h = item["card"]
        card_lines = [
            line for line in lines
            if x - 8 <= int(line.get("cx", 0)) <= x + w + 8
            and y - 8 <= int(line.get("cy", 0)) <= y + h + 8
        ]
        texts = [line.get("text", "") for line in card_lines]
        summon_line = None
        summon_line_score = 0.0
        for line in card_lines:
            for word in re.findall(r"[A-Za-z]{4,10}", line.get("text", "")):
                score = _word_similarity(word, "summon")
                if score > summon_line_score:
                    summon_line, summon_line_score = line, score
        if summon_line_score < 0.55:
            summon_line = None
        card_crop = frame_bgr[
            max(0, y):min(frame_bgr.shape[0], y + h),
            max(0, x):min(frame_bgr.shape[1], x + w),
        ]
        normalized_card = None
        normalized_summon_cy = None
        if card_crop.size:
            # Normalize every dynamically detected parchment card to one
            # canonical size before OCR. This cancels the observed +/-8-15%
            # Roblox UI render drift without changing clicks or globally
            # scaling the screen.
            normalized_card = cv2.resize(
                card_crop, _SUMMON_CARD_SIZE, interpolation=cv2.INTER_CUBIC)
            enlarged = cv2.resize(
                normalized_card, None, fx=2, fy=2,
                interpolation=cv2.INTER_CUBIC)
            local_lines = ocr_windows.ocr_lines(enlarged)
            texts.extend(line.get("text", "") for line in local_lines)
            local_best_score = 0.0
            for line in local_lines:
                for word in re.findall(
                        r"[A-Za-z]{4,10}", line.get("text", "")):
                    score = _word_similarity(word, "summon")
                    if score > local_best_score:
                        local_best_score = score
                        normalized_summon_cy = int(line["cy"]) // 2
            if local_best_score < 0.55:
                normalized_summon_cy = None
            # At the observed +15% UI render, the normal 2x OCR can still
            # recognize "Summon" but mangle the tiny trailing "times" word,
            # which makes the structurally validated target disappear. Pay
            # for one 3x retry only on that summon-like failure; ordinary
            # bounty cards keep the single fast OCR pass.
            has_summon_word = any(
                _word_similarity(word, "summon") >= 0.55
                for text in texts
                for word in re.findall(r"[A-Za-z]{4,10}", text)
            )
            if has_summon_word and not _extract_summon_targets(texts):
                retry = cv2.resize(
                    normalized_card, None, fx=3, fy=3,
                    interpolation=cv2.INTER_CUBIC)
                texts.extend(
                    line.get("text", "")
                    for line in ocr_windows.ocr_lines(retry)
                )
        joined = " ".join(texts)
        has_progress = bool(re.search(
            r"(?<!\d)\d{1,4}\s*[/|']\s*\d{2,4}(?!\d)", joined))
        if _extract_summon_targets(texts) and not has_progress:
            # One extra normalized read is paid only for a real summon card
            # whose tiny progress text is still missing.
            enlarged = cv2.resize(
                normalized_card, None, fx=3, fy=3,
                interpolation=cv2.INTER_CUBIC)
            read = ocr_windows.ocr_image(enlarged)
            if read:
                texts.append(read)

        compact = re.sub(r"\s+", " ", " ".join(texts)).strip()
        target_votes = _extract_summon_targets(texts)
        if not target_votes:
            continue
        counts = Counter(target_votes)
        target = max(
            counts, key=lambda value: (counts[value], value))

        progress = [
            (int(current), int(target))
            for current, target in re.findall(
                r"(?<!\d)(\d{1,4})\s*[/|']\s*(\d{2,4})(?!\d)",
                compact)
        ]
        agreeing = [
            current for current, total in progress
            if total == target and 0 <= current <= target
            and current % 50 == 0
        ]
        if normalized_summon_cy is not None:
            bar_current = _summon_progress_from_bar(
                normalized_card, normalized_summon_cy, target)
            if bar_current is not None:
                agreeing.append(bar_current)
        elif summon_line is not None:
            bar_current = _summon_progress_from_bar(
                card_crop, int(summon_line["cy"]) - y, target)
            if bar_current is not None:
                agreeing.append(bar_current)
        if not agreeing:
            # The tiny progress row may be clipped at the bottom of a
            # privately scrollable card. Wait for the caller to drag the
            # card and rescan rather than assuming progress is zero.
            continue
        current = max(agreeing)
        if current >= target:
            continue
        found.append({
            "kind": "summon",
            "target_summons": target,
            "current_summons": current,
            "remaining_summons": target - current,
            "card": item["card"],
            "cx": x + w // 2,
            "cy": y + h // 2,
            "text": compact,
            "signature": ("summon", target, card_index),
        })
    return found


def detect_lobby_summon(frame_bgr: np.ndarray) -> dict:
    """Locate the lobby Summon sidebar label with a focused OCR read."""
    if frame_bgr is None or frame_bgr.size == 0:
        return None
    x, y, w, h = SUMMON_LOBBY_REGION
    crop = frame_bgr[
        y:min(frame_bgr.shape[0], y + h),
        x:min(frame_bgr.shape[1], x + w),
    ]
    if crop.size == 0:
        return None
    scale = 2
    enlarged = cv2.resize(
        crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    best, best_score = None, 0.0
    for line in ocr_windows.ocr_lines(enlarged):
        for word in re.findall(r"[A-Za-z]{4,10}", line.get("text", "")):
            score = SequenceMatcher(
                None, re.sub(r"[^a-z]", "", word.lower()), "summon").ratio()
            if score > best_score:
                best, best_score = line, score
    if best is None or best_score < 0.55:
        return None
    return {
        "text": best.get("text", ""),
        "cx": x + int(best["cx"]) // scale,
        "cy": y + int(best["cy"]) // scale,
        "score": best_score,
    }


def detect_summon_menu(frame_bgr: np.ndarray, ocr_lines=None) -> dict:
    """Locate banner tabs and the live 50x control on the Summon screen."""
    if frame_bgr is None or frame_bgr.size == 0:
        return None
    lines = (
        ocr_windows.ocr_lines(frame_bgr)
        if ocr_lines is None else ocr_lines
    )

    def fuzzy_line(wanted):
        best, best_score = None, 0.0
        target = re.sub(r"[^a-z]", "", wanted.lower())
        for line in lines:
            text = re.sub(r"[^a-z]", "", line.get("text", "").lower())
            score = SequenceMatcher(None, text, target).ratio()
            if score > best_score:
                best, best_score = line, score
        return best if best_score >= 0.62 else None

    standard = fuzzy_line("Standard")
    mini = fuzzy_line("Mini")
    villain = fuzzy_line("Villain")
    # The selected Villain label has proved much less OCR-friendly than the
    # adjacent Standard/Mini labels. Their tabs are evenly spaced, so derive
    # Villain only when both neighboring anchors agree on a plausible row.
    if villain is None and standard is not None and mini is not None:
        spacing = int(mini["cx"]) - int(standard["cx"])
        if 70 <= spacing <= 180 and abs(int(mini["cy"]) - int(standard["cy"])) <= 20:
            villain = {
                "cx": int(standard["cx"]) - spacing,
                "cy": (int(standard["cy"]) + int(mini["cy"])) // 2,
                "text": "Villain (derived)",
            }

    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(
        hsv,
        np.array((35, 100, 90), dtype=np.uint8),
        np.array((95, 255, 255), dtype=np.uint8),
    )
    # Summon action controls are broad green rectangles in the lower half.
    # Restricting the component search keeps lobby scenery and currency
    # icons from entering the candidate set.
    green[:frame_bgr.shape[0] // 2, :] = 0
    green[:, :frame_bgr.shape[1] // 3] = 0
    green = cv2.morphologyEx(
        green, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (7, 5)))
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(green)
    buttons = []
    for index in range(1, count):
        x, y, w, h, area = (int(value) for value in stats[index])
        if w >= 120 and h >= 28 and area >= 2500:
            buttons.append({
                "x": x, "y": y, "w": w, "h": h,
                "cx": x + w // 2, "cy": y + h // 2,
            })
    buttons.sort(key=lambda button: button["cx"])
    summon_50 = buttons[-1] if len(buttons) >= 2 else None
    if len(buttons) == 1 and buttons[0]["w"] >= 280:
        # The two adjacent green actions have no dark gap on some renders,
        # making them one connected component. Split that detected control
        # group in half and target the right-hand 50x action.
        group = buttons[0]
        summon_50 = {
            "x": group["x"] + group["w"] // 2,
            "y": group["y"],
            "w": group["w"] - group["w"] // 2,
            "h": group["h"],
            "cx": group["x"] + (group["w"] * 3) // 4,
            "cy": group["cy"],
        }
    if standard is None or mini is None or summon_50 is None:
        return None
    return {
        "tabs": {
            "standard": standard,
            "villain": villain,
            "mini": mini,
        },
        "summon_50": summon_50,
    }


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
        # Some bounty cards fit all objectives and render no private
        # scrollbar at all. The track, when present, is a narrow dark
        # vertical run just inside the card's right edge. Mark its presence
        # so callers do not repeatedly drag plain parchment on barless cards.
        edge = cv2.cvtColor(
            frame_bgr[y + 50:y + h - 70, x + w - 45:x + w - 20],
            cv2.COLOR_BGR2HSV,
        )
        has_scrollbar = False
        if edge.size:
            dark_columns = (edge[:, :, 2] < 155).mean(axis=0) > 0.55
            has_scrollbar = bool(
                np.convolve(
                    dark_columns.astype(np.uint8),
                    np.ones(5, dtype=np.uint8),
                    mode="valid",
                ).max(initial=0) >= 5
            )
        drags.append({
            "x": x + w - 31,
            "from_y": y + 58,
            "to_y": y + h - 58,
            "card": (x, y, w, h),
            "has_scrollbar": has_scrollbar,
        })
    return sorted(drags, key=lambda item: item["x"])


def detect_reroll_buttons(frame_bgr: np.ndarray, cards=None) -> list:
    """Locate active gold reroll controls inside detected card footers."""
    if (frame_bgr is None or not hasattr(frame_bgr, "shape")
            or getattr(frame_bgr, "size", 0) == 0):
        return []
    cards = detect_card_scrolls(frame_bgr) if cards is None else cards
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    gold = cv2.inRange(hsv, _GOLD_REROLL_LO, _GOLD_REROLL_HI)
    buttons = []
    frame_height, frame_width = frame_bgr.shape[:2]
    for item in cards:
        x, y, w, h = (int(value) for value in item["card"])
        footer_top = max(0, y + h - max(54, int(round(h * 0.30))))
        footer_bottom = min(frame_height, y + h - 4)
        # The gold reroll control is the rightmost footer control. The gray
        # X beside it is outside this right-side search region.
        search_left = max(x, x + w - max(70, int(round(w * 0.40))))
        search_right = min(frame_width, x + w)
        region = gold[footer_top:footer_bottom, search_left:search_right]
        if region.size == 0:
            continue
        region = cv2.morphologyEx(
            region, cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3)),
        )
        count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(region)
        for index in range(1, count):
            rx, ry, rw, rh, area = (int(value) for value in stats[index])
            if (rw < max(12, int(round(w * 0.06)))
                    or rw > max(18, int(round(w * 0.34)))
                    or rh < max(9, int(round(h * 0.045)))
                    or rh > max(24, int(round(h * 0.30)))
                    or area < max(90, int(round(w * h * 0.006)))):
                continue
            absolute_x = search_left + rx
            absolute_y = footer_top + ry
            center_x = absolute_x + rw // 2
            if center_x < x + int(round(w * 0.68)):
                continue
            gold_pixels = int(np.count_nonzero(
                gold[absolute_y:absolute_y + rh,
                     absolute_x:absolute_x + rw]))
            fill_ratio = gold_pixels / float(max(1, rw * rh))
            if fill_ratio < 0.28:
                continue
            buttons.append({
                "kind": "reroll",
                "x": absolute_x,
                "y": absolute_y,
                "w": rw,
                "h": rh,
                "cx": center_x,
                "cy": absolute_y + rh // 2,
                "card": item["card"],
                "score": fill_ratio,
                "detector": "card_relative_gold_footer",
            })
    return sorted(buttons, key=lambda item: (item["card"][0], item["cx"]))


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
