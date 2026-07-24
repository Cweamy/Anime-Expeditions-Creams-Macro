import pytest
from core import pacing


@pytest.fixture(autouse=True)
def reset_pacing_delay():
    # Ensures action delay is reset to 0 before and after each test
    pacing.set_action_delay_ms(0)
    yield
    pacing.set_action_delay_ms(0)


def test_default_delay():
    # Default initial delay must be 0ms
    assert pacing.get_action_delay_ms() == 0


def test_set_valid_delay():
    # Sets action delay in milliseconds correctly
    pacing.set_action_delay_ms(250)
    assert pacing.get_action_delay_ms() == 250


def test_delay_clamping_max():
    # Values exceeding the 2000ms cap are clamped to 2000ms
    pacing.set_action_delay_ms(5000)
    assert pacing.get_action_delay_ms() == 2000


def test_delay_clamping_min():
    # Negative values are clamped to 0ms
    pacing.set_action_delay_ms(-100)
    assert pacing.get_action_delay_ms() == 0


def test_delay_invalid_type():
    # Invalid inputs fall back safely to 0ms
    pacing.set_action_delay_ms("invalid")
    assert pacing.get_action_delay_ms() == 0

    pacing.set_action_delay_ms(None)
    assert pacing.get_action_delay_ms() == 0


def test_action_pause_calls_sleep(monkeypatch):
    # Calls time.sleep only when action delay is greater than 0
    sleep_calls = []

    def mock_sleep(duration):
        sleep_calls.append(duration)

    monkeypatch.setattr(pacing.time, "sleep", mock_sleep)

    # When delay = 0, time.sleep should not be called
    pacing.set_action_delay_ms(0)
    pacing.action_pause()
    assert len(sleep_calls) == 0

    # When delay > 0, time.sleep should be called with duration in seconds
    pacing.set_action_delay_ms(500)
    pacing.action_pause()
    assert len(sleep_calls) == 1
    assert pytest.approx(sleep_calls[0]) == 0.5
