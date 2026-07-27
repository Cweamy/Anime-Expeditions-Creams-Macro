import pytest

from main import Api


def test_empty_history_returns_dash():
    # Empty history list should return "-"
    assert Api._calculate_runs_per_hour([], current_time=10000.0) == "-"

    # History with entries missing the "at" timestamp key should return "-"
    invalid_history = [{"result": "win"}, {"map": "Story"}]
    assert Api._calculate_runs_per_hour(invalid_history, current_time=10000.0) == "-"


def test_single_run_within_1h():
    now = 10000.0

    # Single run 30s ago (clamped to minimum 60s time span)
    # rate = (1 * 3600) / 60 = 60.0 -> "60"
    history_recent = [{"at": now - 30.0}]
    assert Api._calculate_runs_per_hour(history_recent, current_time=now) == "60"

    # Single run 30 minutes ago (1800s time span)
    # rate = (1 * 3600) / 1800 = 2.0 -> "2"
    history_half_hour = [{"at": now - 1800.0}]
    assert Api._calculate_runs_per_hour(history_half_hour, current_time=now) == "2"


def test_multiple_runs_within_1h():
    now = 10000.0

    # 3 runs within 1200 seconds time span
    # oldest_at = now - 1200.0, rate = (3 * 3600) / 1200 = 9.0 -> "9"
    history = [
        {"at": now - 100.0},
        {"at": now - 600.0},
        {"at": now - 1200.0},
    ]
    assert Api._calculate_runs_per_hour(history, current_time=now) == "9"


def test_filtering_out_runs_older_than_3600_seconds():
    now = 10000.0

    # 2 runs older than 3600s, 1 run within 3600s
    history = [
        {"at": now - 1800.0},  # Recent (within 1h)
        {"at": now - 3601.0},  # Older than 1h
        {"at": now - 5000.0},  # Older than 1h
    ]
    # Filtered recent only has the run at now - 1800.0
    # rate = (1 * 3600) / 1800 = 2.0 -> "2"
    assert Api._calculate_runs_per_hour(history, current_time=now) == "2"

    # All runs older than 3600s should result in "-"
    history_all_old = [
        {"at": now - 3601.0},
        {"at": now - 7200.0},
    ]
    assert Api._calculate_runs_per_hour(history_all_old, current_time=now) == "-"


def test_non_integer_rate_formatting():
    now = 10000.0

    # 1 run 1000 seconds ago -> time_span = 1000.0
    # rate = (1 * 3600) / 1000 = 3.6 -> "3.6"
    history = [{"at": now - 1000.0}]
    assert Api._calculate_runs_per_hour(history, current_time=now) == "3.6"


def test_edge_cases_and_invalid_data():
    now = 10000.0

    # None or non-list history
    assert Api._calculate_runs_per_hour(None, current_time=now) == "-"
    assert Api._calculate_runs_per_hour("invalid_history", current_time=now) == "-"

    # Non-dict items and invalid timestamp types
    invalid_elements = [None, "not_a_dict", {"at": "invalid_string"}, {"at": None}]
    assert Api._calculate_runs_per_hour(invalid_elements, current_time=now) == "-"

    # Future timestamps (now - h["at"] < 0)
    future_history = [{"at": now + 500.0}]
    assert Api._calculate_runs_per_hour(future_history, current_time=now) == "-"

