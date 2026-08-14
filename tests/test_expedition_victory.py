"""Expedition has to notice a Victory it did not cause.

Extraction is a party decision. When the others take it the match is over
and the result screen comes up regardless of what this client chose --
which matters most in exactly the case the play-on logic creates, where the
macro has given up extracting and is deliberately continuing every
checkpoint.

Story and Raid have always watched for Victory. Expedition never did: its
only route to "win" was its own extract confirming. So a party that
extracted left the run clicking at checkpoints that no longer existed,
until MATCH_RESULT_TIMEOUT.
"""
import threading
from unittest.mock import MagicMock

from core import runner_expedition as rx
from core.runner import MacroRunner


def _runner(monkeypatch, *, on_screen):
    """on_screen: which result image, if any, the game is showing."""
    r = MacroRunner(MagicMock(), MagicMock(), MagicMock())
    r.logs = []
    r._log = r.logs.append
    r._checkpoint = lambda _stop: False
    r._debug_save = lambda *_a, **_k: None
    r._find_start_game_button = lambda _hwnd: (None, None)
    r._dismiss_reward_card_if_found = lambda _hwnd: False
    r._expedition_color_buttons = True
    r._is_expedition_match = True

    monkeypatch.setattr(rx.time, "sleep", lambda _s: None)
    monkeypatch.setattr(rx.vision, "find_image",
                        lambda h, n, **k: {"cx": 1, "cy": 1, "score": 1.0} if n == on_screen else None)
    # No checkpoint on the board -- the result screen replaced it.
    monkeypatch.setattr(rx.vision, "find_color_run", lambda *_a, **_k: None)
    return r


def test_a_victory_the_party_caused_ends_the_run(monkeypatch):
    """The regression: without this the run keeps clicking at checkpoints
    that are no longer there, for the rest of MATCH_RESULT_TIMEOUT."""
    r = _runner(monkeypatch, on_screen="victory")

    assert r._check_expedition_wave_result(1, threading.Event()) == "win"
    assert any("Victory screen found" in m for m in r.logs)


def test_a_defeat_still_reports_a_loss(monkeypatch):
    """The victory check sits next to defeat and must not shadow it."""
    r = _runner(monkeypatch, on_screen="defeat")

    assert r._check_expedition_wave_result(1, threading.Event()) == "loss"


def test_mid_run_with_neither_screen_keeps_polling(monkeypatch):
    r = _runner(monkeypatch, on_screen=None)

    assert r._check_expedition_wave_result(1, threading.Event()) is None


def test_a_missing_victory_crop_does_not_break_the_poll(monkeypatch):
    """Optional/best-effort like every other template here."""
    r = _runner(monkeypatch, on_screen=None)
    monkeypatch.setattr(rx.vision, "find_image",
                        lambda h, n, **k: (_ for _ in ()).throw(rx.vision.TemplateNotFound(n)))

    assert r._check_expedition_wave_result(1, threading.Event()) is None
