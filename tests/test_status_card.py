import pytest
from core.status_card import render_status_card_bgr


def test_render_status_card_with_runs_per_hour():
    """Tests if status card rendering works correctly including the runs_per_hour parameter."""
    img = render_status_card_bgr(
        is_win=True,
        action="Match Finished",
        last_run_duration="2m 30s",
        last_run_win=True,
        time_until_challenge="15m",
        session_wins=10,
        session_losses=2,
        all_time_wins=100,
        all_time_losses=20,
        runs_per_hour="12.5",
        results=[True, True, False, True]
    )

    # Asserts that the return value is a valid numpy array with expected dimensions
    assert img is not None
    assert img.shape == (434, 1152, 3)


def test_render_status_card_default_runs_per_hour():
    """Tests if the default runs_per_hour value is accepted without errors."""
    img = render_status_card_bgr(
        is_win=False,
        action="Match Finished",
        last_run_duration="3m 00s",
        last_run_win=False,
        time_until_challenge="Disabled",
        session_wins=0,
        session_losses=1,
        all_time_wins=5,
        all_time_losses=1,
        results=[False]
    )

    assert img is not None
    assert img.shape == (434, 1152, 3)
