"""Unit tests for Webhook enhancements (Auto Bounty, Fuel Left, Auto Refuel, Auto Crafting & Challenge 10/10)."""
import time
from unittest.mock import MagicMock, patch

import pytest

from main import _time_until_fuel_refill


def test_time_until_fuel_refill_disabled():
    assert _time_until_fuel_refill(None) == "Disabled"
    assert _time_until_fuel_refill({"enabled": False}) == "Disabled"


def test_time_until_fuel_refill_due():
    fuel = {
        "enabled": True,
        "resources": {
            "resource_drill": {"enabled": True, "due": True},
        },
    }
    assert _time_until_fuel_refill(fuel) == "Due Now"


def test_time_until_fuel_refill_countdown():
    now = 100000.0
    fuel = {
        "enabled": True,
        "resources": {
            "resource_drill": {"enabled": True, "due": False, "next_attempt_at": now + 3600 + 300},
        },
    }
    with patch("time.time", return_value=now):
        result = _time_until_fuel_refill(fuel)
    assert result == "01h 05m"


def test_send_result_webhook_bounty_and_fuel_left():
    from core.runner import MacroRunner

    runner = MacroRunner(
        mouse=MagicMock(),
        keyboard=MagicMock(),
        log=MagicMock(),
        set_status=MagicMock(),
        record_result=MagicMock(),
        get_challenge_settings=MagicMock(return_value={}),
        mark_challenge_stage_played=MagicMock(),
        get_run_stats=MagicMock(return_value={
            "session_wins": 5,
            "session_losses": 1,
            "all_time_wins": 20,
            "all_time_losses": 2,
            "session_start": time.time() - 300,
            "version": "1.0.0",
            "time_until_challenge": "Ready",
            "fuel_left": "02h 15m",
            "runs_per_hour": "12.0",
        }),
    )

    captured_embeds = []

    def fake_send_rich(url, embeds=None, file_attachments=None, components=None, content="", silent=False):
        captured_embeds.extend(embeds or [])
        return {"ok": True, "reason": ""}

    webhook_cfg = {"url": "https://discord.com/api/webhooks/123/abc", "enabled": True}
    task = {"mode": "story", "is_bounty": True, "map": "School Grounds", "stage": "1", "difficulty": "Hard"}

    with patch("core.webhook.send_rich", side_effect=fake_send_rich):
        runner._send_result_webhook(webhook_cfg, "win", task, "03m 45s", screenshot_path=None)

    assert len(captured_embeds) > 0
    main_embed = captured_embeds[0]
    assert "Bounty Victory!" in main_embed["title"]
    assert "Bounty Victory on" in main_embed["description"]

    # Verify Session field contains Fuel Left
    session_field = next(f for f in main_embed["fields"] if f["name"] == "\U0001F4CA Session")
    assert "Fuel Left" in session_field["value"]
    assert "02h 15m" in session_field["value"]

    # Verify Match field contains Type: Bounty
    match_field = next(f for f in main_embed["fields"] if f["name"] == "⚔️ Match")
    assert "Bounty" in match_field["value"]


def test_refuel_event_webhook():
    from core.runner import MacroRunner

    runner = MacroRunner(
        mouse=MagicMock(),
        keyboard=MagicMock(),
        log=MagicMock(),
        set_status=MagicMock(),
        record_result=MagicMock(),
        get_challenge_settings=MagicMock(),
        mark_challenge_stage_played=MagicMock(),
        get_run_stats=MagicMock(),
        get_crafting_settings=MagicMock(),
        set_crafting_count=MagicMock(),
        get_bounty_settings=MagicMock(),
        set_bounty_remaining=MagicMock(),
        get_fuel_settings=MagicMock(return_value={
            "enabled": True,
            "resources": {
                "resource_drill": {"enabled": True, "due": True, "amount": "max"},
            },
            "paths": {"hub_to_resource_drill": "Drill Route"},
        }),
        mark_fuel_refill_result=MagicMock(),
    )

    runner._send_event_webhook = MagicMock()
    runner._ensure_lobby = MagicMock(return_value=True)
    runner._fuel_enter_hub = MagicMock(return_value=True)
    runner._fuel_run_path = MagicMock(return_value=True)
    runner._fuel_refill_station = MagicMock(return_value=True)
    runner._recover_to_lobby = MagicMock()

    import threading
    stop_event = threading.Event()
    webhook = {"url": "https://discord.com/api/webhooks/123/abc", "enabled": True}

    with patch("core.window.show_window"), patch("core.window.activate_window"):
        runner._run_fuel_refill(None, stop_event, force=True, webhook=webhook)

    assert runner._send_event_webhook.called
    args = runner._send_event_webhook.call_args[0]
    assert args[2] == "\U000026FD Auto Refuel Completed"
    assert "Resource Drill" in args[3]


def test_crafting_event_webhook():
    from core.runner import MacroRunner

    runner = MacroRunner(
        mouse=MagicMock(),
        keyboard=MagicMock(),
        log=MagicMock(),
        set_status=MagicMock(),
        record_result=MagicMock(),
        get_challenge_settings=MagicMock(),
        mark_challenge_stage_played=MagicMock(),
        get_run_stats=MagicMock(),
        get_crafting_settings=MagicMock(return_value={
            "enabled": True,
            "items": [{"key": "sprite_fire", "enabled": True, "amount": "max"}],
        }),
        set_crafting_count=MagicMock(),
    )

    runner._send_event_webhook = MagicMock()
    runner._ensure_lobby = MagicMock(return_value=True)
    runner._click_found_image = MagicMock(return_value={"x": 100, "y": 100})
    runner._crafting_wait_for = MagicMock(return_value=True)
    runner._craft_one_item = MagicMock()
    runner._recover_to_lobby = MagicMock()

    import threading
    stop_event = threading.Event()
    webhook = {"url": "https://discord.com/api/webhooks/123/abc", "enabled": True}

    runner._run_crafting(None, stop_event, force=True, webhook=webhook)

    assert runner._send_event_webhook.called
    args = runner._send_event_webhook.call_args[0]
    assert args[2] == "\U0001F6E0 Auto Crafting Completed"


def test_challenge_cap_event_webhook():
    from core.runner import MacroRunner

    challenge_state = {
        "enabled": True,
        "cap": 10,
        "stages": {"1": {"enabled": True, "count": 9, "ready": True}},
    }

    def fake_get_challenge():
        return challenge_state

    def fake_mark_played(slot, count_play=True):
        if count_play:
            challenge_state["stages"][slot]["count"] += 1

    runner = MacroRunner(
        mouse=MagicMock(),
        keyboard=MagicMock(),
        log=MagicMock(),
        set_status=MagicMock(),
        record_result=MagicMock(),
        get_challenge_settings=fake_get_challenge,
        mark_challenge_stage_played=fake_mark_played,
    )

    runner._send_event_webhook = MagicMock()
    runner._run_one_daily_challenge = MagicMock()
    runner._run_one_challenge_stage = MagicMock(return_value="win")
    runner._checkpoint = MagicMock(return_value=False)

    import threading
    stop_event = threading.Event()
    webhook = {"url": "https://discord.com/api/webhooks/123/abc", "enabled": True}

    runner._run_challenges(None, stop_event, {}, {}, webhook=webhook)

    assert runner._send_event_webhook.called
    args = runner._send_event_webhook.call_args[0]
    assert args[2] == "\U0001F3AF Challenge #1 Completed"
    assert "10/10" in args[3]
