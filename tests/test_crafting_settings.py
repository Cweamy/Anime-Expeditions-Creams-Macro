import main

from core import settings
from core.runner_constants import CRAFT_SPRITES


def _api():
    # These settings methods do not depend on Api's window/input machinery.
    return object.__new__(main.Api)


def test_every_sprite_selection_count_persists_across_reload(
        tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    api = _api()

    for selected_count in range(len(CRAFT_SPRITES) + 1):
        settings.save({"crafting": api._default_crafting_settings()})
        selected = set(CRAFT_SPRITES[:selected_count])
        for key in CRAFT_SPRITES:
            result = api.set_crafting_item_enabled(key, key in selected)
            assert result == {"ok": True}

        saved = api.get_crafting_settings()
        reloaded = _api().get_crafting_settings()
        for snapshot in (saved, reloaded):
            enabled = {
                item["key"] for item in snapshot["items"]
                if item["enabled"]
            }
            assert enabled == selected
            assert len(snapshot["items"]) == len(CRAFT_SPRITES)


def test_each_sprite_can_be_enabled_and_disabled_independently(
        tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    api = _api()
    settings.save({"crafting": api._default_crafting_settings()})

    for key in CRAFT_SPRITES:
        assert api.set_crafting_item_enabled(key, True) == {"ok": True}
        current = {
            item["key"]: item["enabled"]
            for item in api.get_crafting_settings()["items"]
        }
        assert current[key] is True

        assert api.set_crafting_item_enabled(key, False) == {"ok": True}
        reloaded = {
            item["key"]: item["enabled"]
            for item in _api().get_crafting_settings()["items"]
        }
        assert reloaded[key] is False
