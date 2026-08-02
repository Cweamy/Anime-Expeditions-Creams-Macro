import cv2
import numpy as np

from core import bounty


def _frame():
    return np.zeros((756, 1152, 3), dtype=np.uint8)


def _link_text(frame, x, y, text="FlowerForest", color=(40, 210, 65)):
    cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)


def test_reads_card_rarity_from_card_local_title_ocr():
    frame = _frame()
    card = (250, 180, 200, 230)
    lines = [{"text": "Mythic Bounty #6", "cx": 350, "cy": 230}]

    assert bounty.read_card_rarity(frame, card, lines) == "mythic"


def test_reads_non_mythic_card_rarity_from_card_local_title_ocr():
    frame = _frame()
    card = (250, 180, 200, 230)
    lines = [{"text": "Legendary Bounty #2", "cx": 350, "cy": 230}]

    assert bounty.read_card_rarity(frame, card, lines) == "other"


def test_reroll_detector_accepts_only_the_gold_right_footer_control():
    frame = _frame()
    card = {"card": (100, 100, 200, 250)}
    cv2.rectangle(frame, (270, 300), (295, 335), (0, 170, 220), -1)

    buttons = bounty.detect_reroll_buttons(frame, [card])

    assert len(buttons) == 1
    assert buttons[0]["kind"] == "reroll"
    assert buttons[0]["detector"] == "card_relative_gold_footer"
    assert buttons[0]["card"] == card["card"]
    assert 270 <= buttons[0]["cx"] <= 295


def test_reads_remaining_bounty_counter_from_orange_mask(monkeypatch):
    frame = _frame()
    reads = iter(["", "2 / 10"])
    monkeypatch.setattr(
        bounty.ocr_windows, "ocr_image", lambda _image: next(reads))

    assert bounty.read_bounties_left(frame) == (2, 10)


def test_rejects_impossible_remaining_bounty_counter(monkeypatch):
    frame = _frame()
    monkeypatch.setattr(
        bounty.ocr_windows, "ocr_image", lambda _image: "12 / 10")

    assert bounty.read_bounties_left(frame) is None


def test_detects_incomplete_summon_objective_from_card_scoped_ocr():
    frame = _frame()
    cards = [{"card": (250, 180, 200, 230)}]
    lines = [
        {"text": "Summon 500 times", "cx": 350, "cy": 250},
        {"text": "150 / 500", "cx": 350, "cy": 280},
        # An unrelated board counter must not affect the objective.
        {"text": "2 / 10 Bounties Left", "cx": 1040, "cy": 80},
    ]

    found = bounty.detect_summon_objectives(frame, cards, lines)

    assert len(found) == 1
    assert found[0]["target_summons"] == 500
    assert found[0]["current_summons"] == 150
    assert found[0]["remaining_summons"] == 350


def test_summon_objective_requires_matching_progress_and_skips_complete():
    frame = _frame()
    cards = [{"card": (250, 180, 200, 230)}]
    incomplete_without_progress = [
        {"text": "Summon 250 times", "cx": 350, "cy": 250},
        {"text": "2 / 10", "cx": 350, "cy": 280},
    ]
    complete = [
        {"text": "Summon 250 times", "cx": 350, "cy": 250},
        {"text": "250 / 250", "cx": 350, "cy": 280},
    ]

    assert bounty.detect_summon_objectives(
        frame, cards, incomplete_without_progress) == []
    assert bounty.detect_summon_objectives(frame, cards, complete) == []


def test_summon_objective_tolerates_live_ocr_spelling_confusion():
    frame = _frame()
    cards = [{"card": (250, 180, 200, 230)}]
    lines = [
        {"text": "Surnrnon 500 tirnes", "cx": 350, "cy": 250},
        {"text": "50 / 500", "cx": 350, "cy": 280},
    ]

    found = bounty.detect_summon_objectives(frame, cards, lines)

    assert len(found) == 1
    assert found[0]["remaining_summons"] == 450


def test_summon_objective_accepts_touching_target_and_apostrophe_progress(
        monkeypatch):
    frame = _frame()
    cards = [{"card": (250, 180, 200, 230)}]
    lines = [
        {"text": "Summcn500 times", "cx": 350, "cy": 250},
        {"text": "0'500", "cx": 350, "cy": 280},
    ]
    monkeypatch.setattr(bounty.ocr_windows, "ocr_image", lambda _image: "")

    found = bounty.detect_summon_objectives(frame, cards, lines)

    assert len(found) == 1
    assert found[0]["target_summons"] == 500
    assert found[0]["remaining_summons"] == 500


def test_summon_card_fallback_recovers_misread_target_and_progress(monkeypatch):
    frame = _frame()
    cards = [{"card": (250, 180, 200, 230)}]
    lines = [{"text": "Summcn 25Ltimes", "cx": 350, "cy": 250}]
    reads = iter([
        "Summon 250 times 0/250 (0%)",
        "Summm 250 times 0/250",
    ])
    monkeypatch.setattr(
        bounty.ocr_windows, "ocr_image", lambda _image: next(reads))

    found = bounty.detect_summon_objectives(frame, cards, lines)

    assert len(found) == 1
    assert found[0]["target_summons"] == 250


