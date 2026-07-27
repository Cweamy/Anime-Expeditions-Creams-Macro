"""Names that sanitise to the same filename must not destroy each other.

templates.py and paths.py both derive the filename by stripping disallowed
characters, so "Farm A/B" and "Farm AB" -- or "King's Tomb" and "Kings Tomb" --
mapped onto one file and the second save silently overwrote the first.

It stayed silent afterwards too: load_template reports a missing file as an
EMPTY block list (a routine that just does nothing), and load_path treats it as
a miss and falls through to the shipped default (a walk down a DIFFERENT route
through the map).
"""
import json
import os

import pytest

from core import paths as walk_paths
from core import templates as tpl


@pytest.fixture
def tdir(tmp_path, monkeypatch):
    d = tmp_path / "Templates"
    d.mkdir()
    monkeypatch.setattr(tpl, "TEMPLATES_DIR", str(d))
    return d


@pytest.fixture
def pdir(tmp_path, monkeypatch):
    d = tmp_path / "Paths"
    d.mkdir()
    monkeypatch.setattr(walk_paths, "PATHS_DIR", str(d))
    return d


def _blocks(n):
    return {"prestart": [], "battle": [{"type": "wait_ms"}] * n}


# --- the bug ---------------------------------------------------------------

def test_templates_with_colliding_names_both_survive(tdir):
    tpl.save_template("Farm A/B", _blocks(1))
    tpl.save_template("Farm AB", _blocks(2))

    assert sorted(tpl.list_templates()) == ["Farm A/B", "Farm AB"]
    assert len(tpl.load_template("Farm A/B")["blocks"]["battle"]) == 1
    assert len(tpl.load_template("Farm AB")["blocks"]["battle"]) == 2
    assert len(list(tdir.glob("*.json"))) == 2


def test_walk_paths_with_colliding_names_both_survive(pdir):
    walk_paths.save_path("King's Tomb", [{"t": 0.0}])
    walk_paths.save_path("Kings Tomb", [{"t": 1.0}, {"t": 2.0}])

    assert {"King's Tomb", "Kings Tomb"} <= set(walk_paths.list_paths())
    assert len(walk_paths.load_path("King's Tomb")["events"]) == 1
    assert len(walk_paths.load_path("Kings Tomb")["events"]) == 2


def test_resaving_a_colliding_name_updates_it_instead_of_piling_up(tdir):
    tpl.save_template("Farm A/B", _blocks(1))
    tpl.save_template("Farm AB", _blocks(2))
    tpl.save_template("Farm A/B", _blocks(5))

    assert len(list(tdir.glob("*.json"))) == 2, "a re-save created another copy"
    assert len(tpl.load_template("Farm A/B")["blocks"]["battle"]) == 5
    assert len(tpl.load_template("Farm AB")["blocks"]["battle"]) == 2


def test_deleting_one_colliding_name_leaves_the_other(tdir):
    tpl.save_template("Farm A/B", _blocks(1))
    tpl.save_template("Farm AB", _blocks(2))

    assert tpl.delete_template("Farm A/B") is True
    assert tpl.list_templates() == ["Farm AB"]
    assert len(tpl.load_template("Farm AB")["blocks"]["battle"]) == 2


# --- nothing that already worked may change --------------------------------
# Templates are referenced BY NAME from tasks, the Challenge screen and task
# presets, so a template written by the old code has to keep resolving to the
# same file, by the same name, on the same code path.

def _write_legacy(directory, name, payload):
    """Exactly what the old save_template/save_path wrote: filename == stored
    name == the sanitised name."""
    (directory / f"{name}.json").write_text(json.dumps({"name": name, **payload}), encoding="utf-8")


def test_templates_written_by_the_old_code_are_unchanged(tdir):
    for n in ("Rose Farm", "Kings Tomb", "Farm AB"):
        _write_legacy(tdir, n, {"blocks": _blocks(1)})

    assert tpl.list_templates() == ["Farm AB", "Kings Tomb", "Rose Farm"]
    assert tpl.load_template("Rose Farm")["name"] == "Rose Farm"

    tpl.save_template("Rose Farm", _blocks(3))
    assert sorted(f.name for f in tdir.glob("*.json")) == [
        "Farm AB.json", "Kings Tomb.json", "Rose Farm.json"], "a re-save added a file"
    assert len(tpl.load_template("Rose Farm")["blocks"]["battle"]) == 3


def test_a_missing_template_still_reads_as_empty(tdir):
    assert tpl.load_template("Never Saved")["blocks"] == []


def test_your_own_recording_still_beats_the_shipped_default(pdir):
    shipped = os.path.join(walk_paths.DEFAULT_PATHS_DIR, "Kings Tomb.json")
    if not os.path.isfile(shipped):
        pytest.skip("shipped default 'Kings Tomb' not present in this checkout")

    walk_paths.save_path("Kings Tomb", [{"t": 9.9}])
    assert walk_paths.load_path("Kings Tomb")["events"] == [{"t": 9.9}]


def test_a_shipped_default_still_loads_when_you_have_no_recording(pdir):
    shipped = os.path.join(walk_paths.DEFAULT_PATHS_DIR, "Kings Tomb.json")
    if not os.path.isfile(shipped):
        pytest.skip("shipped default 'Kings Tomb' not present in this checkout")

    assert walk_paths.load_path("Kings Tomb")["events"], "the shipped default stopped resolving"


def test_a_new_name_never_shadows_a_shipped_default(pdir):
    """A recording whose slug collides with a shipped default must get its own
    file -- otherwise loading the default's name would return the wrong walk."""
    shipped = os.path.join(walk_paths.DEFAULT_PATHS_DIR, "Kings Tomb.json")
    if not os.path.isfile(shipped):
        pytest.skip("shipped default 'Kings Tomb' not present in this checkout")

    walk_paths.save_path("King's Tomb", [{"t": 1.1}])
    assert walk_paths.load_path("King's Tomb")["events"] == [{"t": 1.1}]
    assert walk_paths.load_path("Kings Tomb")["events"] != [{"t": 1.1}]
