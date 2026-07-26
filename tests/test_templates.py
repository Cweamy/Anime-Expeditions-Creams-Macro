import os
import pytest
from core import templates as tpl
import main


@pytest.fixture
def mock_templates_dir(tmp_path, monkeypatch):
    # Isolate templates directory in a temporary folder for test execution
    temp_dir = str(tmp_path / "Templates")
    monkeypatch.setattr(tpl, "TEMPLATES_DIR", temp_dir)
    return temp_dir


def test_safe_name_basename_sanitization():
    # Verify that base name is extracted and special path characters are stripped
    assert tpl._safe_name("../../malicious_name") == "malicious_name"
    assert tpl._safe_name("folder/subfolder/my_template") == "my_template"
    assert tpl._safe_name("..\\..\\windows_system") == "windows_system"
    assert tpl._safe_name("  valid_template_123  ") == "valid_template_123"
    assert tpl._safe_name("../../../") == "template"


def test_resolve_template_path_valid(mock_templates_dir):
    # Verify path resolution strictly inside TEMPLATES_DIR
    path = tpl._resolve_template_path("test_template")
    expected = os.path.abspath(os.path.join(mock_templates_dir, "test_template.json"))
    assert path == expected


def test_resolve_template_path_traversal_raises(mock_templates_dir):
    # Verify that navigation attempts in path raise ValueError
    with pytest.raises(ValueError, match="navigation characters"):
        tpl._resolve_template_path("../evil")

    with pytest.raises(ValueError, match="navigation characters"):
        tpl._resolve_template_path("..\\evil")

    with pytest.raises(ValueError, match="navigation characters"):
        tpl._resolve_template_path("subfolder/template")


def test_save_load_delete_template_flow(mock_templates_dir):
    # Test normal lifecycle of saving, loading, and deleting a template
    blocks = [{"type": "click", "x": 100, "y": 200}]
    saved = tpl.save_template("my_macro", blocks)
    assert saved == "my_macro"
    assert os.path.isfile(os.path.join(mock_templates_dir, "my_macro.json"))

    loaded = tpl.load_template("my_macro")
    assert loaded["name"] == "my_macro"
    assert loaded["blocks"] == blocks

    assert tpl.delete_template("my_macro") is True
    assert not os.path.isfile(os.path.join(mock_templates_dir, "my_macro.json"))


def test_main_api_template_path_traversal_handling(mock_templates_dir, monkeypatch):
    # Verify Api class in main handles path traversal gracefully
    api = main.Api()

    # Attempting to save malicious template
    res_save = api.save_template("../secret_override", [])
    assert res_save["ok"] is False
    assert "reason" in res_save

    # Attempting to load malicious template
    res_load = api.load_template("../secret_override")
    assert res_load["blocks"] == []

    # Attempting to delete malicious template
    res_del = api.delete_template("../secret_override")
    assert res_del["ok"] is False

