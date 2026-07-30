import main


class _Api(main.Api):
    def __init__(self):
        self.logs = []

    def push_log(self, message):
        self.logs.append(message)


def _settings(macros=None, enabled=False):
    macros = macros or {}
    return {
        "bounty": {
            "enabled": enabled,
            "play_mode": "solo",
            "summon_banner": "standard",
            "maps": {
                name: {"macro": macros.get(name, "")}
                for name in main.BOUNTY_STORY_MAPS
            },
        }
    }


def _modern_template(_name):
    return {"blocks": {"prestart": [], "battle": []}}


def test_bounty_cannot_enable_until_every_map_has_a_macro(monkeypatch):
    state = _settings({
        name: f"{name} Farm"
        for name in main.BOUNTY_STORY_MAPS[:-1]
    })
    monkeypatch.setattr(main.cfg, "load", lambda: state)
    monkeypatch.setattr(main.cfg, "update", lambda patch: state.update(patch))
    monkeypatch.setattr(main.tpl, "template_exists", lambda _name: True)
    monkeypatch.setattr(main.tpl, "load_template", _modern_template)
    api = _Api()

    result = api.set_bounty_enabled(True)

    assert result["ok"] is False
    assert result["reason"] == "incomplete_bounty_maps"
    assert result["missing_maps"] == [main.BOUNTY_STORY_MAPS[-1]]
    assert state["bounty"]["enabled"] is False
    assert any("was not enabled" in message for message in api.logs)


def test_bounty_cannot_enable_with_deleted_macro(monkeypatch):
    macros = {name: f"{name} Farm" for name in main.BOUNTY_STORY_MAPS}
    state = _settings(macros)
    monkeypatch.setattr(main.cfg, "load", lambda: state)
    monkeypatch.setattr(main.cfg, "update", lambda patch: state.update(patch))
    monkeypatch.setattr(
        main.tpl, "template_exists",
        lambda name: name != "Rose Kingdom Farm")
    monkeypatch.setattr(main.tpl, "load_template", _modern_template)
    api = _Api()

    result = api.set_bounty_enabled(True)

    assert result["ok"] is False
    assert result["invalid_maps"] == [{
        "map": "Rose Kingdom", "macro": "Rose Kingdom Farm"}]
    assert state["bounty"]["enabled"] is False


def test_bounty_cannot_enable_with_old_format_macro(monkeypatch):
    macros = {name: f"{name} Farm" for name in main.BOUNTY_STORY_MAPS}
    state = _settings(macros)
    monkeypatch.setattr(main.cfg, "load", lambda: state)
    monkeypatch.setattr(main.cfg, "update", lambda patch: state.update(patch))
    monkeypatch.setattr(main.tpl, "template_exists", lambda _name: True)
    monkeypatch.setattr(
        main.tpl, "load_template",
        lambda name: (
            {"blocks": []}
            if name == "King's Tomb Farm"
            else _modern_template(name)))
    api = _Api()

    result = api.set_bounty_enabled(True)

    assert result["ok"] is False
    assert result["invalid_maps"] == [{
        "map": "King's Tomb", "macro": "King's Tomb Farm"}]
    assert state["bounty"]["enabled"] is False


def test_bounty_enables_when_all_five_macros_are_usable(monkeypatch):
    macros = {name: f"{name} Farm" for name in main.BOUNTY_STORY_MAPS}
    state = _settings(macros)
    monkeypatch.setattr(main.cfg, "load", lambda: state)
    monkeypatch.setattr(main.cfg, "update", lambda patch: state.update(patch))
    monkeypatch.setattr(main.tpl, "template_exists", lambda _name: True)
    monkeypatch.setattr(main.tpl, "load_template", _modern_template)
    api = _Api()

    result = api.set_bounty_enabled(True)

    assert result == {"ok": True}
    assert state["bounty"]["enabled"] is True
    assert "setup_ready" not in state["bounty"]


def test_clearing_a_map_macro_disables_enabled_bounty(monkeypatch):
    macros = {name: f"{name} Farm" for name in main.BOUNTY_STORY_MAPS}
    state = _settings(macros, enabled=True)
    monkeypatch.setattr(main.cfg, "load", lambda: state)
    monkeypatch.setattr(main.cfg, "update", lambda patch: state.update(patch))
    monkeypatch.setattr(main.tpl, "template_exists", lambda _name: True)
    monkeypatch.setattr(main.tpl, "load_template", _modern_template)
    api = _Api()

    result = api.set_bounty_map_macro("School Grounds", "")

    assert result["ok"] is True
    assert result["auto_disabled"] is True
    assert state["bounty"]["enabled"] is False
    assert result["missing_maps"] == ["School Grounds"]


def _api():
    api = main.Api.__new__(main.Api)
    api.push_log = lambda _message: None
    return api


def _saved_bounty(period="2026-07-29"):
    return {
        "enabled": True,
        "play_mode": "solo",
        "summon_banner": "standard",
        "remaining": 10,
        "total": 10,
        "last_reset_date": period,
        "reset_schedule": main.BOUNTY_RESET_SCHEDULE,
        "maps": {},
    }


def test_bounty_mode_banner_and_count_persist_together(monkeypatch):
    store = {"bounty": _saved_bounty()}
    monkeypatch.setattr(main.cfg, "load", lambda: store.copy())
    monkeypatch.setattr(main.cfg, "update", lambda changes: store.update(changes))
    monkeypatch.setattr(
        main, "_current_challenge_reset_period",
        lambda now=None: "2026-07-29")
    api = _api()

    assert api.set_bounty_play_mode("matchmaking") == {"ok": True}
    assert api.set_bounty_summon_banner("villain") == {"ok": True}
    assert api.set_bounty_remaining(0, 10) == {"ok": True}

    saved = api.get_bounty_settings()
    assert saved["play_mode"] == "matchmaking"
    assert saved["summon_banner"] == "villain"
    assert saved["remaining"] == 0
    assert saved["total"] == 10


def test_bounty_manual_reset_restores_total(monkeypatch):
    saved = _saved_bounty()
    saved["remaining"] = 0
    store = {"bounty": saved}
    monkeypatch.setattr(main.cfg, "load", lambda: store.copy())
    monkeypatch.setattr(main.cfg, "update", lambda changes: store.update(changes))
    monkeypatch.setattr(
        main, "_current_challenge_reset_period",
        lambda now=None: "2026-07-29")

    assert _api().reset_bounty_remaining() == {"ok": True}
    assert store["bounty"]["remaining"] == 10


def test_bounty_tracker_resets_at_next_utc_game_day(monkeypatch):
    saved = _saved_bounty("2026-07-28")
    saved["remaining"] = 0
    store = {"bounty": saved}
    logs = []
    monkeypatch.setattr(main.cfg, "load", lambda: store.copy())
    monkeypatch.setattr(main.cfg, "update", lambda changes: store.update(changes))
    monkeypatch.setattr(
        main, "_current_challenge_reset_period",
        lambda now=None: "2026-07-29")
    api = _api()
    api.push_log = logs.append

    result = api.get_bounty_settings()

    assert result["remaining"] == 10
    assert result["last_reset_date"] == "2026-07-29"
    assert logs == ["[Bounty] Daily bounty tracker reset."]
