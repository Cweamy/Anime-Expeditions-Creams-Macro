import main


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
