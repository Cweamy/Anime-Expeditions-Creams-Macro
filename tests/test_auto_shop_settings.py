from core import auto_shop
from core import settings
from core.auto_shop import AUTO_SHOP_ITEMS, STATUS_FAILED_TODAY, current_auto_shop_period
from main import Api


def _api(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    api = object.__new__(Api)
    api.push_log = lambda _message: None
    return api


def test_auto_shop_defaults_expose_gold_shop_catalog_and_daily_state(monkeypatch, tmp_path):
    api = _api(monkeypatch, tmp_path)

    current = api.get_auto_shop_settings()

    gold_shop = current["shops"]["gold_shop"]
    assert current["enabled"] is False
    assert gold_shop["enabled"] is False
    assert gold_shop["name"] == "Gold Shop"
    assert gold_shop["state"]["period"] == current_auto_shop_period()
    assert [
        (item["key"], item["name"], item["daily_maximum"])
        for item in gold_shop["items"]
    ] == [
        (item["key"], item["name"], item["stock"])
        for item in AUTO_SHOP_ITEMS
    ]
    assert all(item["enabled"] is False for item in gold_shop["items"])
    assert all(item["target"] == 1 for item in gold_shop["items"])


def test_auto_shop_setters_persist_only_known_shop_items_and_targets(monkeypatch, tmp_path):
    api = _api(monkeypatch, tmp_path)

    assert api.set_auto_shop_enabled(True) == {"ok": True}
    assert api.set_auto_shop_shop_enabled("gold_shop", True) == {"ok": True}
    assert api.set_auto_shop_item_enabled("gold_shop", "trait_crystal", True) == {"ok": True}
    assert api.set_auto_shop_item_target("gold_shop", "trait_crystal", "max") == {"ok": True}

    reloaded = object.__new__(Api).get_auto_shop_settings()
    trait = next(
        item
        for item in reloaded["shops"]["gold_shop"]["items"]
        if item["key"] == "trait_crystal"
    )
    assert reloaded["enabled"] is True
    assert reloaded["shops"]["gold_shop"]["enabled"] is True
    assert trait["enabled"] is True
    assert trait["target"] == "max"

    assert api.set_auto_shop_shop_enabled("unknown", True) == {
        "ok": False,
        "reason": "bad_shop",
    }
    assert api.set_auto_shop_item_enabled("gold_shop", "unknown", True) == {
        "ok": False,
        "reason": "bad_item",
    }
    assert api.set_auto_shop_item_target("gold_shop", "trait_crystal", 26) == {
        "ok": False,
        "reason": "bad_target",
    }


def test_auto_shop_utc_rollover_resets_runtime_state_but_keeps_configuration(
        monkeypatch, tmp_path):
    api = _api(monkeypatch, tmp_path)
    first_period = "2026-07-29"
    second_period = "2026-07-30"
    monkeypatch.setattr("main.current_auto_shop_period", lambda: first_period)

    api.set_auto_shop_enabled(True)
    api.set_auto_shop_shop_enabled("gold_shop", True)
    api.set_auto_shop_item_enabled("gold_shop", "cursed_boba", True)
    api.set_auto_shop_item_target("gold_shop", "cursed_boba", 5)
    api._save_auto_shop_item_state(
        "gold_shop",
        "cursed_boba",
        {
            "period": first_period,
            "status": STATUS_FAILED_TODAY,
            "attempts": 3,
            "last_known_left": 49,
            "stock_signature": "00",
            "verification": None,
        },
    )

    monkeypatch.setattr("main.current_auto_shop_period", lambda: second_period)
    current = api.get_auto_shop_settings()
    cursed_boba = next(
        item
        for item in current["shops"]["gold_shop"]["items"]
        if item["key"] == "cursed_boba"
    )

    assert current["enabled"] is True
    assert current["shops"]["gold_shop"]["enabled"] is True
    assert cursed_boba["enabled"] is True
    assert cursed_boba["target"] == 5
    assert cursed_boba["state"]["period"] == second_period
    assert cursed_boba["state"]["status"] == "pending"
    assert cursed_boba["state"]["attempts"] == 0


def test_changing_a_completed_numeric_target_reopens_daily_evaluation(
        monkeypatch, tmp_path):
    api = _api(monkeypatch, tmp_path)
    period = current_auto_shop_period()
    api.set_auto_shop_item_target("gold_shop", "cursed_boba", 5)
    api._save_auto_shop_item_state(
        "gold_shop",
        "cursed_boba",
        {
            **auto_shop.fresh_item_state(period),
            "status": auto_shop.STATUS_COMPLETED,
            "last_known_left": 45,
        },
    )

    api.set_auto_shop_item_target("gold_shop", "cursed_boba", 10)
    current = api.get_auto_shop_settings()
    cursed_boba = next(
        item
        for item in current["shops"]["gold_shop"]["items"]
        if item["key"] == "cursed_boba"
    )

    assert cursed_boba["target"] == 10
    assert cursed_boba["state"]["status"] == auto_shop.STATUS_PENDING
    assert cursed_boba["state"]["last_known_left"] == 45


def test_runtime_item_states_can_be_manually_reset_without_clearing_other_states(
        monkeypatch, tmp_path):
    api = _api(monkeypatch, tmp_path)
    period = current_auto_shop_period()
    api._save_auto_shop_item_state(
        "gold_shop",
        "trait_crystal",
        {
            **auto_shop.fresh_item_state(period),
            "status": auto_shop.STATUS_FAILED_TODAY,
            "attempts": 3,
        },
    )
    api._save_auto_shop_item_state(
        "gold_shop",
        "mana_flask",
        {
            **auto_shop.fresh_item_state(period),
            "status": auto_shop.STATUS_PENDING_VERIFICATION,
            "last_known_left": 150,
            "verification": {
                "before_left": 150,
                "before_signature": "00",
            },
        },
    )

    assert api.reset_auto_shop_item_today(
        "gold_shop",
        "trait_crystal",
    ) == {"ok": True}
    assert api.reset_auto_shop_item_today(
        "gold_shop",
        "mana_flask",
    ) == {"ok": True}

    current = api.get_auto_shop_settings()
    items = {item["key"]: item for item in current["shops"]["gold_shop"]["items"]}
    assert items["trait_crystal"]["state"] == auto_shop.fresh_item_state(period)
    assert items["mana_flask"]["state"] == auto_shop.fresh_item_state(period)
