"""Checks GitHub Releases for a newer tagged version than the one in VERSION,
and -- once the user confirms via the Dashboard's update popup -- downloads
the release's source zip and swaps it in for the running install.

Update flow (see main.Api.apply_update): download+extract the release zip to
a temp staging folder, write a small .bat helper that waits for this process
to exit, robocopy's the staged source over the install dir (skipping
anything the user owns -- settings.json, debug/, Paths/, Templates/,
regenerated Assets, same list .gitignore excludes), then relaunches run.bat.
apply_update launches that helper detached and only THEN closes the app --
the helper doesn't touch any files until this process (and its file
handles) are actually gone.
"""
import os
import re
import subprocess
import tempfile
import zipfile

import requests

GITHUB_REPO = "Cweamy/Anime-Expeditions-Creams-Macro"
RELEASES_LATEST_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
VERSION_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "VERSION")

# Robocopy /XD (directory names, matched anywhere in the tree) / /XF (file
# names) -- everything a user's own run generates or owns, never something
# an update should overwrite.
_EXCLUDE_DIRS = ["debug", "Paths", "Templates", "__pycache__", ".git", "item_icons"]
_EXCLUDE_FILES = ["settings.json", "*.log"]


def get_current_version() -> str:
    try:
        with open(VERSION_FILE, "r", encoding="utf-8") as f:
            return f.read().strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def _parse_version(tag: str) -> tuple:
    nums = re.findall(r"\d+", tag)
    return tuple(int(n) for n in nums) if nums else (0,)


def check_for_update(timeout: float = 6.0) -> dict:
    """Never raises -- a failed check (offline, rate-limited, no releases
    yet) just reports not available so it can't break startup."""
    current = get_current_version()
    try:
        resp = requests.get(RELEASES_LATEST_URL, timeout=timeout,
                             headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return {"available": False}

    tag = data.get("tag_name") or ""
    if not tag or _parse_version(tag) <= _parse_version(current):
        return {"available": False}
    return {
        "available": True,
        "version": tag,
        "current_version": current,
        "url": data.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases",
        "zip_url": data.get("zipball_url") or f"https://github.com/{GITHUB_REPO}/archive/refs/tags/{tag}.zip",
        "notes": (data.get("body") or "").strip(),
    }


def stage_update(zip_url: str, app_dir: str, log) -> str:
    """Downloads + extracts the release zip and writes the relaunch helper
    script. Returns the helper's path -- the caller launches it detached,
    then closes the app (see main.Api.apply_update)."""
    tmp_root = tempfile.mkdtemp(prefix="aecm_update_")
    zip_path = os.path.join(tmp_root, "update.zip")

    log(f"[Update] Downloading {zip_url}...")
    resp = requests.get(zip_url, timeout=60, stream=True)
    resp.raise_for_status()
    with open(zip_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            f.write(chunk)

    extract_dir = os.path.join(tmp_root, "extracted")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_dir)

    # GitHub's zipball wraps everything in one top-level folder
    # (<owner>-<repo>-<sha>/) -- that's the actual source root to copy from.
    entries = [os.path.join(extract_dir, e) for e in os.listdir(extract_dir)]
    src_root = entries[0] if len(entries) == 1 and os.path.isdir(entries[0]) else extract_dir
    log(f"[Update] Extracted to {src_root}.")

    helper_path = os.path.join(tmp_root, "apply_update.bat")
    _write_helper_script(helper_path, src_root, app_dir, tmp_root)
    return helper_path


def _write_helper_script(helper_path: str, src_root: str, app_dir: str, tmp_root: str) -> None:
    xd = " ".join(f'"{d}"' for d in _EXCLUDE_DIRS)
    xf = " ".join(f'"{f}"' for f in _EXCLUDE_FILES)
    script = f"""@echo off
setlocal
rem "ping" instead of "timeout" -- timeout needs a real console handle and
rem this .bat is launched detached (no console), where it just errors out.
rem A couple seconds is enough for the just-closed app to release its file
rem handles before robocopy starts touching the same files.
ping -n 3 127.0.0.1 >nul

robocopy "{src_root}" "{app_dir}" /E /XD {xd} /XF {xf} /NFL /NDL /NJH /NJS

rmdir /s /q "{tmp_root}" >nul 2>nul

cd /d "{app_dir}"
start "" "run.bat"
"""
    with open(helper_path, "w", encoding="utf-8") as f:
        f.write(script)


def launch_helper(helper_path: str) -> None:
    # DETACHED_PROCESS: no console of its own, survives this process exiting
    # (which happens right after this call -- see main.Api.apply_update).
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    subprocess.Popen(
        ["cmd.exe", "/c", helper_path],
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
