import threading
from unittest.mock import Mock

from core.runner import MacroRunner
from core.runner_challenge import ChallengeOps
from main import Api
from core import settings


def test_webhook_progress_setting_defaults_off_and_round_trips(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    api = object.__new__(Api)

    assert api.get_webhook_settings() == {
        "url": "",
        "enabled": False,
        "silent": False,
        "mention_id": "",
        "progress": False,
    }

    api.save_webhook_settings(
        "https://discord.com/api/webhooks/123/token",
        True,
        True,
        "456",
        True,
    )

    assert object.__new__(Api).get_webhook_settings() == {
        "url": "https://discord.com/api/webhooks/123/token",
        "enabled": True,
        "silent": True,
        "mention_id": "456",
        "progress": True,
    }


def test_task_progress_webhooks_are_opt_in(monkeypatch):
    runner = MacroRunner(Mock(), Mock(), Mock())
    runner._stop_event = threading.Event()
    runner._checkpoint = lambda _stop: False
    runner._set_status = lambda **_kwargs: None
    runner._run_task_setup = Mock(return_value=True)
    runner._play_one_match = Mock(return_value="win")
    runner._handle_match_result = Mock(return_value=True)
    runner._challenge_has_ready_stage = lambda: False
    runner._crafting_wants_in = lambda *_args: False
    runner._fuel_wants_in = lambda: False
    runner._auto_shop_wants_in = lambda: False
    runner._memory_refresh_due = lambda: False
    runner._send_event_webhook = Mock()
    task = {"mode": "story", "map": "King's Tomb", "stage": "1", "repeat": 1}

    runner._run_task(
        123, runner._stop_event, task, 1, 1, {}, None, None, {},
        {"enabled": True, "progress": False},
    )
    runner._send_event_webhook.assert_not_called()

    runner._run_task(
        123, runner._stop_event, task, 1, 1, {}, None, None, {},
        {"enabled": True, "progress": True},
    )

    titles = [call.args[2] for call in runner._send_event_webhook.call_args_list]
    assert titles == ["Task 1/1 Started", "Task 1/1 Finished"]
    start_fields = runner._send_event_webhook.call_args_list[0].kwargs["extra_fields"]
    finish_fields = runner._send_event_webhook.call_args_list[1].kwargs["extra_fields"]
    assert {field["name"]: field["value"] for field in start_fields}["Next"] == (
        "Enter stage, then Pre Start (repeat 1/1)"
    )
    assert {field["name"]: field["value"] for field in finish_fields}["Next"] == (
        "Auto resource checks, then Task 1/1 on the next queue pass"
    )
    assert runner._next_task_progress(1, 2) == "Auto resource checks, then Task 2/2"


def test_challenge_progress_webhooks_report_start_and_finish():
    runner = MacroRunner(Mock(), Mock(), Mock())
    runner._send_event_webhook = Mock()
    runner._set_status = lambda **_kwargs: None
    runner._checkpoint = lambda _stop: False
    runner._enter_challenge_stage = Mock(return_value=True)
    runner._run_challenge_battle = Mock(return_value="win")

    result = ChallengeOps._run_one_challenge_stage(
        runner,
        123,
        threading.Event(),
        "2",
        "solo",
        {},
        {},
        {},
        {"enabled": True, "progress": True},
    )

    assert result == "win"
    titles = [call.args[2] for call in runner._send_event_webhook.call_args_list]
    assert titles == ["Challenge #2 Started", "Challenge #2 Finished"]
    assert "Victory" in runner._send_event_webhook.call_args_list[1].args[3]
    finish_fields = runner._send_event_webhook.call_args_list[1].kwargs["extra_fields"]
    assert {field["name"]: field["value"] for field in finish_fields}["Next"] == (
        "Check for another ready Challenge stage, then Task queue"
    )
