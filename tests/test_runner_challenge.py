import threading

import numpy as np
import pytest

from core import ocr
from core import vision
from core import ocr_windows
from core.runner_challenge import ChallengeOps
from core.runner_constants import CHALLENGE_STORY_MAPS


class ChallengeProbe:
    def __init__(self):
        self.logs = []
        self.debug_calls = []

    def _debug_save(self, hwnd, name, match):
        self.debug_calls.append((hwnd, name, match))
        return None

    def _log(self, message):
        self.logs.append(message)


def test_detect_current_challenge_map_uses_ordered_candidate_search(monkeypatch):
    """Challenge map detection delegates its ordered alternatives to vision."""
    probe = ChallengeProbe()
    match = {"score": 0.95}
    calls = []

    def find_image_any(hwnd, names):
        calls.append((hwnd, names))
        return match, "King's Tomb"

    monkeypatch.setattr(vision, "find_image_any", find_image_any)

    detected = ChallengeOps._detect_current_challenge_map(probe, 123)

    assert detected == "King's Tomb"
    assert calls == [(123, CHALLENGE_STORY_MAPS)]
    assert probe.debug_calls == [(123, "King's Tomb", match)]


def test_detect_current_challenge_map_handles_missing_templates(monkeypatch):
    """Missing challenge templates remain a non-fatal detection miss."""
    probe = ChallengeProbe()

    def find_image_any(hwnd, names):
        raise vision.TemplateNotFound("missing")

    monkeypatch.setattr(vision, "find_image_any", find_image_any)

    assert ChallengeOps._detect_current_challenge_map(probe, 123) is None


def test_daily_challenge_counts_as_ready_without_regular_challenge():
    probe = ChallengeProbe()
    probe._get_challenge_settings = lambda: {
        "enabled": False,
        "daily": {"enabled": True, "ready": True},
    }

    assert ChallengeOps._challenge_has_ready_stage(probe) is True


class DailyEntryProbe(ChallengeProbe):
    def __init__(self):
        super().__init__()
        self.clicked = []
        self._open_challenge_screen = lambda *_args: True
        self._set_status = lambda **_kwargs: None
        self._checkpoint = lambda _stop: False
        self._recover_to_lobby = lambda *_args: True
        self._enter_selected_challenge = lambda *_args, **_kwargs: True

    def _click_found_image(self, _hwnd, name, _timeout, _stop, *args, **kwargs):
        self.clicked.append(name)
        return {"score": 0.99}


def test_daily_challenge_clicks_tab_then_stage_card(monkeypatch):
    probe = DailyEntryProbe()
    monkeypatch.setattr(vision, "find_image", lambda *_args, **_kwargs: None)

    result = ChallengeOps._enter_daily_challenge_stage(
        probe, 123, threading.Event(), "solo", {}, {})

    assert result == "entered"
    assert probe.clicked == ["daily_challenge_available", "daily_challenge_stage"]


def test_daily_challenge_unavailable_returns_to_lobby_without_clicking(monkeypatch):
    probe = DailyEntryProbe()
    monkeypatch.setattr(
        vision, "find_image",
        lambda *_args, **_kwargs: {"score": 0.98},
    )

    result = ChallengeOps._enter_daily_challenge_stage(
        probe, 123, threading.Event(), "solo", {}, {})

    assert result == "unavailable"
    assert probe.clicked == []


def test_daily_challenge_unavailable_marks_current_game_day_complete():
    probe = ChallengeProbe()
    marked = []
    probe._get_challenge_settings = lambda: {
        "enabled": False,
        "play_mode": "solo",
        "daily": {"enabled": True, "ready": True},
    }
    probe._run_one_daily_challenge = lambda *_args: "unavailable"
    probe._checkpoint = lambda _stop: False
    probe._mark_challenge_stage_played = lambda stage: marked.append(stage)

    ChallengeOps._run_challenges(probe, 123, threading.Event(), {}, {}, {})

    assert marked == ["daily"]


