"""Named Task Queue presets -- save the current queue under a name and load
it back later, without going through a file dialog.

This is deliberately NOT the same thing as the Task screen's Export/Import
(main.Api.export_tasks_file / import_tasks_file), which writes a .json
through a native save/open dialog so a queue can be shared with someone
else. That's for moving a queue BETWEEN installs; this is for switching
between your own saved queues on one install -- "Daily Story", "Raid
Farm", "Overnight" -- so it's a dropdown, not a file picker.

Stored under Templates/Tasks/ rather than as its own top-level folder:
Templates/ is already where the user's own saved JSON lives, and .gitignore
covers `Templates/` (a new top-level folder would show up as untracked for
anyone running from source) while core/updater.py's _EXCLUDE_DIRS already
names "Templates" too. list_templates() reads TEMPLATES_DIR with a
`.json` filter, so a subdirectory beside those files is invisible to it.

One deliberate difference from core/templates.py: the DISPLAY name is
stored inside the file and the filename is a separate, disambiguated slug.
templates.py derives the filename by stripping disallowed characters, so
"Farm A/B" and "Farm AB" both become "Farm AB.json" and the second save
silently destroys the first. Presets keep the two apart so that can't
happen.
"""
import json
import os
import re
import threading

from . import constants
from .jsonstore import write_json_atomic

# Serializes claim-a-slug-then-write. _slug_for() reads the folder to pick a
# free filename and save_preset() then writes it -- two savers racing that gap
# both pick the SAME slug, so one silently overwrites the other. Same reason
# core/settings.py holds a lock across its read-modify-write.
_lock = threading.Lock()

# Templates/Tasks/, not a top-level TaskPresets/ -- see the module docstring.
PRESETS_DIR = os.path.join(constants.APP_DIR, "Templates", "Tasks")

# What the queue looks like on disk. Bumped only if the shape changes in a
# way an older reader couldn't cope with.
PRESET_VERSION = 1


def _safe_slug(name: str) -> str:
    """Filename-safe form of a preset name. Same character rules as
    core/templates.py so nothing can escape PRESETS_DIR -- but here it only
    ever produces the FILENAME; the name the user typed is stored in the
    file itself (see the module docstring)."""
    cleaned = re.sub(r"[^A-Za-z0-9 _-]", "", name or "").strip().strip(".")
    return cleaned or "preset"


def _index() -> dict:
    """slug (lowercased) -> display name, for every preset on disk.
    Lowercased because Windows filenames are case-insensitive, so "Raid" and
    "raid" would be the same file and must be treated as one slug."""
    out = {}
    if not os.path.isdir(PRESETS_DIR):
        return out
    for fname in os.listdir(PRESETS_DIR):
        if not fname.endswith(".json"):
            continue
        slug = fname[:-5]
        try:
            with open(os.path.join(PRESETS_DIR, fname), "r", encoding="utf-8") as f:
                data = json.load(f)
            out[slug.lower()] = data.get("name") or slug
        except (OSError, json.JSONDecodeError):
            out[slug.lower()] = slug  # unreadable -- still occupies the slug
    return out


def _slug_for(name: str) -> str:
    """The filename to save `name` under. Reuses the existing slug when this
    display name is already saved (so re-saving overwrites rather than
    piling up copies), and picks the next free "<slug> (n)" when a DIFFERENT
    display name already owns it."""
    base = _safe_slug(name)
    index = _index()
    owner = index.get(base.lower())
    if owner is None or owner == name:
        return base
    n = 2
    while True:
        candidate = f"{base} ({n})"
        owner = index.get(candidate.lower())
        if owner is None or owner == name:
            return candidate
        n += 1


def _path_for_display_name(name: str) -> str:
    """The file holding the preset the user knows as `name`, or None."""
    for slug, display in _index().items():
        if display == name:
            for fname in os.listdir(PRESETS_DIR):
                if fname.lower() == f"{slug}.json":
                    return os.path.join(PRESETS_DIR, fname)
    return None


def list_presets() -> list:
    """Every saved preset's DISPLAY name, sorted case-insensitively."""
    return sorted(_index().values(), key=str.lower)


def save_preset(name: str, tasks: list) -> str:
    """Persist `tasks` under `name`, overwriting a preset of the same name.
    Returns the display name actually stored."""
    name = (name or "").strip()
    if not name:
        name = "Preset"
    os.makedirs(PRESETS_DIR, exist_ok=True)
    # Slug choice and write happen under one lock -- see _lock. Atomic write
    # (core/jsonstore.py) on top of that: an interrupted save must not
    # truncate the preset already on disk, since load_preset() reports a
    # corrupt file as an empty queue and the loss would be silent.
    with _lock:
        path = os.path.join(PRESETS_DIR, f"{_slug_for(name)}.json")
        write_json_atomic(path, {"version": PRESET_VERSION, "name": name, "tasks": tasks or []})
    return name


def load_preset(name: str) -> dict:
    """{"name", "tasks", "found", "error"} for a saved preset. Never raises --
    the caller shows the problem instead of failing the screen.

    `found`/`error` keep "there is no preset by that name" apart from "the
    file is there but won't parse". Both yield an empty task list, but only
    one of them means the user has a broken file sitting in the folder that
    they'd want to know about (hand-edited JSON, an interrupted copy).
    """
    path = _path_for_display_name(name)
    if not path:
        return {"name": name, "tasks": [], "found": False, "error": ""}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return {"name": name, "tasks": [], "found": True, "error": str(exc)}
    tasks = data.get("tasks")
    return {"name": data.get("name") or name,
            "tasks": tasks if isinstance(tasks, list) else [],
            "found": True, "error": ""}


def delete_preset(name: str) -> bool:
    path = _path_for_display_name(name)
    if not path:
        return False
    try:
        os.remove(path)
        return True
    except OSError:
        return False
