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