def test_summon_card_fallback_runs_when_full_frame_ocr_omits_row(monkeypatch):
    frame = _frame()
    cards = [{"card": (250, 180, 200, 230)}]
    monkeypatch.setattr(
        bounty.ocr_windows,
        "ocr_lines",
        lambda _image: [
            {"text": "Summcn500 times", "cx": 210, "cy": 120},
            {"text": "0'500", "cx": 210, "cy": 145},
        ],
    )

    found = bounty.detect_summon_objectives(frame, cards, [])

    assert len(found) == 1
    assert found[0]["target_summons"] == 500
    assert found[0]["remaining_summons"] == 500


def test_summon_card_retries_larger_ocr_when_times_word_is_mangled(monkeypatch):
    frame = _frame()
    cards = [{"card": (250, 180, 200, 230)}]
    reads = iter([
        [
            {"text": "Summm 2SO ttrne-", "cx": 210, "cy": 120},
            {"text": "0/250", "cx": 210, "cy": 145},
        ],
        [
            {"text": "Summon 250 times", "cx": 315, "cy": 180},
            {"text": "0/250", "cx": 315, "cy": 218},
        ],
    ])
    monkeypatch.setattr(
        bounty.ocr_windows, "ocr_lines", lambda _image: next(reads))

    found = bounty.detect_summon_objectives(frame, cards, [])

    assert len(found) == 1
    assert found[0]["target_summons"] == 250


def test_summon_target_requires_valid_50x_structure():
    assert bounty._extract_summon_targets(["Summon 1254 times"]) == []
    assert bounty._extract_summon_targets(["Summcn 2SO times"]) == [250]
    assert bounty._extract_summon_targets(["Summon SOO times"]) == [500]


def test_lobby_summon_uses_focused_sidebar_ocr(monkeypatch):
    frame = _frame()
    lines = [{"text": "Summon", "cx": 96, "cy": 90}]
    monkeypatch.setattr(
        bounty.ocr_windows, "ocr_lines", lambda _image: lines)

    target = bounty.detect_lobby_summon(frame)

    assert target["cx"] == 113
    assert target["cy"] == 430


def test_summon_progress_falls_back_to_detected_half_filled_bar(monkeypatch):
    frame = _frame()
    x, y, w, h = 250, 180, 200, 230
    cards = [{"card": (x, y, w, h)}]
    summon_cy = y + 55
    lines = [
        {"text": "Summcn 500 times", "cx": x + 100, "cy": summon_cy},
        {"text": "250560150", "cx": x + 100, "cy": summon_cy + 12},
    ]
    cv2.rectangle(
        frame, (x, y), (x + w - 1, y + h - 1), (105, 130, 165), -1)
    # A 120px progress track, half warm fill and half dark remainder.
    cv2.rectangle(
        frame, (x + 38, y + 72), (x + 157, y + 75), (25, 25, 25), -1)
    cv2.rectangle(
        frame, (x + 38, y + 72), (x + 97, y + 75), (20, 150, 235), -1)
    monkeypatch.setattr(bounty.ocr_windows, "ocr_image", lambda _image: "")

    found = bounty.detect_summon_objectives(frame, cards, lines)

    assert len(found) == 1
    assert found[0]["current_summons"] == 250
    assert found[0]["remaining_summons"] == 250


def test_detects_summon_menu_from_tabs_and_green_actions():
    frame = _frame()
    cv2.rectangle(frame, (520, 550), (730, 605), (40, 210, 65), -1)
    cv2.rectangle(frame, (760, 550), (980, 605), (40, 210, 65), -1)
    lines = [
        {"text": "Standard", "cx": 610, "cy": 170},
        {"text": "Mini", "cx": 738, "cy": 170},
    ]

    menu = bounty.detect_summon_menu(frame, lines)

    assert menu is not None
    assert menu["tabs"]["standard"]["cx"] == 610
    assert menu["tabs"]["villain"]["cx"] == 482
    assert menu["summon_50"]["cx"] == 870


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


def test_reads_bounty_reward_overlay_and_close_target(monkeypatch):
    frame = _frame()
    lines = [
        {"text": "Obtained Rewards", "x": 460, "y": 280, "w": 230, "h": 20,
         "cx": 575, "cy": 290},
        {"text": "Event Coin", "x": 550, "y": 399, "w": 60, "h": 10,
         "cx": 580, "cy": 404},
        {"text": "Click anywhere to close", "x": 490, "y": 459, "w": 175, "h": 14,
         "cx": 577, "cy": 466},
    ]
    reads = iter(["Event Coin", "Event Coin", "Event Coin", "sox", "sox", "sox"])
    monkeypatch.setattr(bounty.ocr_windows, "ocr_lines", lambda _frame: lines)
    monkeypatch.setattr(
        bounty.ocr_windows, "ocr_image", lambda _image: next(reads))

    reward = bounty.read_reward_overlay(frame)

    assert reward["description"] == "50x Event Coin"
    assert (reward["close_cx"], reward["close_cy"]) == (577, 466)


def test_board_help_text_is_not_mistaken_for_reward_overlay(monkeypatch):
    frame = _frame()
    lines = [{
        "text": "Spend Gold to reroll a bounty's rarity and objectives for better rewards!",
        "x": 520, "y": 78, "w": 300, "h": 30, "cx": 670, "cy": 93,
    }]
    monkeypatch.setattr(bounty.ocr_windows, "ocr_lines", lambda _frame: lines)

    assert bounty.read_reward_overlay(frame) is None


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
    assert bounty.match_story_map(
        "Infinite King's Tonb - Act 1 Hard Mode Star Missions Clear Wave 10"
    ) == "King's Tomb"
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
