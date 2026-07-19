"""Checks GitHub Releases for a newer tagged version than the one in VERSION,
and -- once the user confirms via the Dashboard's update popup -- applies it.
Two different update strategies depending on how the app is actually
running (see core.constants.IS_FROZEN), since "swap in the new files" means
something different in each case:

Running from Python source (dev / git clone): downloads the release's
SOURCE zip (GitHub generates one for any tag automatically) and robocopy's
it over the install dir, skipping anything the user owns (settings.json,
debug/, Paths/, Templates/, regenerated Assets -- same list .gitignore
excludes).

Running as a built exe (Nuitka, see build_nuitka.py): downloads the new
release's EXE ASSET and swaps the exe file itself -- robocopying loose .py
source over a compiled exe's directory wouldn't do anything, the exe
doesn't read scattered source files at runtime. This mirrors the sibling
Anime Squadron project's core.updater, which already solved the batch-
script choreography (wait for the old exe to actually exit, move it aside,
move the new one into place, relaunch, clean up) -- ported here rather than
re-solving it blind.

Either way: main.Api.apply_update stages the update, launches the relaunch
helper detached, THEN closes the app -- the helper doesn't touch any files
until this process (and its file handles) are actually gone.
"""
import os
import re
import subprocess
import sys
import tempfile
import zipfile

import requests

from . import constants

GITHUB_REPO = "Cweamy/Anime-Expeditions-Creams-Macro"
RELEASES_LATEST_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
# BUNDLE_DIR, not APP_DIR -- VERSION ships as part of the app itself (it's
# what identifies which release you're running), not user-owned data.
VERSION_FILE = os.path.join(constants.BUNDLE_DIR, "VERSION")

# Robocopy /XD (directory names, matched anywhere in the tree) / /XF (file
# names) for the source-update path -- everything a user's own run
# generates or owns, never something an update should overwrite.
_EXCLUDE_DIRS = ["debug", "Paths", "Templates", "__pycache__", ".git", "item_icons"]
_EXCLUDE_FILES = ["settings.json", "*.log"]

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


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

    # "bootstrapper" must be excluded here -- every release has TWO .exe
    # assets (the real app and the small bootstrapper, see release.yml),
    # and picking whichever happens to come first in the API's asset order
    # risked self-updating by overwriting the real app with the tiny
    # bootstrapper. bootstrap.py's own _find_exe_asset_url() already
    # excludes it the same way; this just needed the same filter.
    exe_asset = next(
        (a for a in data.get("assets", [])
         if a.get("name", "").lower().endswith(".exe") and "bootstrapper" not in a.get("name", "").lower()),
        None)
    return {
        "available": True,
        "version": tag,
        "current_version": current,
        "url": data.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases",
        "zip_url": data.get("zipball_url") or f"https://github.com/{GITHUB_REPO}/archive/refs/tags/{tag}.zip",
        "exe_url": exe_asset["browser_download_url"] if exe_asset else None,
        "notes": (data.get("body") or "").strip(),
    }


# ---------------------------------------------------------------------------
# Source update (running from a git clone / python main.py)
# ---------------------------------------------------------------------------

def stage_source_update(zip_url: str, app_dir: str, log) -> str:
    """Downloads + extracts the release source zip and writes the relaunch
    helper script. Returns the helper's path -- the caller launches it
    detached, then closes the app (see main.Api.apply_update)."""
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
    _write_source_helper_script(helper_path, src_root, app_dir, tmp_root)
    return helper_path


def _write_source_helper_script(helper_path: str, src_root: str, app_dir: str, tmp_root: str) -> None:
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


# ---------------------------------------------------------------------------
# Exe update (running as a built/frozen exe -- ported from the sibling Anime
# Squadron project's core.updater, which already solved this)
# ---------------------------------------------------------------------------

def _current_exe_path() -> str:
    return os.path.abspath(sys.argv[0])


def download_exe_update(exe_url: str, log) -> str:
    """Downloads the new exe alongside the running one (as `<exe>.update`,
    not overwriting it yet -- the running exe's file is likely still
    locked). Returns the downloaded path for apply_exe_update to swap in."""
    current_exe = _current_exe_path()
    new_exe = current_exe + ".update"
    log(f"[Update] Downloading {exe_url}...")
    resp = requests.get(exe_url, stream=True, timeout=120)
    resp.raise_for_status()
    with open(new_exe, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            f.write(chunk)
    return new_exe


def stage_exe_update(new_exe_path: str) -> str:
    """Writes the relaunch helper script for the already-downloaded exe.
    Returns the helper's path -- same launch-detached-then-close-app
    pattern as stage_source_update."""
    current_exe = _current_exe_path()
    exe_dir = os.path.dirname(current_exe)
    exe_name = os.path.basename(current_exe)
    old_exe = current_exe + ".old"
    helper_path = os.path.join(exe_dir, "_update.bat")
    script = f"""@echo off
rem Force-kill as a safety net -- main.Api.apply_update already calls
rem close_window() (which un-parents the docked Roblox window before
rem closing, so it doesn't get taken down with this process) before
rem launching this, so by the time this runs the app should already be
rem gone. The wait loop below just covers a slow shutdown.
taskkill /F /IM "{exe_name}" >nul 2>&1
:waitloop
timeout /t 2 /nobreak >nul
tasklist /FI "IMAGENAME eq {exe_name}" /NH 2>nul | findstr /i "{exe_name}" >nul
if not errorlevel 1 goto waitloop
timeout /t 1 /nobreak >nul
rem Clean up leftover onefile self-extraction folders from old runs.
for /d %%i in ("%TEMP%\\_MEI*") do rd /s /q "%%i" >nul 2>&1
for /d %%i in ("%TEMP%\\onefile_*") do rd /s /q "%%i" >nul 2>&1
if exist "{old_exe}" del /f "{old_exe}"
move /y "{current_exe}" "{old_exe}"
move /y "{new_exe_path}" "{current_exe}"
cd /d "{exe_dir}"
start "" "{current_exe}"
del /f "{old_exe}"
del "%~f0"
"""
    with open(helper_path, "w", encoding="utf-8") as f:
        f.write(script)
    return helper_path


# ---------------------------------------------------------------------------

def launch_helper(helper_path: str) -> None:
    # DETACHED_PROCESS: no console of its own, survives this process exiting
    # (which happens right after this call -- see main.Api.apply_update).
    subprocess.Popen(
        ["cmd.exe", "/c", helper_path],
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
