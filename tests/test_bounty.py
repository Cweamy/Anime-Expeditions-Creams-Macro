import cv2
import numpy as np

from core import bounty


def _frame():
    return np.zeros((756, 1152, 3), dtype=np.uint8)


def _link_text(frame, x, y, text="FlowerForest", color=(40, 210, 65)):
    cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)


def test_detects_green_wave_link_from_color_and_nearby_objective():
    frame = _frame()
    bx, by, _bw, _bh = bounty.BOARD_REGION
    _link_text(frame, bx + 80, by + 130)
    lines = [{"text": "Clear Wave 30 of Flower Forest", "x": 60, "y": 92,
              "w": 155, "h": 16, "cx": 137, "cy": 100}]
    found = bounty.detect_objectives(frame, lines)
    assert len(found) == 1
    assert found[0]["kind"] == "infinite"
    assert found[0]["target_wave"] == 30


def test_rejects_decorative_green_without_wave_text():
    frame = _frame()
    bx, by, _bw, _bh = bounty.BOARD_REGION
    _link_text(frame, bx + 80, by + 130)
    assert bounty.detect_objectives(frame, []) == []


def test_skips_completed_green_progress_bar():
    frame = _frame()
    bx, by, _bw, _bh = bounty.BOARD_REGION
    _link_text(frame, bx + 80, by + 130)
    cv2.rectangle(frame, (bx + 72, by + 138), (bx + 175, by + 144), (40, 210, 65), -1)
    lines = [{"text": "Clear Wave 30 of Flower Forest", "x": 60, "y": 92,
              "w": 155, "h": 16, "cx": 137, "cy": 100}]
    assert bounty.detect_objectives(frame, lines) == []


def test_skips_completed_objective_with_green_check_button():
    frame = _frame()
    bx, by, _bw, _bh = bounty.BOARD_REGION
    _link_text(frame, bx + 80, by + 130)
    cv2.rectangle(frame, (bx + 65, by + 180), (bx + 180, by + 205), (40, 210, 65), -1)
    lines = [{"text": "Clear Wave 15 of Fairy King Forest", "x": 60, "y": 92,
              "w": 175, "h": 16, "cx": 147, "cy": 100}]
    assert bounty.detect_objectives(frame, lines) == []


def test_skips_individually_completed_objective_without_card_check(monkeypatch):
    frame = _frame()
    bx, by, _bw, _bh = bounty.BOARD_REGION
    _link_text(frame, bx + 80, by + 130, "RoseKingdom")
    lines = [{"text": "Clear Wave 45 of Rose Kingdom", "x": 60, "y": 92,
              "w": 170, "h": 16, "cx": 145, "cy": 100}]
    monkeypatch.setattr(bounty.ocr_windows, "ocr_lines", lambda _image: lines)
    monkeypatch.setattr(bounty.ocr_windows, "ocr_image", lambda _image: "1/1 (100%)")
    assert bounty.detect_objectives(frame) == []


def test_skips_individually_completed_amber_progress_fill():
    frame = _frame()
    bx, by, _bw, _bh = bounty.BOARD_REGION
    _link_text(frame, bx + 80, by + 130, "RoseKingdom")
    cv2.rectangle(
        frame,
        (bx + 65, by + 151),
        (bx + 180, by + 155),
        (20, 150, 235),
        -1,
    )
    lines = [{"text": "Clear Wave 45 of Rose Kingdom", "x": 60, "y": 92,
              "w": 170, "h": 16, "cx": 145, "cy": 100}]
    assert bounty.detect_objectives(frame, lines) == []


def test_does_not_click_link_until_its_progress_strip_is_visible():
    frame = np.full((756, 1152, 3), (85, 105, 125), dtype=np.uint8)
    bx, by, _bw, _bh = bounty.BOARD_REGION
    _link_text(frame, bx + 780, by + 390, "KingsTomb")
    lines = [{"text": "Clear Wave 30 of King's Tomb", "x": 750, "y": 352,
              "w": 150, "h": 16, "cx": 825, "cy": 360}]

    assert bounty.detect_objectives(frame, lines) == []


def test_detects_completed_card_claim_button_in_dynamic_card_footer():
    frame = _frame()
    card = {"card": (720, 280, 196, 208)}
    cv2.rectangle(frame, (775, 451), (855, 477), (40, 210, 65), -1)

    claims = bounty.detect_claim_buttons(frame, [card])

    assert len(claims) == 1
    assert claims[0]["kind"] == "claim"
    assert claims[0]["card"] == card["card"]
    assert claims[0]["cx"] == 815


def test_does_not_treat_incomplete_gray_card_action_as_claim():
    frame = _frame()
    card = {"card": (720, 280, 196, 208)}
    cv2.rectangle(frame, (775, 451), (855, 477), (80, 80, 80), -1)

    assert bounty.detect_claim_buttons(frame, [card]) == []


def test_detects_cyan_hard_link_using_difficulty_word():
    frame = _frame()
    bx, by, _bw, _bh = bounty.BOARD_REGION
    _link_text(frame, bx + 280, by + 230, "FairyKingForest", (215, 170, 40))
    lines = [{"text": "Complete Fairy King Forest on Hard difficulty", "x": 245, "y": 190,
              "w": 180, "h": 17, "cx": 335, "cy": 198}]
    found = bounty.detect_objectives(frame, lines)
    assert len(found) == 1
    assert found[0]["kind"] == "hard"


def test_destination_map_matching_tolerates_minor_ocr_error():
    assert bounty.match_story_map("FlowerForest - Act 1") == "Flower Forest"
    assert bounty.match_story_map("Fairy King F0rest - Act 1") == "Fairy King Forest"
    assert bounty.match_story_map("unrelated screen") is None


def test_destination_visual_fallback_accepts_observed_15_percent_scale(monkeypatch):
    frame = _frame()
    rng = np.random.default_rng(7)
    template = rng.integers(0, 256, size=(16, 64), dtype=np.uint8)
    scaled = cv2.resize(template, (54, 14), interpolation=cv2.INTER_AREA)
    frame[180:194, 320:374] = cv2.cvtColor(scaled, cv2.COLOR_GRAY2BGR)

    monkeypatch.setattr(bounty.ocr_windows, "ocr_image", lambda _image: "")

    def load(name):
        if name == "Rose Kingdom":
            return [(template, None)]
        raise bounty.vision.TemplateNotFound(name)

    monkeypatch.setattr(bounty.vision, "load_template_grays", load)
    assert bounty.read_destination_map(frame) == "Rose Kingdom"


def test_destination_visual_fallback_does_not_match_board_card_text(monkeypatch):
    frame = _frame()
    rng = np.random.default_rng(8)
    template = rng.integers(0, 256, size=(16, 64), dtype=np.uint8)
    frame[430:446, 320:384] = cv2.cvtColor(template, cv2.COLOR_GRAY2BGR)

    monkeypatch.setattr(bounty.ocr_windows, "ocr_image", lambda _image: "")

    def load(name):
        if name == "Rose Kingdom":
            return [(template, None)]
        raise bounty.vision.TemplateNotFound(name)

    monkeypatch.setattr(bounty.vision, "load_template_grays", load)
    assert bounty.read_destination_map(frame) is None


def test_objective_signature_tolerates_tiny_rendering_difference():
    assert bounty.same_signature(("infinite", 30, 0b101010), ("infinite", 30, 0b101011))
    assert not bounty.same_signature(("infinite", 30, 0b101010), ("infinite", 45, 0b101010))
