import json
import os
import threading

import pytest

from core import paths as walk_paths
from core import templates as tpl
from core.jsonstore import write_json_atomic


def _kill_mid_write(monkeypatch):
    """Make json.dump emit a few bytes and then die, which is what a crash or
    a kill part-way through a save leaves on disk."""
    def dump_then_die(obj, fp, **kwargs):
        fp.write('{\n  "name": "x",\n  "blocks": [\n    {"ty')
        raise KeyboardInterrupt("simulated kill mid-write")

    monkeypatch.setattr(json, "dump", dump_then_die)


def test_write_json_atomic_round_trip(tmp_path):
    target = tmp_path / "thing.json"
    write_json_atomic(str(target), {"a": 1, "b": [1, 2, 3]})

    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1, "b": [1, 2, 3]}
    assert not list(tmp_path.glob("*.tmp")), "scratch file left behind"


def test_write_json_atomic_leaves_the_old_file_intact(tmp_path, monkeypatch):
    target = tmp_path / "thing.json"
    write_json_atomic(str(target), {"version": "original"})

    _kill_mid_write(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        write_json_atomic(str(target), {"version": "replacement"})

    # The point of the exercise: the previous contents are still readable.
    assert json.loads(target.read_text(encoding="utf-8")) == {"version": "original"}
    assert not list(tmp_path.glob("*.tmp")), "scratch file left behind after a failed write"


def test_interrupted_save_template_keeps_the_previous_template(tmp_path, monkeypatch):
    monkeypatch.setattr(tpl, "TEMPLATES_DIR", str(tmp_path))
    blocks = [{"type": "place_unit", "x": 10, "y": 20}, {"type": "wait", "ms": 500}]
    tpl.save_template("My Farm Setup", blocks)

    _kill_mid_write(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        tpl.save_template("My Farm Setup", blocks + [{"type": "walk"}])

    # load_template reports a corrupt file as an EMPTY block list, so without
    # an atomic write this loss would be completely silent in the UI.
    assert tpl.load_template("My Farm Setup")["blocks"] == blocks


def test_interrupted_save_path_keeps_the_previous_recording(tmp_path, monkeypatch):
    monkeypatch.setattr(walk_paths, "PATHS_DIR", str(tmp_path))
    events = [{"t": 0.0, "key": "w", "state": "down"}, {"t": 2.5, "key": "w", "state": "up"}]
    walk_paths.save_path("My Own Route", events)

    _kill_mid_write(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        walk_paths.save_path("My Own Route", events)

    assert walk_paths.load_path("My Own Route")["events"] == events


def test_interrupted_save_path_does_not_revert_to_the_shipped_default(tmp_path, monkeypatch):
    """load_path falls through to Paths/defaults/ when the user's own file
    won't parse, so for a name that ALSO ships a default a truncated save
    doesn't just lose the recording -- it silently walks the shipped route
    instead, which is a different path through the map."""
    monkeypatch.setattr(walk_paths, "PATHS_DIR", str(tmp_path))
    shipped = os.path.join(walk_paths.DEFAULT_PATHS_DIR, "Kings Tomb.json")
    if not os.path.isfile(shipped):
        pytest.skip("shipped default 'Kings Tomb' not present in this checkout")

    mine = [{"t": float(i), "key": "d", "state": "down"} for i in range(7)]
    walk_paths.save_path("Kings Tomb", mine)

    _kill_mid_write(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        walk_paths.save_path("Kings Tomb", mine)

    assert walk_paths.load_path("Kings Tomb")["events"] == mine


def test_concurrent_writers_to_different_files_all_succeed(tmp_path):
    """Parallel writes to SEPARATE targets must all land intact. (This one
    passes with the old fixed-"<path>.tmp" scheme too -- distinct targets got
    distinct temp names either way. The shared-temp bug is covered by
    test_concurrent_writers_to_the_same_file_do_not_collide below.)"""
    errors = []
    threading.excepthook = lambda a: errors.append(a.exc_value)
    start = threading.Event()

    def write(i):
        start.wait()
        for n in range(20):
            write_json_atomic(str(tmp_path / f"f{i}.json"), {"i": i, "n": n})

    threads = [threading.Thread(target=write, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    start.set()
    for t in threads:
        t.join()

    assert errors == []
    for i in range(6):
        assert json.loads((tmp_path / f"f{i}.json").read_text(encoding="utf-8")) == {"i": i, "n": 19}
    assert not list(tmp_path.glob("*.tmp")), "scratch files left behind"


def test_concurrent_writers_to_the_same_file_do_not_collide(tmp_path):
    """The scratch file used to be a fixed "<path>.tmp", so two writers on the
    SAME target shared one temp file -- they wrote into each other's buffer
    and then raced os.replace, which on Windows surfaces as PermissionError
    rather than any clean failure. Each writer gets its own mkstemp name now.

    Measured against both versions at this contention: the shared-temp version
    raised in 5/5 runs, the current one in 0/5. Kept modest (4 writers, 25
    writes) deliberately -- far heavier same-target contention can still hit a
    transient rename failure on Windows, and asserting zero exceptions there
    would make this flaky on a busier CI runner rather than catch a real
    regression.
    """
    errors = []
    threading.excepthook = lambda a: errors.append(a.exc_value)
    target = tmp_path / "same.json"
    start = threading.Event()

    def write(i):
        start.wait()
        for _ in range(25):
            write_json_atomic(str(target), {"writer": i})

    threads = [threading.Thread(target=write, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    start.set()
    for t in threads:
        t.join()

    assert errors == [], f"writers collided: {errors}"
    # Last write wins, but it must be a COMPLETE record from one writer.
    assert json.loads(target.read_text(encoding="utf-8"))["writer"] in range(4)
    assert not list(tmp_path.glob("*.tmp")), "scratch files left behind"
