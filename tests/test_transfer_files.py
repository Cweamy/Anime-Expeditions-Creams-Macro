import os

import main


def test_transfer_directory_uses_task_folder_for_task_exports(monkeypatch, tmp_path):
    from core import task_presets, templates

    task_dir = str(tmp_path / "Templates" / "Tasks")
    template_dir = str(tmp_path / "Templates")
    monkeypatch.setattr(task_presets, "PRESETS_DIR", task_dir)
    monkeypatch.setattr(templates, "TEMPLATES_DIR", template_dir)

    assert main.Api._transfer_directory("tasks") == task_dir
    assert main.Api._transfer_directory("templates") == template_dir
    assert main.Api._transfer_directory("settings") == template_dir


def test_walk_path_bridge_round_trip(monkeypatch, tmp_path):
    from core import paths

    monkeypatch.setattr(paths, "PATHS_DIR", str(tmp_path / "Paths"))
    monkeypatch.setattr(paths, "DEFAULT_PATHS_DIR", str(tmp_path / "Defaults"))
    api = main.Api()
    events = [{"t": 0.0, "key": "w", "state": "down"},
              {"t": 1.0, "key": "w", "state": "up"}]

    saved = api.save_walk_path("Boss Route", events)

    assert saved == {"ok": True, "name": "Boss Route"}
    assert api.load_walk_path("Boss Route")["events"] == events
    assert api.list_custom_paths() == ["Boss Route"]
    assert os.path.isfile(tmp_path / "Paths" / "Boss Route.json")
