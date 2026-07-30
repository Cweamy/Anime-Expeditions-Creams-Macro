import pytest
from main import Api


def test_initial_status_placeholders():
    """Verify that initial status contains default placeholder values."""
    api = Api()
    status = api.get_status()
    assert status["action"] == "Idle"
    assert status["current_task"] == "-"
    assert status["current_repeat"] == "-"
    assert status["map"] == "-"
    assert status["mode"] == "-"
    assert status["stage"] == "-"
    assert status["difficulty"] == "-"
    assert status["play_mode"] == "-"
    assert status["macro"] == "-"


def test_set_run_status_live_update():
    """Verify that updating run status reflects live task data."""
    api = Api()
    api._set_run_status(
        action="Starting...",
        current_task="1 / 3",
        current_repeat="1 / 5",
        map="Subway",
        mode="story",
        stage="1",
        difficulty="hard",
        play_mode="solo",
        macro="DefaultMacro",
    )
    status = api.get_status()
    assert status["action"] == "Starting..."
    assert status["current_task"] == "1 / 3"
    assert status["current_repeat"] == "1 / 5"
    assert status["map"] == "Subway"
    assert status["mode"] == "story"
    assert status["stage"] == "1"
    assert status["difficulty"] == "hard"
    assert status["play_mode"] == "solo"
    assert status["macro"] == "DefaultMacro"


def test_set_run_status_resets_on_idle():
    """Verify that setting action to Idle resets task-specific status fields to '-'."""
    api = Api()
    api._set_run_status(
        action="In battle...",
        current_task="2 / 3",
        current_repeat="3 / 5",
        map="Desert",
        mode="story",
        stage="2",
        difficulty="normal",
        play_mode="solo",
        macro="MyMacro",
    )
    # Setting action back to Idle should reset task fields
    api._set_run_status(action="Idle")
    status = api.get_status()
    assert status["action"] == "Idle"
    assert status["current_task"] == "-"
    assert status["current_repeat"] == "-"
    assert status["map"] == "-"
    assert status["mode"] == "-"
    assert status["stage"] == "-"
    assert status["difficulty"] == "-"
    assert status["play_mode"] == "-"
    assert status["macro"] == "-"


def test_reset_run_status_helper():
    """Verify reset_run_status helper method resets fields and updates action."""
    api = Api()
    api._set_run_status(
        action="Battle...",
        current_task="3 / 3",
        current_repeat="5 / 5",
        map="City",
    )
    api.reset_run_status("Stopped due to failure")
    status = api.get_status()
    assert status["action"] == "Stopped due to failure"
    assert status["current_task"] == "-"
    assert status["map"] == "-"


def test_stop_macro_resets_status():
    """Verify calling stop_macro resets status readout fields to Idle defaults."""
    api = Api()
    api._set_run_status(
        action="Running",
        current_task="1 / 2",
        map="Event",
    )
    api.stop_macro()
    status = api.get_status()
    assert status["action"] == "Idle"
    assert status["current_task"] == "-"
    assert status["map"] == "-"
