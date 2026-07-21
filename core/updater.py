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
RELEASES_PAGE_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"
# GitHub replaces spaces with dots in uploaded asset filenames (confirmed
# against real releases), so build_pyinstaller.py's "Creams Macro - Anime
# Expeditions.exe" actually ends up hosted under this name -- used as a
# best-effort fallback download link if the API call in check_for_update
# gets rate-limited (see its docstring), same fallback the sibling Anime
# Squadron project's core.updater already needed for the same reason.
FALLBACK_EXE_NAME = "Creams.Macro.-.Anime.Expeditions.exe"
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


def _latest_tag_via_redirect(timeout: float, log=None) -> str:
    """github.com/OWNER/REPO/releases/latest 302-redirects to the tagged
    release page -- reading the Location header off that redirect tells us
    the latest tag without ever touching api.github.com, which caps
    unauthenticated requests at 60/hour *per IP*. Many unrelated users can
    share a public IP (school/office networks, large-scale CGNAT some ISPs
    use), so that limit can get exhausted across a whole user base, not
    just from one person restarting the app a lot -- and a rate-limited
    (403) response used to look identical to "already up to date", since a
    non-200 status just fell into the catch-all except and reported
    "available": False either way. Ported from the sibling Anime Squadron
    project's core.updater, which hit and fixed this exact failure mode
    first. Returns "" if the redirect lookup itself fails for any reason.
    """
    try:
        resp = requests.head(RELEASES_PAGE_URL, allow_redirects=False, timeout=timeout)
        location = resp.headers.get("Location", "")
        if "/releases/tag/" in location:
            return location.rsplit("/releases/tag/", 1)[-1]
    except Exception as exc:
        if log:
            log(f"[Update] Redirect-based version check failed: {exc}")
    return ""


def check_for_update(timeout: float = 6.0, log=None) -> dict:
    """Never raises -- a failed check (offline, no releases yet) just
    reports not available so it can't break startup."""
    current = get_current_version()
    tag = _latest_tag_via_redirect(timeout, log)
    if not tag or _parse_version(tag) <= _parse_version(current):
        return {"available": False}

    # A newer tag genuinely exists -- worth spending one real API call
    # (subject to the 60/hr limit the redirect check above avoids for the
    # common "nothing new" case) to get exact asset URLs and release notes.
    try:
        resp = requests.get(RELEASES_LATEST_URL, timeout=timeout,
                             headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        if log:
            log(f"[Update] Release metadata request failed ({exc}) -- "
                f"falling back to a direct link for {tag}.")
        # Metadata call failed/rate-limited, but the redirect above already
        # confirmed a newer tag exists -- still report it, with a
        # best-effort constructed link instead of exact asset metadata.
        return {
            "available": True,
            "version": tag,
            "current_version": current,
            "url": f"https://github.com/{GITHUB_REPO}/releases/tag/{tag}",
            "zip_url": f"https://github.com/{GITHUB_REPO}/archive/refs/tags/{tag}.zip",
            "exe_url": f"https://github.com/{GITHUB_REPO}/releases/download/{tag}/{FALLBACK_EXE_NAME}",
            "notes": "",
        }

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
        "exe_url": exe_asset["browser_download_url"] if exe_asset else
                   f"https://github.com/{GITHUB_REPO}/releases/download/{tag}/{FALLBACK_EXE_NAME}",
        "notes": (data.get("body") or "").strip(),
    }


# ---------------------------------------------------------------------------
# Source update (running from a git clone / python main.py)
# ---------------------------------------------------------------------------

