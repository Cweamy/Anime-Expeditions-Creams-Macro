import json
import os
import re

from . import constants
from .jsonstore import write_json_atomic

# Diretório absoluto para armazenamento de templates
TEMPLATES_DIR = os.path.abspath(os.path.join(constants.APP_DIR, "Templates"))


def _safe_name(name: str) -> str:
    # Sanitize the template name by extracting only the base name (os.path.basename)
    # and stripping characters that are not alphanumeric, space, dash, or underscore.
    base_name = os.path.basename(name or "")
    cleaned = re.sub(r"[^A-Za-z0-9 _-]", "", base_name).strip()
    return cleaned or "template"


def _resolve_template_path(name: str) -> str:
    """Resolves the absolute path of the template and validates it remains strictly inside TEMPLATES_DIR.

    Raises ValueError if navigation characters ('..', '/', '\\') are present or boundary validation fails.
    """
    if not isinstance(name, str) or ".." in name or "/" in name or "\\" in name:
        raise ValueError(f"Invalid template name containing navigation characters: {name!r}")

    safe = _safe_name(name)
    resolved_dir = os.path.abspath(TEMPLATES_DIR)
    resolved_path = os.path.abspath(os.path.join(resolved_dir, f"{safe}.json"))

    # Boundary validation against path traversal
    try:
        common = os.path.commonpath([resolved_dir, resolved_path])
    except ValueError:
        raise ValueError(f"Security check failed for template path: {name!r} outside of TEMPLATES_DIR")

    if common != resolved_dir:
        raise ValueError(f"Security check failed for template path: {name!r} outside of TEMPLATES_DIR")

    return resolved_path


def list_templates() -> list:
    # List all templates stored in the templates directory
    if not os.path.isdir(TEMPLATES_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(TEMPLATES_DIR) if f.endswith(".json"))


def save_template(name: str, blocks: list) -> str:
    # Save template atomically after validating and resolving the file path
    path = _resolve_template_path(name)
    safe = _safe_name(name)
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    write_json_atomic(path, {"name": safe, "blocks": blocks})
    return safe


def load_template(name: str) -> dict:
    # Load corresponding template if it exists and is valid
    try:
        path = _resolve_template_path(name)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return {"name": _safe_name(name), "blocks": []}


def delete_template(name: str) -> bool:
    # Delete the specified template file
    try:
        path = _resolve_template_path(name)
        os.remove(path)
        return True
    except (OSError, ValueError):
        return False