def test_regular_challenge_finishes_slots_1_2_3_in_one_ordered_pass():
    probe = ChallengeProbe()
    played = []
    marked = []
    reads = 0

    def settings():
        nonlocal reads
        reads += 1
        # Simulate live state changing after slot 1. The pass must retain
        # the work it accepted at entry instead of returning to the task.
        ready = reads == 1
        return {
            "enabled": True,
            "play_mode": "solo",
            "daily": {"enabled": False, "ready": False},
            "cap": 0,
            "stages": {
                slot: {"enabled": True, "ready": ready, "count": 0}
                for slot in ("1", "2", "3")
            },
        }

    probe._get_challenge_settings = settings
    probe._checkpoint = lambda _stop: False
    probe._run_one_challenge_stage = (
        lambda _hwnd, _stop, slot, *_args: played.append(slot) or "win")
    probe._mark_challenge_stage_played = lambda slot: marked.append(slot)

    ChallengeOps._run_challenges(probe, 123, threading.Event(), {}, {}, {})

    assert played == ["1", "2", "3"]
    assert marked == ["1", "2", "3"]


@pytest.mark.parametrize(
    ("ocr_text", "expected"),
    (
        ("Grands - Act 1", "School Grounds"),
        ("Kingdm - Act 1", "Rose Kingdom"),
        ("Fary King Forest", "Fairy King Forest"),
        ("Tornb - Act 1", "King's Tomb"),
        ("Flovver Forest", "Flower Forest"),
        ("Eest Town - Act 1", "East Town"),
    ),
)
def test_challenge_map_ocr_uses_unique_map_words(monkeypatch, ocr_text, expected):
    probe = ChallengeProbe()
    frame = np.zeros((756, 1152, 3), dtype=np.uint8)
    monkeypatch.setattr(vision, "capture_game_bgr", lambda _hwnd: frame)
    monkeypatch.setattr(
        vision,
        "find_in_gray_multiscale",
        lambda *_args, **_kwargs: {"x": 895, "y": 348, "w": 123, "h": 21},
    )
    monkeypatch.setattr(ocr, "get_pytesseract", lambda: (_ for _ in ()).throw(ocr.TesseractNotAvailable()))
    monkeypatch.setattr(ocr, "ocr_mask", lambda _engine, _image, _config: ocr_text)

    assert ChallengeOps._detect_challenge_map_ocr(probe, 123) == expected


def test_challenge_map_ocr_does_not_guess_from_shared_forest_word(monkeypatch):
    probe = ChallengeProbe()
    frame = np.zeros((756, 1152, 3), dtype=np.uint8)
    monkeypatch.setattr(vision, "capture_game_bgr", lambda _hwnd: frame)
    monkeypatch.setattr(
        vision,
        "find_in_gray_multiscale",
        lambda *_args, **_kwargs: {"x": 895, "y": 348, "w": 123, "h": 21},
    )
    monkeypatch.setattr(ocr, "get_pytesseract", lambda: (_ for _ in ()).throw(ocr.TesseractNotAvailable()))
    monkeypatch.setattr(ocr, "ocr_mask", lambda _engine, _image, _config: "Forest - Act 1")

    assert ChallengeOps._detect_challenge_map_ocr(probe, 123) is None


def test_challenge_map_ocr_uses_hud_crop_before_fixed_fallback(monkeypatch):
    probe = ChallengeProbe()
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    seen_shapes = []
    monkeypatch.setattr(vision, "capture_game_bgr", lambda _hwnd: frame)
    monkeypatch.setattr(
        vision,
        "find_in_gray_multiscale",
        lambda *_args, **_kwargs: {"x": 100, "y": 20, "w": 20, "h": 10},
    )
    monkeypatch.setattr(ocr, "get_pytesseract", lambda: (_ for _ in ()).throw(ocr.TesseractNotAvailable()))

    def fake_ocr(_engine, image, _config):
        seen_shapes.append(image.shape[:2])
        return "School Grounds"

    monkeypatch.setattr(ocr, "ocr_mask", fake_ocr)

    assert ChallengeOps._detect_challenge_map_ocr(probe, 123) == "School Grounds"
    assert seen_shapes[0] == (43 * 8, 85 * 8)


def test_challenge_map_ocr_uses_fixed_fallback_when_hud_absent(monkeypatch):
    probe = ChallengeProbe()
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    seen_shapes = []
    monkeypatch.setattr(vision, "capture_game_bgr", lambda _hwnd: frame)
    monkeypatch.setattr(vision, "find_in_gray_multiscale", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ocr, "get_pytesseract", lambda: (_ for _ in ()).throw(ocr.TesseractNotAvailable()))

    def fake_ocr(_engine, image, _config):
        seen_shapes.append(image.shape[:2])
        return "Rose Kingdom"

    monkeypatch.setattr(ocr, "ocr_mask", fake_ocr)

    assert ChallengeOps._detect_challenge_map_ocr(probe, 123) == "Rose Kingdom"
    assert seen_shapes[0] == (10 * 8, 81 * 8)