def stage_source_update(zip_url: str, app_dir: str, log, on_progress=None) -> str:
    """Downloads + extracts the release source zip and writes the relaunch
    helper script. Returns the helper's path -- the caller launches it
    detached, then closes the app (see main.Api.apply_update).

    on_progress(downloaded_bytes, total_bytes), if given, is called after
    every chunk -- total_bytes is 0 if the server didn't send a
    Content-Length (rare, but not worth failing over -- callers should
    treat that as "unknown", e.g. an indeterminate spinner instead of a
    percentage).
    """
    tmp_root = tempfile.mkdtemp(prefix="aecm_update_")
    zip_path = os.path.join(tmp_root, "update.zip")

    log(f"[Update] Downloading {zip_url}...")
    resp = requests.get(zip_url, timeout=60, stream=True)
    resp.raise_for_status()
    total = int(resp.headers.get("content-length") or 0)
    downloaded = 0
    with open(zip_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            f.write(chunk)
            downloaded += len(chunk)
            if on_progress:
                on_progress(downloaded, total)

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


def download_exe_update(exe_url: str, log, on_progress=None) -> str:
    """Downloads the new exe alongside the running one (as `<exe>.update`,
    not overwriting it yet -- the running exe's file is likely still
    locked). Returns the downloaded path for apply_exe_update to swap in.

    on_progress(downloaded_bytes, total_bytes), if given, is called after
    every chunk -- see stage_source_update's docstring for what total=0
    means.
    """
    current_exe = _current_exe_path()
    new_exe = current_exe + ".update"
    log(f"[Update] Downloading {exe_url}...")
    resp = requests.get(exe_url, stream=True, timeout=120)
    resp.raise_for_status()
    total = int(resp.headers.get("content-length") or 0)
    downloaded = 0
    with open(new_exe, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            f.write(chunk)
            downloaded += len(chunk)
            if on_progress:
                on_progress(downloaded, total)
    return new_exe


def stage_exe_update(new_exe_path: str) -> str:
    """Writes the relaunch helper script for the already-downloaded exe.
    Returns the helper's path -- same launch-detached-then-close-app
    pattern as stage_source_update."""
    current_exe = _current_exe_path()
    exe_dir = os.path.dirname(current_exe)
    exe_name = os.path.basename(current_exe)
    old_exe = current_exe + ".old"
    log_path = os.path.join(exe_dir, "_update.log")
    helper_path = os.path.join(exe_dir, "_update.bat")
    # This whole script runs fully detached (see launch_helper -- no
    # console window at all, by design, so the app can close cleanly right
    # after launching it), which means every "echo" below was previously
    # going nowhere: a failed move, a taskkill that didn't actually work,
    # anything at all -- all silent, with the only visible symptom being
    # "closed the app and then nothing happened" and a folder full of
    # leftover .update/.old files with zero explanation why. Every step
    # below is now ALSO appended to _update.log (next to the exe) with
    # >>"{log_path}" 2>&1, so a failure here is finally something that can
    # actually be diagnosed instead of a black box.
    script = f"""@echo off
setlocal enabledelayedexpansion
set LOG="{log_path}"
echo ---- %date% %time% ---- > %LOG%
echo Updating Cream's Macro -- please wait, this window closes itself...
echo [1/5] Stopping the running app (image: {exe_name})... >> %LOG%
rem Force-kill as a safety net -- main.Api.apply_update already calls
rem close_window() (which un-parents the docked Roblox window before
rem closing, so it doesn't get taken down with this process) before
rem launching this, so by the time this runs the app should already be
rem gone. The wait loop below just covers a slow shutdown.
taskkill /F /IM "{exe_name}" >>%LOG% 2>&1
rem "ping" instead of "timeout" -- timeout needs a real console input
rem handle, which this .bat (launched detached, see launch_helper) doesn't
rem reliably have; same trick _write_source_helper_script already uses.
set _wait=0
:waitloop
ping -n 3 127.0.0.1 >nul
tasklist /FI "IMAGENAME eq {exe_name}" /NH 2>nul | findstr /i "{exe_name}" >nul
if errorlevel 1 goto proceed
set /a _wait+=1
rem Bounded, not infinite -- a process that never actually dies (locked by
rem AV, a permission mismatch, a protected-process edge case, ...) used to
rem leave this waiting forever with the window just sitting there showing
rem nothing happening. After ~30s, force-kill once more and proceed
rem anyway: a failed move below at least surfaces a real error instead of
rem hanging indefinitely with no explanation.
if !_wait! lss 15 goto waitloop
echo Still running after 30s -- forcing it closed and continuing anyway. >>%LOG%
taskkill /F /IM "{exe_name}" >>%LOG% 2>&1
ping -n 2 127.0.0.1 >nul
:proceed
echo [2/5] Old process confirmed gone (or timed out waiting). >>%LOG%
rem Clean up leftover onefile self-extraction folders from old runs.
for /d %%i in ("%TEMP%\\_MEI*") do rd /s /q "%%i" >nul 2>&1
for /d %%i in ("%TEMP%\\onefile_*") do rd /s /q "%%i" >nul 2>&1
if exist "{old_exe}" del /f "{old_exe}" >>%LOG% 2>&1
echo [3/5] Moving current exe to "{old_exe}"... >>%LOG%
move /y "{current_exe}" "{old_exe}" >>%LOG% 2>&1
if not exist "{old_exe}" (
    echo [FAILED] Could not move the running exe aside -- it may still be locked. >>%LOG%
    echo Update aborted. The app was NOT relaunched -- start it manually from "{current_exe}". >>%LOG%
    goto :eof
)
echo [4/5] Moving downloaded update into place... >>%LOG%
move /y "{new_exe_path}" "{current_exe}" >>%LOG% 2>&1
if not exist "{current_exe}" (
    echo [FAILED] Could not move the downloaded update into place. >>%LOG%
    echo Restoring the previous exe from "{old_exe}" so the app still runs... >>%LOG%
    move /y "{old_exe}" "{current_exe}" >>%LOG% 2>&1
    cd /d "{exe_dir}"
    start "" "{current_exe}"
    echo Update failed -- reverted to the previous version and relaunched it. >>%LOG%
    goto :eof
)
echo [5/5] Relaunching... >>%LOG%
cd /d "{exe_dir}"
start "" "{current_exe}"
del /f "{old_exe}" >>%LOG% 2>&1
echo Update finished successfully. >>%LOG%
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
