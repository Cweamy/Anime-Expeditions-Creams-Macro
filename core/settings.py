import json
import os
import threading

from . import constants

SETTINGS_FILE = os.path.join(constants.APP_DIR, "settings.json")

# Serializes read-modify-write across the app. Without it, two callers doing
# load() -> change a key -> save() concurrently (e.g. the coordinate picker
# saving x, y and row-height at once) race: each loads before the other's
# save lands, then overwrites it -- the reported "my coordinates didn't
# save". update() below is the atomic multi-key setter callers should use.
_lock = threading.Lock()


def _load_unlocked() -> dict:
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_unlocked(data: dict) -> None:
    # Atomic write: dump to a temp file, fsync, then os.replace (an atomic
    # rename on both Windows and POSIX). A crash/kill mid-write can then only
    # ever leave the OLD complete file or the NEW complete file -- never a
    # half-written, truncated settings.json that load() would reject as
    # corrupt and silently replace with {} (which wiped every setting).
    tmp = SETTINGS_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, SETTINGS_FILE)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def load() -> dict:
    with _lock:
        return _load_unlocked()


def save(data: dict) -> None:
    with _lock:
        _save_unlocked(data)


def update(changes: dict) -> dict:
    """Atomically merge `changes` into the saved settings and return the
    result -- the whole read-modify-write happens under the lock, so
    concurrent callers can't clobber each other's keys (the fix for
    coordinates/settings not saving). Prefer this over the
    load()/mutate/save() pattern whenever only some keys change."""
    with _lock:
        data = _load_unlocked()
        data.update(changes)
        _save_unlocked(data)
        return data
