from datetime import datetime, timedelta, timezone

import main


UTC = timezone.utc


def _epoch(year, month, day, hour=0, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC).timestamp()


def _saved_challenge(period, *, schedule=main.CHALLENGE_RESET_SCHEDULE, count=4):
    return {
        "enabled": True,
        "play_mode": "solo",
        "stages": {
            slot: {"enabled": True, "count": count, "last_played_at": 0}
            for slot in main.CHALLENGE_STAGE_SLOTS
        },
        "maps": {},
        "last_reset_date": period,
        "reset_schedule": schedule,
    }


def _api():
    api = main.Api.__new__(main.Api)
    api.push_log = lambda _message: None
    return api


def test_reset_period_changes_exactly_at_utc_midnight():
    assert main._current_challenge_reset_period(_epoch(2026, 7, 27, 23, 59, 59)) == "2026-07-27"
    assert main._current_challenge_reset_period(_epoch(2026, 7, 28, 0, 0, 0)) == "2026-07-28"


def test_same_instant_has_same_period_in_eastern_and_gmt_plus_8():
    instant = datetime(2026, 7, 28, 0, 0, tzinfo=UTC)
    eastern = instant.astimezone(timezone(-timedelta(hours=4)))
    gmt_plus_8 = instant.astimezone(timezone(timedelta(hours=8)))

    assert (eastern.hour, eastern.day) == (20, 27)
    assert (gmt_plus_8.hour, gmt_plus_8.day) == (8, 28)
    assert main._current_challenge_reset_period(eastern.timestamp()) == "2026-07-28"
    assert main._current_challenge_reset_period(gmt_plus_8.timestamp()) == "2026-07-28"


def test_poll_after_utc_boundary_resets_counts_without_restarting(monkeypatch):
    saved = _saved_challenge("2026-07-27")
    writes = []
    logs = []
    api = _api()
    api.push_log = logs.append

    monkeypatch.setattr(main.cfg, "load", lambda: {"challenge": saved})
    monkeypatch.setattr(main.cfg, "update", lambda changes: writes.append(changes))
    monkeypatch.setattr(main, "_current_challenge_reset_period", lambda now=None: "2026-07-28")

    result = api.get_challenge_settings()

    assert all(stage["count"] == 0 for stage in result["stages"].values())
    assert result["last_reset_date"] == "2026-07-28"
    assert writes == [{"challenge": result}]
    assert logs == ["[Challenge] Daily play counts reset."]


def test_running_api_rolls_over_in_place_when_clock_crosses_boundary(monkeypatch):
    store = {"challenge": _saved_challenge("2026-07-27")}
    clock = {"period": "2026-07-27"}
    api = _api()
    logs = []
    api.push_log = logs.append

    monkeypatch.setattr(main.cfg, "load", lambda: store.copy())
    monkeypatch.setattr(main.cfg, "update", lambda changes: store.update(changes))
    monkeypatch.setattr(main, "_current_challenge_reset_period", lambda now=None: clock["period"])

    before = api.get_challenge_settings()
    assert all(stage["count"] == 4 for stage in before["stages"].values())

    # The same live Api instance sees the next status poll after midnight UTC.
    # No app, Roblox, or macro lifecycle operation is involved.
    clock["period"] = "2026-07-28"
    after = api.get_challenge_settings()

    assert all(stage["count"] == 0 for stage in after["stages"].values())
    assert store["challenge"]["last_reset_date"] == "2026-07-28"

    api.set_challenge_stage_count("1", 1)
    later = api.get_challenge_settings()
    assert later["stages"]["1"]["count"] == 1
    assert logs == ["[Challenge] Daily play counts reset."]


def test_poll_before_utc_boundary_keeps_counts_and_does_not_write(monkeypatch):
    saved = _saved_challenge("2026-07-27")
    writes = []

    monkeypatch.setattr(main.cfg, "load", lambda: {"challenge": saved})
    monkeypatch.setattr(main.cfg, "update", lambda changes: writes.append(changes))
    monkeypatch.setattr(main, "_current_challenge_reset_period", lambda now=None: "2026-07-27")

    result = _api().get_challenge_settings()

    assert all(stage["count"] == 4 for stage in result["stages"].values())
    assert writes == []


def test_local_date_install_migrates_without_an_early_reset(monkeypatch):
    saved = _saved_challenge("2026-07-28", schedule=None)
    saved.pop("reset_schedule")
    writes = []
    logs = []
    api = _api()
    api.push_log = logs.append

    monkeypatch.setattr(main.cfg, "load", lambda: {"challenge": saved})
    monkeypatch.setattr(main.cfg, "update", lambda changes: writes.append(changes))
    monkeypatch.setattr(main, "_current_challenge_reset_period", lambda now=None: "2026-07-27")

    result = api.get_challenge_settings()

    assert all(stage["count"] == 4 for stage in result["stages"].values())
    assert result["last_reset_date"] == "2026-07-27"
    assert result["reset_schedule"] == main.CHALLENGE_RESET_SCHEDULE
    assert writes == [{"challenge": result}]
    assert logs == []
