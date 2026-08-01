from unittest.mock import MagicMock

import numpy as np
import pytest

from core import runner_blocks
from core import wave
from core.runner import MacroRunner


def test_impossible_current_wave_above_maximum_is_rejected(monkeypatch):
    readings = iter(["24 / 15"] * 9)

    monkeypatch.setattr(wave.ocr_windows, "is_available", lambda: True)
    monkeypatch.setattr(wave.ocr_windows, "ocr_image", lambda _image: "24 / 15")
    monkeypatch.setattr(
        wave,
        "candidate_masks",
        lambda _image, sharpen_amount: [np.zeros((10, 10), dtype=np.uint8)] * 3,
    )
    monkeypatch.setattr(wave, "ocr_mask", lambda *_args, **_kwargs: next(readings))

    assert wave.read_wave(np.zeros((61, 104, 3), dtype=np.uint8)) == (None, None)


def test_valid_wave_still_wins_when_impossible_votes_are_more_common(monkeypatch):
    readings = iter(["24 / 15"] * 8 + ["4 / 15"] * 3)

    monkeypatch.setattr(wave.ocr_windows, "is_available", lambda: True)
    monkeypatch.setattr(wave.ocr_windows, "ocr_image", lambda _image: next(readings))
    monkeypatch.setattr(
        wave,
        "candidate_masks",
        lambda _image, sharpen_amount: [np.zeros((10, 10), dtype=np.uint8)] * 3,
    )

    assert wave.read_wave(np.zeros((61, 104, 3), dtype=np.uint8)) == (4, 15)


def test_windows_color_pass_is_preferred_over_noisy_mask_votes(monkeypatch):
    readings = iter(["4 / 15", ""] + ["14 / 15"] * 5 + ["4 / 15"] * 4)

    monkeypatch.setattr(wave.ocr_windows, "is_available", lambda: True)
    monkeypatch.setattr(wave.ocr_windows, "ocr_image", lambda _image: next(readings))
    monkeypatch.setattr(
        wave,
        "candidate_masks",
        lambda _image, sharpen_amount: [np.zeros((10, 10), dtype=np.uint8)] * 3,
    )

    assert wave.read_wave(np.zeros((61, 104, 3), dtype=np.uint8)) == (4, 15)


def test_finite_20_and_30_wave_modes_and_explicit_infinite_mode(monkeypatch):
    monkeypatch.setattr(wave.ocr_windows, "is_available", lambda: True)
    monkeypatch.setattr(
        wave,
        "candidate_masks",
        lambda _image, sharpen_amount: [np.zeros((10, 10), dtype=np.uint8)] * 3,
    )
    monkeypatch.setattr(wave, "ocr_mask", lambda *_args, **_kwargs: "")

    for text, expected in (
        ("14 / 20 wave", (14, 20)),
        ("24 / 30 wave", (24, 30)),
        ("42 / ∞ wave", (42, "∞")),
        ("42 / infinite wave", (42, "∞")),
        ("42 wave", (42, None)),
    ):
        monkeypatch.setattr(wave.ocr_windows, "ocr_image", lambda _image, value=text: value)
        assert wave.read_wave(np.zeros((61, 104, 3), dtype=np.uint8)) == expected


def test_current_only_fallback_recovers_missing_number_and_s_as_five(monkeypatch):
    monkeypatch.setattr(wave.ocr_windows, "is_available", lambda: True)
    monkeypatch.setattr(
        wave,
        "candidate_masks",
        lambda _image, sharpen_amount: [np.zeros((10, 10), dtype=np.uint8)] * 3,
    )
    monkeypatch.setattr(wave, "ocr_mask", lambda *_args, **_kwargs: "")

    readings = iter(["wave", "24 wave"] + [""] * 9)
    monkeypatch.setattr(wave.ocr_windows, "ocr_image", lambda _image: next(readings))
    assert wave.read_wave(np.zeros((61, 104, 3), dtype=np.uint8)) == (24, None)

    monkeypatch.setattr(wave.ocr_windows, "ocr_image", lambda _image: "2S wave")
    assert wave.read_wave(np.zeros((61, 104, 3), dtype=np.uint8)) == (25, None)


def test_finite_counter_wins_over_current_only_mask_noise(monkeypatch):
    readings = iter(["10 / 15 wave"] + ["15 wave"] * 10)

    monkeypatch.setattr(wave.ocr_windows, "is_available", lambda: True)
    monkeypatch.setattr(wave.ocr_windows, "ocr_image", lambda _image: next(readings))
    monkeypatch.setattr(
        wave,
        "candidate_masks",
        lambda _image, sharpen_amount: [np.zeros((10, 10), dtype=np.uint8)] * 3,
    )

    assert wave.read_wave(np.zeros((61, 104, 3), dtype=np.uint8)) == (10, 15)


