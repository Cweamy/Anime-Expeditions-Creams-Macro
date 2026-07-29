"""End-to-end: a shared macro carries its custom Walk Path recordings so the
importer's Walk Path blocks resolve on a machine that never recorded them."""
import main
from core import paths, templates as tpl


class MockApi(main.Api):
    def __init__(self):
        self.logs = []

    def push_log(self, text: str):
        self.logs.append(text)


def _isolate(monkeypatch, tmp_path):
    tdir = tmp_path / "Templates"
    pdir = tmp_path / "Paths"
    ddir = tmp_path / "defaults"
    for d in (tdir, pdir, ddir):
        d.mkdir()
    monkeypatch.setattr(tpl, "TEMPLATES_DIR", str(tdir))
    monkeypatch.setattr(paths, "PATHS_DIR", str(pdir))
    monkeypatch.setattr(paths, "DEFAULT_PATHS_DIR", str(ddir))
    return pdir


EVENTS = [{"t": 0.0, "key": "w", "state": "down"}, {"t": 0.6, "key": "w", "state": "up"}]


def _custom_walk_template():
    return {
        "prestart": [{"type": "walk_path", "params": {}, "once": True,
                       "mode": "custom", "pathName": "Secret Route", "sprint": False}],
        "battle": [{"type": "place_unit", "params": {"name": "Goku", "x": 1, "y": 2}}],
    }


def test_export_bundles_and_import_recreates_walk_path(monkeypatch, tmp_path):
    pdir = _isolate(monkeypatch, tmp_path)
    api = MockApi()
    paths.save_path("Secret Route", EVENTS)
    tpl.save_template("Router", _custom_walk_template())

    code = api.export_template_code("Router")["code"]

    # Simulate a fresh machine: no recordings, no templates.
    for f in pdir.glob("*.json"):
        f.unlink()
    for f in (tmp_path / "Templates").glob("*.json"):
        f.unlink()
    assert paths.list_custom_paths() == []

    res = api.import_template_code(code)
    assert res["ok"] is True
    assert res["walk_paths"] == 1
    # The recording is back, with its moves intact...
    assert paths.load_path("Secret Route")["events"] == EVENTS
    # ...and the imported block still points at it.
    imported = tpl.load_template(res["templates"][0])["blocks"]
    walk = imported["prestart"][0]
    assert walk["mode"] == "custom" and walk["pathName"] == "Secret Route"


def test_import_remaps_block_when_name_taken_by_a_different_recording(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    api = MockApi()
    paths.save_path("Secret Route", EVENTS)
    tpl.save_template("Router", _custom_walk_template())
    code = api.export_template_code("Router")["code"]

    # Importer already has a DIFFERENT recording under that same name -- it must
    # survive the import untouched.
    other = [{"t": 0.0, "key": "s", "state": "down"}, {"t": 0.3, "key": "s", "state": "up"}]
    paths.save_path("Secret Route", other)
    assert paths.load_path("Secret Route")["events"] == other

    res = api.import_template_code(code)
    assert res["ok"] is True
    # Existing recording untouched; the bundled walk landed under a new name...
    assert paths.load_path("Secret Route")["events"] == other
    imported = tpl.load_template(res["templates"][0])["blocks"]
    new_name = imported["prestart"][0]["pathName"]
    assert new_name != "Secret Route"
    # ...and the block was remapped to it.
    assert paths.load_path(new_name)["events"] == EVENTS
