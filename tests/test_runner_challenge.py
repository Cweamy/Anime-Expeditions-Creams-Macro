from core import vision
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
