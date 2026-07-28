import json
import threading

import pytest

from core import task_presets as tp


@pytest.fixture(autouse=True)
def presets_dir(tmp_path, monkeypatch):
    """Every test gets its own empty presets folder."""
    d = tmp_path / "Templates" / "Tasks"
    d.mkdir(parents=True)
    monkeypatch.setattr(tp, "PRESETS_DIR", str(d))
    return d


def _tasks(n):
    return [{"mode": "story", "map": f"Map {i}", "repeat": i} for i in range(n)]


# --- round trip ------------------------------------------------------------

def test_save_and_load_round_trip():
    tp.save_preset("Overnight", _tasks(3))
    got = tp.load_preset("Overnight")
    assert got["name"] == "Overnight"
    assert got["tasks"] == _tasks(3)
    assert got["found"] is True and got["error"] == ""


def test_every_task_field_survives():
    """A task carries mode/map/stage/difficulty/macro/repeat/team/equipment/
    play_mode/extract_after/infinite_wave_limit plus the Event Act 4 fields -- the preset is
    useless if any of them is dropped or coerced."""
    task = {
        "id": "t_abc", "mode": "story", "map": "King's Tomb", "stage": "Infinite",
        "difficulty": "Hard", "extract_after": "3", "repeat": 25, "team": "Team B",
        "equipment": "exclude", "play_mode": "matchmaking", "macro": "Rose Farm",
        "infinite_wave_limit": 50,
        "act4_on_drop": True, "act4_mode": "until_locked", "act4_macro": "Act4 Routine",
    }
    tp.save_preset("Full", [task])
    assert tp.load_preset("Full")["tasks"] == [task]


def test_resaving_the_same_name_overwrites(presets_dir):
    tp.save_preset("Overnight", _tasks(3))
    tp.save_preset("Overnight", _tasks(8))
    assert len(tp.load_preset("Overnight")["tasks"]) == 8
    assert [f.name for f in presets_dir.glob("*.json")] == ["Overnight.json"]


def test_list_presets_is_sorted_case_insensitively():
    for n in ("zebra", "Apple", "monkey"):
        tp.save_preset(n, _tasks(1))
    assert tp.list_presets() == ["Apple", "monkey", "zebra"]


# --- name handling ---------------------------------------------------------

def test_names_that_sanitise_alike_stay_separate(presets_dir):
    """core/templates.py strips disallowed characters to build the filename,
    so "Farm A/B" and "Farm AB" collide there and the second save destroys
    the first. Presets keep the display name apart from the slug."""
    tp.save_preset("Farm A/B", _tasks(2))
    tp.save_preset("Farm AB", _tasks(7))

    assert sorted(tp.list_presets()) == ["Farm A/B", "Farm AB"]
    assert len(tp.load_preset("Farm A/B")["tasks"]) == 2
    assert len(tp.load_preset("Farm AB")["tasks"]) == 7
    assert len(list(presets_dir.glob("*.json"))) == 2


def test_a_name_that_sanitises_to_nothing_still_saves():
    tp.save_preset("///", _tasks(1))
    assert tp.load_preset("///")["tasks"] == _tasks(1)


def test_blank_name_falls_back():
    assert tp.save_preset("   ", _tasks(1)) == "Preset"


def test_name_cannot_escape_the_presets_folder(presets_dir):
    tp.save_preset("../../evil", _tasks(1))
    written = list(presets_dir.parent.parent.rglob("*.json"))
    assert all(str(presets_dir) in str(p) for p in written), "a preset escaped its folder"


# --- files put there by hand ----------------------------------------------

def test_hand_dropped_json_is_picked_up(presets_dir):
    (presets_dir / "Dropped.json").write_text(
        json.dumps({"version": 1, "name": "Dropped", "tasks": [{"mode": "raid"}]}),
        encoding="utf-8")
    assert "Dropped" in tp.list_presets()
    assert tp.load_preset("Dropped")["tasks"] == [{"mode": "raid"}]


def test_file_without_a_name_key_falls_back_to_the_filename(presets_dir):
    (presets_dir / "Renamed.json").write_text(json.dumps({"tasks": [{"mode": "story"}]}),
                                              encoding="utf-8")
    assert "Renamed" in tp.list_presets()
    assert len(tp.load_preset("Renamed")["tasks"]) == 1


