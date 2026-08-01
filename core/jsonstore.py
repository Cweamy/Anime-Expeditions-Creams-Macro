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
import tempfile
import time

# Windows-only transient-failure allowance on the final rename -- see the
# retry loop in write_json_atomic.
_REPLACE_ATTEMPTS = 5
_REPLACE_BACKOFF = 0.02  # seconds, multiplied by the attempt number


def write_json_atomic(path: str, data, compact: bool = False) -> None:
    """Serialize `data` to `path` as JSON, atomically. Raises whatever the
    write would have raised (a caller that can't write at all should still
    hear about it) but never leaves a partial file behind.

    compact=True drops the indent=2 pretty-printing (one line, no extra
    whitespace) -- for a file meant to be hand-read/edited (a Macro Operation
    template, settings.json) indent=2 is worth the size; for one that's just
    a dense stream of small similar objects (a Record block's captured
    events, potentially thousands of them) the indentation alone can be the
    majority of the file's size for zero readability benefit at that
    density, so core.input_record opts into this instead.
    """
    # A UNIQUE scratch file per writer, not a fixed "<path>.tmp": two threads
    # writing the same target shared one temp name, so they interleaved into
    # each other's buffer and then raced os.replace -- which on Windows
    # surfaces as PermissionError [WinError 32] ("used by another process")
    # rather than any kind of clean failure. mkstemp keeps it in the SAME
    # directory so os.replace stays a same-filesystem atomic rename.
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            if compact:
                json.dump(data, f, separators=(",", ":"))
            else:
                json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        # Windows only: MoveFileEx can come back ACCESS_DENIED/SHARING_VIOLATION
        # if the destination is momentarily open -- another writer replacing it,
        # an indexer or AV holding a read handle. It's transient, so a couple of
        # short retries turn a spurious crash into a completed write. POSIX
        # rename has no such window and takes the first attempt every time.
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(_REPLACE_BACKOFF * (attempt + 1))
    except BaseException:
        # BaseException, not Exception: a KeyboardInterrupt mid-write is one
        # of the very cases this exists to survive, and it must not leave the
        # scratch file lying next to the real one.
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
