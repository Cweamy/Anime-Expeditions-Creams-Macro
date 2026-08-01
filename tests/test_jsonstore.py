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


def test_write_json_atomic_compact_drops_indentation(tmp_path):
    """core.input_record saves Record block events with compact=True -- a
    dense recording is thousands of small similar objects, and indent=2's
    pretty-printing whitespace would dominate the file size for no
    readability benefit at that density."""
    target = tmp_path / "compact.json"
    data = {"name": "x", "events": [{"t": 0.0, "type": "move", "x": 1, "y": 2}] * 50}

    write_json_atomic(str(target), data, compact=True)
    compact_text = target.read_text(encoding="utf-8")

    write_json_atomic(str(target), data, compact=False)
    pretty_text = target.read_text(encoding="utf-8")

    assert json.loads(compact_text) == data
    assert "\n" not in compact_text
    assert len(compact_text) < len(pretty_text)


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


def test_each_write_gets_its_own_scratch_file(tmp_path, monkeypatch):
    """The scratch file used to be a fixed "<path>.tmp", so two writers on the
    SAME target shared one temp file -- they wrote into each other's buffer
    and then raced os.replace, which on Windows surfaces as PermissionError.

    Asserted on the scratch NAMES rather than by racing threads: uniqueness is
    what the fix actually guarantees, and it holds no matter how a given
    machine schedules. A thread race only reproduces the collision
    probabilistically, and would fail on a loaded runner for reasons that have
    nothing to do with this bug.
    """
    target = tmp_path / "same.json"
    scratch_names = []
    real_replace = os.replace

    def spy(src, dst):
        scratch_names.append(os.path.basename(src))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    write_json_atomic(str(target), {"n": 1})
    write_json_atomic(str(target), {"n": 2})

    assert len(scratch_names) == 2
    assert len(set(scratch_names)) == 2, (
        f"both writes to the same target used one scratch file: {scratch_names}")
    # Still written beside the target, so os.replace stays a same-filesystem
    # atomic rename rather than a cross-device copy.
    for name in scratch_names:
        assert name.startswith("same.json"), f"scratch file not beside the target: {name}"


def test_concurrent_writers_leave_the_file_intact(tmp_path):
    """Several writers on one target is last-write-wins. The invariants that
    hold on any machine: the file is always complete, valid JSON from a single
    writer, and no scratch file is left behind.

    Deliberately does NOT assert that nobody raises. On Windows the final
    rename can still return ACCESS_DENIED when the destination is momentarily
    open, which is transient and load-dependent -- asserting on it makes the
    test fail for reasons unrelated to the code. Scratch-file uniqueness (the
    actual regression) is pinned by the test above.
    """
    target = tmp_path / "same.json"
    start = threading.Event()

    def write(i):
        start.wait()
        for _ in range(25):
            try:
                write_json_atomic(str(target), {"writer": i})
            except PermissionError:
                pass  # transient rename contention -- see the docstring

    threads = [threading.Thread(target=write, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    start.set()
    for t in threads:
        t.join()

    assert json.loads(target.read_text(encoding="utf-8"))["writer"] in range(4)
    assert not list(tmp_path.glob("*.tmp")), "scratch files left behind"
