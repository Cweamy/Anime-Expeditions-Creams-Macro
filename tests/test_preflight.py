import pytest
from unittest.mock import patch, MagicMock

import main
from core import constants
import os

class MockRunner:
    def start(self, *args, **kwargs):
        return {"ok": True, "reason": "success"}

class MockApi(main.Api):
    def __init__(self):
        # Prevent webview from initializing in test
        self.runner = MockRunner()
        self.game_hwnd = 12345
        self.logs = []

    def push_log(self, text: str):
        self.logs.append(text)

@patch('core.window.is_window')
@patch('core.window.is_process_elevated')
@patch('core.window.is_self_elevated')
@patch('core.window.get_display_scale_percent')
@patch('os.path.isdir')
@patch('os.listdir')
@patch('core.vision.template_variant_paths')
def test_run_preflight_check_pass(mock_tvp, mock_listdir, mock_isdir, mock_get_scale, mock_is_self_el, mock_is_proc_el, mock_is_window):
    mock_is_window.return_value = True
    mock_is_proc_el.return_value = False
    mock_is_self_el.return_value = False
    mock_get_scale.return_value = 100
    mock_isdir.return_value = True
    mock_listdir.return_value = ['file.png']
    mock_tvp.return_value = ['path/to/img.png']

    api = MockApi()
    res = api.run_preflight_check()

    assert res["ok"] is True
    assert res["has_blocker"] is False
    assert len(res["warnings"]) == 0
    assert len(res["checks"]) == 5

@patch('core.window.is_window')
@patch('core.window.is_process_elevated')
@patch('core.window.is_self_elevated')
@patch('core.window.get_display_scale_percent')
@patch('os.path.isdir')
@patch('os.listdir')
@patch('core.vision.template_variant_paths')
def test_run_preflight_check_missing_roblox(mock_tvp, mock_listdir, mock_isdir, mock_get_scale, mock_is_self_el, mock_is_proc_el, mock_is_window):
    mock_is_window.return_value = False
    mock_is_proc_el.return_value = False
    mock_is_self_el.return_value = False
    mock_get_scale.return_value = 100
    mock_isdir.return_value = True
    mock_listdir.return_value = ['file.png']
    mock_tvp.return_value = ['path/to/img.png']

    api = MockApi()
    res = api.run_preflight_check()

    assert res["ok"] is False
    assert res["has_blocker"] is True
    has_roblox_error = any(c for c in res["checks"] if c["code"] == "ROBLOX_NOT_FOUND" and not c["ok"])
    assert has_roblox_error

@patch('core.window.is_window')
@patch('core.window.is_process_elevated')
@patch('core.window.is_self_elevated')
@patch('core.window.get_display_scale_percent')
@patch('os.path.isdir')
@patch('os.listdir')
@patch('core.vision.template_variant_paths')
def test_run_preflight_check_elevation_mismatch(mock_tvp, mock_listdir, mock_isdir, mock_get_scale, mock_is_self_el, mock_is_proc_el, mock_is_window):
    mock_is_window.return_value = True
    mock_is_proc_el.return_value = True
    mock_is_self_el.return_value = False
    mock_get_scale.return_value = 100
    mock_isdir.return_value = True
    mock_listdir.return_value = ['file.png']
    mock_tvp.return_value = ['path/to/img.png']

    api = MockApi()
    res = api.run_preflight_check()

    assert res["ok"] is False
    assert res["has_blocker"] is True
    has_elev_error = any(c for c in res["checks"] if c["code"] == "ELEVATION_MISMATCH" and not c["ok"])
    assert has_elev_error

@patch('main.Api.run_preflight_check')
def test_start_macro_blocker(mock_preflight):
    mock_preflight.return_value = {
        "ok": False,
        "has_blocker": True,
        "warnings": [],
        "checks": []
    }
    api = MockApi()
    res = api.start_macro()
    assert res["ok"] is False
    assert res["reason"] == "preflight_blocker"
    assert "preflight" in res
    assert "[Preflight] Start blocked due to environment/configuration issue." in api.logs

