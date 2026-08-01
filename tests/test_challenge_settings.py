import main


def _api(logs=None):
    api = main.Api.__new__(main.Api)
    api.push_log = (logs if logs is not None else []).append
    return api


def _state(macros=None, *, enabled=False, daily_enabled=False):
    macros = macros or {}
    return {
        "challenge": {
            "enabled": enabled,
            "play_mode": "solo",
            "daily": {
                "enabled": daily_enabled,
                "last_completed_period": "",
            },
            "stages": {
                slot: {"enabled": True, "count": 0, "last_played_at": 0}
                for slot in main.CHALLENGE_STAGE_SLOTS
            },
            "maps": {
                name: {"macro": macros.get(name, "")}
                for name in main.CHALLENGE_STORY_MAPS
            },
            "last_reset_date": "2026-07-29",
            "reset_schedule": main.CHALLENGE_RESET_SCHEDULE,
        }
    }


def _modern_template(_name):
    return {"blocks": {"prestart": [], "battle": []}}


def _patch_settings(monkeypatch, state):
    monkeypatch.setattr(main.cfg, "load", lambda: state)
    monkeypatch.setattr(main.cfg, "update", lambda patch: state.update(patch))
    monkeypatch.setattr(
        main, "_current_challenge_reset_period", lambda now=None: "2026-07-29")


def test_challenge_settings_report_incomplete_story_map_setup(monkeypatch):
    macros = {
        name: f"{name} Farm"
        for name in main.CHALLENGE_STORY_MAPS[:-1]
    }
    state = _state(macros)
    _patch_settings(monkeypatch, state)
    monkeypatch.setattr(main.tpl, "template_exists", lambda _name: True)
    monkeypatch.setattr(main.tpl, "load_template", _modern_template)

    result = _api().get_challenge_settings()

    assert result["setup_ready"] is False
    assert result["missing_maps"] == [main.CHALLENGE_STORY_MAPS[-1]]
    assert result["invalid_maps"] == []


def test_regular_challenge_cannot_enable_until_every_map_has_a_macro(monkeypatch):
    state = _state()
    _patch_settings(monkeypatch, state)
    logs = []
    api = _api(logs)

    result = api.set_challenge_enabled(True)

    assert result["ok"] is False
    assert result["reason"] == "incomplete_challenge_maps"
    assert state["challenge"]["enabled"] is False
    assert any("Auto Challenge was not enabled" in message for message in logs)


def test_daily_challenge_cannot_enable_until_every_map_has_a_macro(monkeypatch):
    state = _state()
    _patch_settings(monkeypatch, state)
    logs = []
    api = _api(logs)

    result = api.set_daily_challenge_enabled(True)

    assert result["ok"] is False
    assert result["reason"] == "incomplete_challenge_maps"
    assert state["challenge"]["daily"]["enabled"] is False
    assert any("Daily Challenge was not enabled" in message for message in logs)


def test_clearing_a_challenge_map_disables_both_challenge_modes(monkeypatch):
    macros = {
        name: f"{name} Farm"
        for name in main.CHALLENGE_STORY_MAPS
    }
    state = _state(macros, enabled=True, daily_enabled=True)
    _patch_settings(monkeypatch, state)
    monkeypatch.setattr(main.tpl, "template_exists", lambda _name: True)
    monkeypatch.setattr(main.tpl, "load_template", _modern_template)

    result = _api().set_challenge_map_macro("School Grounds", "")

    assert result["ok"] is True
    assert result["auto_disabled"] is True
    assert state["challenge"]["enabled"] is False
    assert state["challenge"]["daily"]["enabled"] is False
    assert result["missing_maps"] == ["School Grounds"]