def test_corrupt_file_is_reported_as_corrupt_not_missing(presets_dir):
    """Both come back with no tasks, but only one means there's a broken file
    sitting in the folder that the user would want to know about."""
    (presets_dir / "Broken.json").write_text("{not json at all", encoding="utf-8")

    broken = tp.load_preset("Broken")
    assert broken["tasks"] == [] and broken["found"] is True and broken["error"]

    missing = tp.load_preset("Never Saved")
    assert missing["tasks"] == [] and missing["found"] is False and missing["error"] == ""


def test_tasks_key_of_the_wrong_type_is_ignored(presets_dir):
    (presets_dir / "Weird.json").write_text(json.dumps({"name": "Weird", "tasks": "nope"}),
                                            encoding="utf-8")
    assert tp.load_preset("Weird")["tasks"] == []


# --- delete ----------------------------------------------------------------

def test_delete_removes_only_the_named_preset():
    tp.save_preset("Keep", _tasks(1))
    tp.save_preset("Drop", _tasks(1))
    assert tp.delete_preset("Drop") is True
    assert tp.list_presets() == ["Keep"]


def test_delete_unknown_preset_reports_false():
    assert tp.delete_preset("Never Existed") is False


# --- durability ------------------------------------------------------------

def test_interrupted_save_keeps_the_previous_preset(presets_dir, monkeypatch):
    tp.save_preset("Overnight", _tasks(3))

    def dump_then_die(obj, fp, **kwargs):
        fp.write('{"version": 1, "na')
        raise KeyboardInterrupt("simulated kill mid-write")

    # monkeypatch.context(), not monkeypatch.undo(): undo() reverts EVERY
    # patch this fixture instance made, which includes the presets_dir
    # fixture's PRESETS_DIR redirect -- the load below would then look in the
    # real folder and find nothing.
    with monkeypatch.context() as m:
        m.setattr(json, "dump", dump_then_die)
        with pytest.raises(KeyboardInterrupt):
            tp.save_preset("Overnight", _tasks(99))

    assert len(tp.load_preset("Overnight")["tasks"]) == 3
    assert not list(presets_dir.glob("*.tmp")), "scratch file left behind"


# --- concurrency -----------------------------------------------------------

def test_two_names_sharing_a_slug_saved_at_once_both_survive(presets_dir):
    """_slug_for() reads the folder to pick a free filename and save_preset()
    then writes it. Without a lock across that gap both savers pick the SAME
    slug and one silently overwrites the other."""
    errors = []
    threading.excepthook = lambda a: errors.append(a.exc_value)
    start = threading.Event()

    def save(name, count):
        start.wait()
        for _ in range(10):
            tp.save_preset(name, _tasks(count))

    threads = [threading.Thread(target=save, args=a)
               for a in (("Farm A/B", 2), ("Farm AB", 7), ("Overnight", 5))]
    for t in threads:
        t.start()
    start.set()
    for t in threads:
        t.join()

    assert errors == []
    assert sorted(tp.list_presets()) == ["Farm A/B", "Farm AB", "Overnight"]
    assert len(tp.load_preset("Farm A/B")["tasks"]) == 2
    assert len(tp.load_preset("Farm AB")["tasks"]) == 7
    assert len(tp.load_preset("Overnight")["tasks"]) == 5
    assert not list(presets_dir.glob("*.tmp"))


def test_same_preset_saved_concurrently_is_last_write_wins_not_a_crash(presets_dir):
    errors = []
    threading.excepthook = lambda a: errors.append(a.exc_value)
    start = threading.Event()

    def save(count):
        start.wait()
        for _ in range(15):
            tp.save_preset("Overnight", _tasks(count))

    threads = [threading.Thread(target=save, args=(c,)) for c in (3, 9)]
    for t in threads:
        t.start()
    start.set()
    for t in threads:
        t.join()

    assert errors == []
    assert [f.name for f in presets_dir.glob("*.json")] == ["Overnight.json"]
    assert len(tp.load_preset("Overnight")["tasks"]) in (3, 9)