def test_wait_for_wave_requires_two_target_readings_before_later_blocks(monkeypatch):
    runner = MacroRunner(MagicMock(), MagicMock(), MagicMock())
    runner._battle_block_state = {}
    block = {"params": {"wave": 10}}
    readings = iter([(14, 15), (4, 15), (10, 15), (10, 15)])

    monkeypatch.setattr(runner_blocks.time, "time", lambda: 100.0)
    monkeypatch.setattr(
        runner_blocks.wm,
        "get_window_rect_screen",
        lambda _hwnd: (0, 0, 1152, 756),
    )
    monkeypatch.setattr(
        runner_blocks.vision,
        "capture_window_region_bgr",
        lambda _hwnd, _region: np.zeros((61, 104, 3)),
    )
    monkeypatch.setattr("core.wave.read_wave", lambda _image: next(readings))

    # A plausible one-frame jump above the target is not trusted.
    assert runner._run_wait_wave_tick(123, block, 1) is False
    runner._battle_block_state["next_check"] = 0.0
    # Returning below the target clears that pending confirmation.
    assert runner._run_wait_wave_tick(123, block, 1) is False
    runner._battle_block_state["next_check"] = 0.0
    # The real target also waits for one confirmation...
    assert runner._run_wait_wave_tick(123, block, 1) is False
    runner._battle_block_state["next_check"] = 0.0
    # ...and only the consecutive second read unlocks the next block.
    assert runner._run_wait_wave_tick(123, block, 1) is True


def test_wait_for_wave_captures_the_roblox_window_not_the_screen(monkeypatch):
    runner = MacroRunner(MagicMock(), MagicMock(), MagicMock())
    runner._battle_block_state = {}
    block = {"params": {"wave": 10}}
    captured = []

    monkeypatch.setattr(
        runner_blocks.vision,
        "capture_window_region_bgr",
        lambda hwnd, region: captured.append((hwnd, region)) or np.zeros((61, 104, 3)),
    )
    monkeypatch.setattr(
        "core.ocr.capture_region",
        lambda *_args: pytest.fail("wave OCR must not capture the physical screen"),
    )
    monkeypatch.setattr(runner_blocks.time, "time", lambda: 100.0)
    monkeypatch.setattr("core.wave.read_wave", lambda _image: (10, 15))

    runner._run_wait_wave_tick(123, block, 1)

    assert captured == [(123, runner_blocks.WAVE_REGION)]


def test_wait_for_wave_supports_current_only_unlimited_counter(monkeypatch):
    runner = MacroRunner(MagicMock(), MagicMock(), MagicMock())
    runner._battle_block_state = {}
    block = {"params": {"wave": 10}}
    readings = iter([(9, None), (10, None), (10, None)])

    monkeypatch.setattr(runner_blocks.time, "time", lambda: 100.0)
    monkeypatch.setattr(
        runner_blocks.wm,
        "get_window_rect_screen",
        lambda _hwnd: (0, 0, 1152, 756),
    )
    monkeypatch.setattr(
        runner_blocks.vision,
        "capture_window_region_bgr",
        lambda _hwnd, _region: np.zeros((61, 104, 3)),
    )
    monkeypatch.setattr("core.wave.read_wave", lambda _image: next(readings))

    assert runner._run_wait_wave_tick(123, block, 1) is False
    runner._battle_block_state["next_check"] = 0.0
    assert runner._run_wait_wave_tick(123, block, 1) is False
    runner._battle_block_state["next_check"] = 0.0
    assert runner._run_wait_wave_tick(123, block, 1) is True
    assert all(
        "/None" not in logged.args[0]
        for logged in runner._log.call_args_list
    )


def test_wait_for_wave_rejects_inconsistent_impossible_unlimited_reads(monkeypatch):
    runner = MacroRunner(MagicMock(), MagicMock(), MagicMock())
    runner._battle_block_state = {}
    block = {"params": {"wave": 45}}
    readings = iter([(1414, None), (46, None), (46, None)])

    monkeypatch.setattr(runner_blocks.time, "time", lambda: 100.0)
    monkeypatch.setattr(
        runner_blocks.wm,
        "get_window_rect_screen",
        lambda _hwnd: (0, 0, 1152, 756),
    )
    monkeypatch.setattr(
        runner_blocks.vision,
        "capture_window_region_bgr",
        lambda _hwnd, _region: np.zeros((61, 104, 3)),
    )
    monkeypatch.setattr("core.wave.read_wave", lambda _image: next(readings))

    # The impossible first read cannot combine with the real wave 46.
    assert runner._run_wait_wave_tick(123, block, 1) is False
    runner._battle_block_state["next_check"] = 0.0
    assert runner._run_wait_wave_tick(123, block, 1) is False
    runner._battle_block_state["next_check"] = 0.0
    # Only the second identical 46 reading satisfies the confirmation.
    assert runner._run_wait_wave_tick(123, block, 1) is True
