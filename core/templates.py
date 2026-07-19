import json
import os
import re

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Templates")


def _safe_name(name: str) -> str:
    # Template names end up as filenames straight from the UI: strip anything
    # that isn't alnum/space/dash/underscore so a name can't escape TEMPLATES_DIR.
    cleaned = re.sub(r"[^A-Za-z0-9 _-]", "", name or "").strip()
    return cleaned or "template"


def list_templates() -> list:
    if not os.path.isdir(TEMPLATES_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(TEMPLATES_DIR) if f.endswith(".json"))


def save_template(name: str, blocks: list) -> str:
    name = _safe_name(name)
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    path = os.path.join(TEMPLATES_DIR, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"name": name, "blocks": blocks}, f, indent=2)
    return name


def load_template(name: str) -> dict:
    path = os.path.join(TEMPLATES_DIR, f"{_safe_name(name)}.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"name": name, "blocks": []}


def delete_template(name: str) -> bool:
    path = os.path.join(TEMPLATES_DIR, f"{_safe_name(name)}.json")
    try:
        os.remove(path)
        return True
    except OSError:
        return False
