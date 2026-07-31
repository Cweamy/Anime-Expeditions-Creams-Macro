from core import settings
from core import paths
from core.runner_constants import (
    FUEL_INTERVAL_SECONDS,
    FUEL_RETRY_SECONDS,
    fuel_refill_interval_seconds,
)
from main import Api


def _api(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    api = object.__new__(Api)
    api.push_log = lambda _message: None
    return api


def test_first_enable_is_due_immediately_and_persists(monkeypatch, tmp_path):
    api = _api(monkeypatch, tmp_path)
    monkeypatch.setattr("main.time.time", lambda: 1_000.0)

    api.set_fuel_resource_enabled("resource_drill", True)
    api.set_fuel_enabled(True)

    current = api.get_fuel_settings()
    assert current["resources"]["resource_drill"]["due"] is True
    assert current["resources"]["resource_drill"]["remaining_seconds"] == 0

    reloaded = object.__new__(Api)
    assert reloaded.get_fuel_settings()["resources"]["resource_drill"]["due"] is True


def test_default_fuel_paths_are_shipped_and_nonempty(monkeypatch, tmp_path):
    api = _api(monkeypatch, tmp_path)
    empty_user_paths = tmp_path / "Paths"
    empty_user_paths.mkdir()
    monkeypatch.setattr(paths, "PATHS_DIR", str(empty_user_paths))

    configured = api.get_fuel_settings()["paths"]
    assert configured == {
        "hub_to_resource_drill": "Auto Fuel - Hub to Resource Drill",
        "hub_to_gold_mine": "Auto Fuel - Hub to Gold Mine",
        "resource_drill_to_gold_mine": "Auto Fuel - Resource Drill to Gold Mine",
    }
    for path_name in configured.values():
        assert paths.load_path(path_name)["events"]


def test_max_success_starts_eight_hour_timer_and_reset_makes_due(monkeypatch, tmp_path):
    api = _api(monkeypatch, tmp_path)
    now = [10_000.0]
    monkeypatch.setattr("main.time.time", lambda: now[0])
    api.set_fuel_resource_enabled("gold_mine", True)
    api.set_fuel_enabled(True)

    api.mark_fuel_refill_result("gold_mine", True)
    state = api.get_fuel_settings()["resources"]["gold_mine"]
    assert state["due"] is False
    assert state["next_due_at"] == now[0] + FUEL_INTERVAL_SECONDS

    now[0] += FUEL_INTERVAL_SECONDS
    assert api.get_fuel_settings()["resources"]["gold_mine"]["due"] is True

    now[0] += 5
    api.mark_fuel_refill_result("gold_mine", True)
    api.reset_fuel_timer()
    assert api.get_fuel_settings()["resources"]["gold_mine"]["due"] is True


def test_numeric_amount_uses_its_coverage_with_a_safety_margin(monkeypatch, tmp_path):
    api = _api(monkeypatch, tmp_path)
    now = [20_000.0]
    monkeypatch.setattr("main.time.time", lambda: now[0])
    api.set_fuel_resource_enabled("resource_drill", True)
    api.set_fuel_resource_amount("resource_drill", 20)
    api.set_fuel_enabled(True)

    api.mark_fuel_refill_result("resource_drill", True)
    state = api.get_fuel_settings()["resources"]["resource_drill"]
    expected_interval = fuel_refill_interval_seconds(20)

    assert expected_interval == 95 * 60
    assert state["interval_seconds"] == expected_interval
    assert state["next_due_at"] == now[0] + expected_interval

    now[0] += expected_interval
    assert api.get_fuel_settings()["resources"]["resource_drill"]["due"] is True


def test_fuel_interval_examples_and_low_amount_floor():
    assert fuel_refill_interval_seconds("max") == 8 * 60 * 60
    assert fuel_refill_interval_seconds(50) == 4 * 60 * 60
    assert fuel_refill_interval_seconds(20) == 95 * 60
    assert fuel_refill_interval_seconds(10) == 45 * 60
    assert fuel_refill_interval_seconds(1) == 5 * 60


def test_failure_retries_after_five_minutes_without_resetting_success(monkeypatch, tmp_path):
    api = _api(monkeypatch, tmp_path)
    now = [50_000.0]
    monkeypatch.setattr("main.time.time", lambda: now[0])
    assert FUEL_RETRY_SECONDS == 5 * 60
    for resource in ("resource_drill", "gold_mine"):
        api.set_fuel_resource_enabled(resource, True)
    api.set_fuel_enabled(True)

    api.mark_fuel_refill_result("resource_drill", False)
    api.mark_fuel_refill_result("gold_mine", True)
    resources = api.get_fuel_settings()["resources"]
    assert resources["resource_drill"]["next_attempt_at"] == now[0] + FUEL_RETRY_SECONDS
    assert resources["resource_drill"]["due"] is False
    assert resources["gold_mine"]["next_due_at"] == now[0] + FUEL_INTERVAL_SECONDS

    now[0] += FUEL_RETRY_SECONDS
    resources = api.get_fuel_settings()["resources"]
    assert resources["resource_drill"]["due"] is True
    assert resources["gold_mine"]["due"] is False


def test_amount_and_path_validation(monkeypatch, tmp_path):
    api = _api(monkeypatch, tmp_path)

    assert api.set_fuel_resource_amount("resource_drill", 0)["ok"] is True
    assert api.get_fuel_settings()["resources"]["resource_drill"]["amount"] == 1
    assert api.set_fuel_resource_amount("resource_drill", 99_999)["ok"] is True
    assert api.get_fuel_settings()["resources"]["resource_drill"]["amount"] == 100
    assert api.set_fuel_resource_amount("resource_drill", "bad")["ok"] is False
    assert api.set_fuel_path("unknown", "Route")["ok"] is False

    assert api.set_fuel_path("hub_to_resource_drill", "Drill Route")["ok"] is True
    assert api.get_fuel_settings()["paths"]["hub_to_resource_drill"] == "Drill Route"
