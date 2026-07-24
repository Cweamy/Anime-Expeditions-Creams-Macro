import threading
import pytest
from core import settings


@pytest.fixture
def mock_settings_file(tmp_path, monkeypatch):
    # Isolate settings.json to a temporary directory for each test
    settings_path = str(tmp_path / "settings.json")
    monkeypatch.setattr(settings, "SETTINGS_FILE", settings_path)
    return settings_path


def test_load_non_existent_file(mock_settings_file):
    # Returns an empty dict if the settings file does not exist yet
    assert settings.load() == {}


def test_save_and_load_roundtrip(mock_settings_file):
    # Persists and reloads configuration data correctly
    data = {"theme": "dark", "action_delay_ms": 100}
    settings.save(data)
    assert settings.load() == data


def test_update_merge(mock_settings_file):
    # Merges new key-value pairs into existing settings
    settings.save({"theme": "dark", "action_delay_ms": 100})
    result = settings.update({"action_delay_ms": 200, "start_minimized": True})

    expected = {"theme": "dark", "action_delay_ms": 200, "start_minimized": True}
    assert result == expected
    assert settings.load() == expected


def test_load_corrupted_json(mock_settings_file):
    # Handles JSON parse errors gracefully by returning an empty dict
    with open(mock_settings_file, "w", encoding="utf-8") as f:
        f.write("{invalid json content")

    assert settings.load() == {}


def test_concurrent_updates(mock_settings_file):
    # Verifies thread safety during concurrent update() calls
    settings.save({"counter": 0})

    def worker(key, value):
        settings.update({key: value})

    threads = []
    for i in range(10):
        t = threading.Thread(target=worker, args=(f"key_{i}", i))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    result = settings.load()
    assert len(result) == 11  # counter + 10 new keys
    for i in range(10):
        assert result[f"key_{i}"] == i
