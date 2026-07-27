import json
import os
import re

from . import constants
from .jsonstore import write_json_atomic

TEMPLATES_DIR = os.path.join(constants.APP_DIR, "Templates")


def _safe_name(name: str) -> str:
    # Template names end up as filenames straight from the UI: strip anything
    # that isn't alnum/space/dash/underscore so a name can't escape TEMPLATES_DIR.
    cleaned = re.sub(r"[^A-Za-z0-9 _-]", "", name or "").strip()
    return cleaned or "template"


def _stored_name(path: str, fallback: str) -> str:
    """The display name recorded inside a template file, or the filename if it
    has none (hand-dropped file) or won't parse."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("name") or fallback
    except (OSError, json.JSONDecodeError):
        return fallback


def _resolve(name: str) -> str:
    """Absolute path of the file holding the template called `name`.

    Tries "<safe name>.json" FIRST. That is where every template saved before
    this function existed lives, and its stored name is that same safe name --
    so for an existing install this returns on the first check and behaves
    exactly as it always did, with no directory scan and no chance of
    re-pointing a task at a different file.

    Only when that file is absent, or holds a DIFFERENT display name (the
    collision case below), does it look for the file that actually claims this
    name."""
    base = os.path.join(TEMPLATES_DIR, f"{_safe_name(name)}.json")
    if os.path.isfile(base) and _stored_name(base, _safe_name(name)) == name:
        return base
    if os.path.isdir(TEMPLATES_DIR):
        for fname in sorted(os.listdir(TEMPLATES_DIR)):
            if not fname.endswith(".json"):
                continue
            full = os.path.join(TEMPLATES_DIR, fname)
            if _stored_name(full, fname[:-5]) == name:
                return full
    return base


def _free_slug(name: str) -> str:
    """Filename to save `name` under. Reuses the slug this name already owns,
    and otherwise picks the next free "<slug> (n)".

    Without this, _safe_name maps several distinct names onto one file:
    "Farm A/B" and "Farm AB" both become "Farm AB.json", so saving the second
    silently destroyed the first -- and load_template reports a missing file
    as an EMPTY block list, so the loss showed up much later as a routine that
    simply did nothing."""
    slug = _safe_name(name)
    candidate, n = slug, 2
    while True:
        path = os.path.join(TEMPLATES_DIR, f"{candidate}.json")
        if not os.path.isfile(path) or _stored_name(path, candidate) == name:
            return candidate
        candidate = f"{slug} ({n})"
        n += 1


def list_templates() -> list:
    """Display names, which for every pre-existing template is still exactly
    the filename it always was."""
    if not os.path.isdir(TEMPLATES_DIR):
        return []
    names = []
    for fname in os.listdir(TEMPLATES_DIR):
        if fname.endswith(".json"):
            names.append(_stored_name(os.path.join(TEMPLATES_DIR, fname), fname[:-5]))
    return sorted(set(names))


def template_exists(name: str) -> bool:
    """Whether a macro is actually saved under this display name.

    list_templates() reports display names; _resolve maps one back to its
    file. Export used to go straight to load_template, which returns an
    empty dict for a name with no file -- so exporting something renamed or
    deleted in another window produced a valid-looking file full of empty
    macros, and the failure only showed up on import.
    """
    return os.path.isfile(_resolve(name))


def save_template(name: str, blocks: list) -> str:
    name = (name or "").strip() or "template"
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    path = os.path.join(TEMPLATES_DIR, f"{_free_slug(name)}.json")
    # Atomic: an interrupted save must not truncate the template that was
    # already there -- load_template() reports a corrupt file as an empty
    # block list, so the loss would be silent (see core/jsonstore.py).
    write_json_atomic(path, {"name": name, "blocks": blocks})
    return name


def load_template(name: str) -> dict:
    try:
        with open(_resolve(name), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"name": name, "blocks": []}


def delete_template(name: str) -> bool:
    try:
        os.remove(_resolve(name))
        return True
    except OSError:
        return False
