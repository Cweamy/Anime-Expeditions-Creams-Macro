"""Crash-safe JSON writes for the user's own saved data (Macro Operation
templates, recorded walk paths).

Writing straight over the real file with json.dump means a crash, a kill, or
a power cut part-way through leaves a TRUNCATED file -- and every loader here
treats a JSONDecodeError as "empty" rather than as an error, so the failure
surfaces as a Macro Operation that quietly has no blocks, or a walk path that
replays nothing (or, for a name that also ships a default, silently falls back
to the shipped route and walks somewhere else entirely). Losing a recorded
path or a built template that way is exactly the kind of work that isn't
cheap to redo.

Same temp-file + fsync + os.replace dance core/settings.py already uses for
settings.json, for the same reason -- os.replace is atomic on both Windows and
POSIX, so an interrupted write can only ever leave the OLD complete file or
the NEW complete file, never a half-written one.
"""
import json
import os


def write_json_atomic(path: str, data) -> None:
    """Serialize `data` to `path` as JSON, atomically. Raises whatever the
    write would have raised (a caller that can't write at all should still
    hear about it) but never leaves a partial file behind."""
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        # BaseException, not Exception: a KeyboardInterrupt mid-write is one
        # of the very cases this exists to survive, and it must not leave the
        # scratch file lying next to the real one.
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
