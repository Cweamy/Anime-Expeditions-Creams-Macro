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

Running as a built exe (see build_pyinstaller.py): downloads the release
zip (the only binary asset a release publishes -- exe + Assets/ side by
side), extracts the new exe out of it, and swaps the exe file itself --
robocopying loose .py source over a compiled exe's directory wouldn't do
anything, the exe doesn't read scattered source files at runtime. The
batch-script swap choreography (wait for the old exe to actually exit,
move it aside, move the new one into place, relaunch, clean up) mirrors
the sibling Anime Squadron project's core.updater, which already solved
it -- ported here rather than re-solving it blind. The user-editable
Assets/ folder beside the exe is NEVER part of the swap -- an update
leaves it alone except for an add-only merge, from that same zip, of any
reference images that are new in the release (see the Assets section
below).

Either way: main.Api.apply_update stages the update, launches the relaunch
helper detached, THEN closes the app -- the helper doesn't touch any files
until this process (and its file handles) are actually gone.
"""
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

import requests

from . import constants

GITHUB_REPO = "Cweamy/Anime-Expeditions-Creams-Macro"
RELEASES_LATEST_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"
# The packaged release zip (exe + the loose Assets/ folder side by side,
# see release.yml) -- the ONE download everything uses: new installs, the
# bootstrapper, AND frozen-build updates (the exe is extracted out of it
# and swapped, the Assets entries add-only merged, all from a single
# download -- see download_release_update). Dashed name on purpose:
# GitHub rewrites spaces in uploaded asset filenames to dots, dashes stay
# put, so the constructed fallback URL below stays predictable when the
# API call in check_for_update gets rate-limited. Releases used to also
# ship the bare exe and a separate Assets.zip for these flows; folded into
# this one file to keep the release's asset list from being a wall of
# downloads where picking the wrong one is easy.
#
# Per-platform: each OS's build is its own explicitly-suffixed zip
# (-Windows / -macOS, so neither reads as "the default" on the release
# page), and everything here that names the asset (update download,
# ensure_assets_present's constructed URLs) resolves to the RUNNING
# platform's zip automatically. The Windows zip briefly shipped unsuffixed
# (v0.3.0-v0.4.0 as published) -- renamed for symmetry once the mac zip
# joined it.
RELEASE_ZIP_NAME = ("Creams-Macro-Anime-Expeditions-macOS.zip" if sys.platform == "darwin"
                     else "Creams-Macro-Anime-Expeditions-Windows.zip")
# BUNDLE_DIR, not APP_DIR -- VERSION ships as part of the app itself (it's
# what identifies which release you're running), not user-owned data.
VERSION_FILE = os.path.join(constants.BUNDLE_DIR, "VERSION")

# Robocopy /XD (directory names, matched anywhere in the tree) / /XF (file
# names) for the source-update path -- everything a user's own run
# generates or owns, never something an update should overwrite.
_EXCLUDE_DIRS = ["debug", "Paths", "Templates", "__pycache__", ".git", "item_icons"]
_EXCLUDE_FILES = ["settings.json", "*.log", "assets_manifest.json"]

# Win32 process-creation flags (see launch_helper for why the update helper
# uses CREATE_NO_WINDOW rather than DETACHED_PROCESS).
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


def get_current_version() -> str:
    try:
        with open(VERSION_FILE, "r", encoding="utf-8") as f:
            return f.read().strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def _parse_version(tag: str) -> tuple:
    # Only the leading dotted number run counts. Collecting EVERY digit run in
    # the tag meant a pre-release like v0.11.0-beta2 parsed as (0, 11, 0, 2),
    # which sorts ABOVE the finished (0, 11, 0) -- so a user on the real
    # 0.11.0 would be offered an "update" back down to the pre-release.
    match = re.search(r"\d+(?:\.\d+)*", tag or "")
    if not match:
        return (0,)
    return tuple(int(n) for n in match.group(0).split("."))


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
        with requests.head(RELEASES_PAGE_URL, allow_redirects=False, timeout=timeout) as resp:
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
        with requests.get(RELEASES_LATEST_URL, timeout=timeout,
                          headers={"Accept": "application/vnd.github+json"}) as resp:
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
            "release_zip_url": f"https://github.com/{GITHUB_REPO}/releases/download/{tag}/{RELEASE_ZIP_NAME}",
            "notes": "",
        }

    # Flexible asset matching, learned the hard way: exact-name matching
    # plus a constructed fallback URL means ANY rename of the zip strands
    # every already-shipped updater on a 404 (exactly what happened when
    # the bare-exe asset was dropped, and again when the Windows zip
    # gained its -Windows suffix). Match by platform suffix first, then
    # the legacy unsuffixed name, and only then fall back to the
    # constructed URL for the current canonical name.
    assets = data.get("assets", [])
    suffix = "-macos.zip" if sys.platform == "darwin" else "-windows.zip"
    release_zip_asset = (
        next((a for a in assets if a.get("name", "").lower().endswith(suffix)), None)
        or next((a for a in assets if a.get("name", "").lower() == "creams-macro-anime-expeditions.zip"), None))
    return {
        "available": True,
        "version": tag,
        "current_version": current,
        "url": data.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases",
        "zip_url": data.get("zipball_url") or f"https://github.com/{GITHUB_REPO}/archive/refs/tags/{tag}.zip",
        "release_zip_url": release_zip_asset["browser_download_url"] if release_zip_asset else
                           f"https://github.com/{GITHUB_REPO}/releases/download/{tag}/{RELEASE_ZIP_NAME}",
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
    try:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length") or 0)
        downloaded = 0
        with open(zip_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                f.write(chunk)
                downloaded += len(chunk)
                if on_progress:
                    on_progress(downloaded, total)
    finally:
        resp.close()

    extract_dir = os.path.join(tmp_root, "extracted")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_dir)

    # GitHub's zipball wraps everything in one top-level folder
    # (<owner>-<repo>-<sha>/) -- that's the actual source root to copy from.
    entries = [os.path.join(extract_dir, e) for e in os.listdir(extract_dir)]
    src_root = entries[0] if len(entries) == 1 and os.path.isdir(entries[0]) else extract_dir
    log(f"[Update] Extracted to {src_root}.")

    # Done here in Python, live, rather than left entirely to the detached
    # helper's plain add-only robocopy/rsync pass -- Assets images aren't
    # locked the way the running app's own source files are, so there's no
    # need to wait for process exit, and only Python can apply the
    # manifest/hash check that tells a genuine fix apart from a user's own
    # edit (see _merge_assets_dir). The helper's own Assets pass still runs
    # afterward as a dumb add-only backup for anything this missed.
    try:
        _merge_assets_dir(os.path.join(src_root, "Assets"), os.path.join(app_dir, "Assets"), log)
    except Exception as exc:
        log(f"[Update] Assets merge skipped ({exc}) -- existing Assets folder left as-is.")

    helper_path = os.path.join(tmp_root, "apply_update.bat")
    _write_source_helper_script(helper_path, src_root, app_dir, tmp_root)
    return helper_path


def _write_source_helper_script(helper_path: str, src_root: str, app_dir: str, tmp_root: str) -> None:
    if sys.platform == "darwin":
        _write_source_helper_script_mac(helper_path, src_root, app_dir, tmp_root)
        return
    xd = " ".join(f'"{d}"' for d in _EXCLUDE_DIRS)
    xf = " ".join(f'"{f}"' for f in _EXCLUDE_FILES)
    script = f"""@echo off
setlocal
rem "ping" instead of "timeout" -- timeout needs a real console handle and
rem this .bat is launched detached (no console), where it just errors out.
rem A couple seconds is enough for the just-closed app to release its file
rem handles before robocopy starts touching the same files.
ping -n 3 127.0.0.1 >nul

rem Assets is excluded from this main copy ON PURPOSE: its images are
rem user-editable reference crops (replace/add variants without a rebuild,
rem see core/vision.py + the Image Manager), so blindly overwriting them
rem with the release's copies would throw away exactly the kind of local
rem fix the folder exists to hold.
robocopy "{src_root}" "{app_dir}" /E /XD "Assets" {xd} /XF {xf} /NFL /NDL /NJH /NJS

rem Assets gets its own ADD-ONLY pass instead: /XC /XN /XO together skip
rem every file that already exists in the destination (changed, newer, and
rem older ones -- i.e. all of them), so this only brings in reference
rem images that are genuinely NEW in this release and never touches ones
rem already on disk. Trade-off, accepted: a release that FIXES an existing
rem image won't overwrite a local copy of it -- delete the local file (or
rem folder) and re-update to take the shipped one. Same policy
rem merge_assets_update applies for exe installs.
robocopy "{src_root}\\Assets" "{app_dir}\\Assets" /E /XC /XN /XO /XD "item_icons" /NFL /NDL /NJH /NJS

rmdir /s /q "{tmp_root}" >nul 2>nul

cd /d "{app_dir}"
start "" "run.bat"
"""
    with open(helper_path, "w", encoding="utf-8") as f:
        f.write(script)


def _write_source_helper_script_mac(helper_path: str, src_root: str, app_dir: str, tmp_root: str) -> None:
    """The .bat helper's macOS twin: rsync instead of robocopy (ships with
    macOS), same exclusion list and the same add-only Assets policy
    (--ignore-existing), relaunching via run.sh instead of run.bat."""
    excludes = " ".join(f"--exclude '{d}/'" for d in _EXCLUDE_DIRS) + " --exclude 'Assets/'"
    excludes += " " + " ".join(f"--exclude '{f}'" for f in _EXCLUDE_FILES)
    script = f"""#!/bin/bash
# Give the just-closed app a moment to release its file handles.
sleep 2

rsync -a {excludes} "{src_root}/" "{app_dir}/"

# Assets: ADD-ONLY (never overwrite the user's own edited/added reference
# images) -- same policy as the Windows helper's robocopy /XC /XN /XO pass
# and core.updater's merge, see the Assets section in updater.py.
rsync -a --ignore-existing --exclude 'item_icons/' "{src_root}/Assets/" "{app_dir}/Assets/"

rm -rf "{tmp_root}"

cd "{app_dir}"
chmod +x run.sh 2>/dev/null
nohup ./run.sh >/dev/null 2>&1 &
"""
    with open(helper_path, "w", encoding="utf-8") as f:
        f.write(script)
    os.chmod(helper_path, 0o755)


# ---------------------------------------------------------------------------
# Assets (the user-editable reference-image folder shipped BESIDE the exe,
# not inside it -- see build_pyinstaller.py / release.yml / core.constants.
# ASSETS_DIR). Sourced from the release zip's Assets/ entries (no separate
# Assets.zip asset anymore -- see RELEASE_ZIP_NAME). Add-only for anything
# genuinely new (a new macro step's button crop, a new map's name label),
# same everywhere: an update never overwrites a file already on disk unless
# it can PROVE that file is still exactly what an update last put there
# (see ASSETS_MANIFEST_FILE below) -- otherwise it's a user-replaced/
# user-added variant and the whole point of the loose folder is to protect
# it. The source-update path enforces the same never-touch-unproven-files
# policy via robocopy /XC /XN /XO (see _write_source_helper_script), backed
# by the same manifest-aware pass in _merge_assets_dir below.
# ---------------------------------------------------------------------------

# Every relative Assets path this install has ever written itself, mapped to
# the sha256 of exactly what it wrote -- NOT touched for anything this app
# didn't write (a pre-existing file skipped as "unproven" stays unproven
# forever, never retroactively marked safe just because we looked at it).
# Lives beside settings.json in APP_DIR, not inside Assets itself: it's this
# install's own bookkeeping, not something a release ships or a user should
# ever need to open.
ASSETS_MANIFEST_FILE = os.path.join(constants.APP_DIR, "assets_manifest.json")


def _load_assets_manifest() -> dict:
    try:
        with open(ASSETS_MANIFEST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}  # missing/corrupt -- every existing file is then "unproven", the safe default


def _save_assets_manifest(manifest: dict) -> None:
    # Same atomic-write shape as core.settings.save -- a crash mid-write
    # must never leave a half-written manifest that then reads as "empty"
    # (which would just make every tracked file look unproven again, not
    # dangerous, but pointless data loss) or as corrupt JSON.
    try:
        tmp = ASSETS_MANIFEST_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, sort_keys=True)
        os.replace(tmp, ASSETS_MANIFEST_FILE)
    except OSError:
        pass  # best-effort -- worst case, the next update re-treats these as unproven


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _assets_rel_parts(filename: str):
    """Shared by the zip and directory-tree merges: strips a zip/tree entry
    down to its path relative to Assets/ (see _extract_assets_zip_addonly's
    old docstring for the "Assets at the root or one wrapper folder down"
    and zip-slip reasoning), or None if this entry isn't an Assets file at
    all. item_icons/ is runtime-fetched wiki icon cache (core.rewards),
    never actually shipped in a release, but skipped defensively anyway in
    case that ever changes -- same exclusion the robocopy/rsync passes use."""
    parts = filename.replace("\\", "/").split("/")
    asset_idx = next((i for i, p in enumerate(parts[:2]) if p.lower() == "assets"), None)
    if asset_idx is None or len(parts) < asset_idx + 2:
        return None
    parts = parts[asset_idx + 1:]
    if any(p in ("", ".", "..") for p in parts):
        return None
    # Containment checked on the RESOLVED destination rather than by pattern:
    # the old `":" in parts[0]` only looked at the FIRST component, and
    # os.path.join restarts at any later absolute one, so "a/b/D:/payload.exe"
    # passed the check and resolved to "D:payload.exe" -- outside Assets/.
    dest = os.path.realpath(os.path.join(constants.ASSETS_DIR, *parts))
    root = os.path.realpath(constants.ASSETS_DIR)
    if dest != root and not dest.startswith(root + os.sep):
        return None
    if parts[0] == "item_icons":
        return None
    return parts


def _extract_assets_zip_addonly(zip_path: str, log) -> int:
    """Extracts the Assets/ entries of a release zip into constants.
    ASSETS_DIR (see the Assets policy note above). A file that doesn't
    exist yet is always added. A file that DOES already exist only gets
    overwritten when the manifest proves it's exactly what THIS app last
    wrote there (untouched since) -- anything untracked, or whose on-disk
    hash has drifted from that, is left alone as a user's own edit/
    replacement. Returns how many files were actually written (added +
    refreshed)."""
    manifest = _load_assets_manifest()
    added = 0
    refreshed = 0
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            parts = _assets_rel_parts(info.filename)
            if parts is None:
                continue
            rel_key = "/".join(parts)
            dest = os.path.join(constants.ASSETS_DIR, *parts)
            if os.path.exists(dest):
                last_shipped = manifest.get(rel_key)
                if last_shipped is None or _sha256_file(dest) != last_shipped:
                    continue  # untracked, or edited/replaced since -- never overwrite
                data = zf.read(info)
                new_hash = _sha256_bytes(data)
                if new_hash == last_shipped:
                    continue  # unchanged in this release -- nothing to do
                with open(dest, "wb") as out:
                    out.write(data)
                manifest[rel_key] = new_hash
                refreshed += 1
            else:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                data = zf.read(info)
                with open(dest, "wb") as out:
                    out.write(data)
                manifest[rel_key] = _sha256_bytes(data)
                added += 1
    _save_assets_manifest(manifest)
    if added or refreshed:
        extra = f", refreshed {refreshed} unedited file(s) with fixes" if refreshed else ""
        log(f"[Update] Added {added} new Assets file(s){extra} (edited/replaced files left untouched).")
    return added + refreshed


def _merge_assets_dir(src_assets_dir: str, dest_assets_dir: str, log) -> int:
    """Directory-tree twin of _extract_assets_zip_addonly for the
    source-update path, where the new release already sits on disk (an
    extracted zip, not read entry-by-entry) -- same manifest/hash policy,
    see that function's docstring. Runs from Python BEFORE the detached
    robocopy/rsync helper script does its own (plain add-only, hash-blind)
    Assets pass, so this is what actually delivers fixes to untouched
    files; the helper's own pass is just a backup net for anything this
    step didn't reach."""
    if not os.path.isdir(src_assets_dir):
        return 0
    manifest = _load_assets_manifest()
    added = 0
    refreshed = 0
    for root, dirs, files in os.walk(src_assets_dir):
        dirs[:] = [d for d in dirs if d != "item_icons"]
        for name in files:
            src_file = os.path.join(root, name)
            rel = os.path.relpath(src_file, src_assets_dir)
            rel_key = rel.replace(os.sep, "/")
            dest = os.path.join(dest_assets_dir, rel)
            if os.path.exists(dest):
                last_shipped = manifest.get(rel_key)
                if last_shipped is None or _sha256_file(dest) != last_shipped:
                    continue
                new_hash = _sha256_file(src_file)
                if new_hash == last_shipped:
                    continue
                shutil.copy2(src_file, dest)
                manifest[rel_key] = new_hash
                refreshed += 1
            else:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copy2(src_file, dest)
                manifest[rel_key] = _sha256_file(src_file)
                added += 1
    _save_assets_manifest(manifest)
    if added or refreshed:
        extra = f", refreshed {refreshed} unedited file(s) with fixes" if refreshed else ""
        log(f"[Update] Added {added} new Assets file(s){extra} (edited/replaced files left untouched).")
    return added + refreshed


def _get_release_zip_with_fallback(release_zip_url: str, log):
    """requests.get(stream=True) for a release zip, retrying across every
    name the zip has ever shipped under when the given URL 404s.

    The zip's filename has changed once already (unsuffixed ->
    -Windows/-macOS in v0.4.1), and each rename strands every install
    whose updater asks for the old name by exact constructed URL -- the
    exact 404 the v0.4.1 release itself was cut to patch over, then seen
    again live from a v0.4.0 install against v0.5.0. Flexible asset-list
    matching (check_for_update) already handles renames when the API
    answers; this covers the OTHER path, where a rate-limited API left
    only a constructed URL to try. Trying the short list of known names
    beats shipping every release with duplicate compatibility assets.

    Returns the streaming response. Raises like requests.get/raise_for_
    status would if every candidate fails (the LAST candidate's error, or
    the first non-404 error immediately -- a rate-limit/network failure on
    the real name shouldn't get retried into confusion on legacy names)."""
    base, _, name = release_zip_url.rpartition("/")
    candidates = [release_zip_url]
    for legacy in (RELEASE_ZIP_NAME, "Creams-Macro-Anime-Expeditions.zip"):
        alt = f"{base}/{legacy}"
        if alt not in candidates:
            candidates.append(alt)
    for i, url in enumerate(candidates):
        log(f"[Update] Downloading {url}...")
        resp = requests.get(url, timeout=120, stream=True)
        if resp.status_code == 404 and i < len(candidates) - 1:
            resp.close()  # streamed response -- hand the connection back before retrying
            log("[Update] Not found under that name -- trying the release zip's other known name.")
            continue
        resp.raise_for_status()
        return resp
    resp.raise_for_status()  # unreachable in practice; keeps the contract obvious


def merge_assets_update(release_zip_url: str, log) -> bool:
    """Downloads a release zip and add-only merges its Assets/ entries into
    the local Assets folder, ignoring the exe it also carries -- the
    restore path ensure_assets_present uses. Never raises: a failed fetch
    (rate limit, offline) logs and reports False rather than breaking the
    caller's flow."""
    tmp_root = tempfile.mkdtemp(prefix="aecm_assets_")
    zip_path = os.path.join(tmp_root, RELEASE_ZIP_NAME)
    try:
        resp = _get_release_zip_with_fallback(release_zip_url, log)
        with open(zip_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                f.write(chunk)
        _extract_assets_zip_addonly(zip_path, log)
        return True
    except Exception as exc:
        log(f"[Update] Assets merge skipped ({exc}) -- existing Assets folder left as-is.")
        return False
    finally:
        # rmtree, not remove()+rmdir(): when the download never got as far as
        # creating the zip (offline, or every candidate name 404s) the
        # os.remove raised, so the os.rmdir never ran and an empty aecm_*
        # folder was left in %TEMP% after every failed attempt.
        shutil.rmtree(tmp_root, ignore_errors=True)


def ensure_assets_present(log) -> bool:
    """Startup safety net for a bare exe with NO Assets folder next to it
    (someone shared just the exe, or an old bootstrapper install predating
    the exe+Assets zip layout): without Assets/ui every image search is
    dead on arrival, so try to fetch this exact version's release zip from
    its GitHub release and lay it down. Checks the ui/ subfolder rather
    than the bare Assets dir since Settings' "Open Assets Folder" creates
    empty scaffolding folders -- existing-but-empty needs the download just
    as much as missing does. No-op when images are already there (the
    normal case, costs one isdir+listdir); returns False, with the log
    saying so, when offline/rate-limited -- the app still launches, just
    with image search unavailable until Assets exists."""
    ui_dir = os.path.join(constants.ASSETS_DIR, "ui")
    try:
        if os.path.isdir(ui_dir) and os.listdir(ui_dir):
            return True
    except OSError:
        pass
    log("[Update] No Assets folder found beside the app -- downloading it from GitHub...")
    # This exact version's release zip first (its Assets are guaranteed to
    # match what the exe searches for), falling back to latest if that
    # tag's asset is missing (e.g. a release cut before the zip layout
    # existed). Only the zip's Assets/ entries are extracted -- the exe it
    # also carries is ignored (see _extract_assets_zip_addonly).
    current = get_current_version()
    urls = [f"https://github.com/{GITHUB_REPO}/releases/download/v{current}/{RELEASE_ZIP_NAME}",
            f"https://github.com/{GITHUB_REPO}/releases/latest/download/{RELEASE_ZIP_NAME}"]
    for url in urls:
        if merge_assets_update(url, log):
            log("[Update] Assets folder restored.")
            return True
    log("[Update] Couldn't download the Assets folder -- image search won't work until "
        "Assets/ exists next to the app (re-download the release zip to fix this).")
    return False


# ---------------------------------------------------------------------------
# Exe update (running as a built/frozen exe -- ported from the sibling Anime
# Squadron project's core.updater, which already solved this)
# ---------------------------------------------------------------------------

def _current_exe_path() -> str:
    return os.path.abspath(sys.argv[0])


def _current_app_bundle_path() -> str:
    """The running frozen mac build's .app directory -- sys.executable is
    <...>/Foo.app/Contents/MacOS/<binary>, so walk up until the .app."""
    path = os.path.abspath(sys.executable)
    while path and not path.endswith(".app"):
        parent = os.path.dirname(path)
        if parent == path:
            raise RuntimeError("Couldn't locate the .app bundle around the running binary -- "
                                "is this actually the packaged mac build?")
        path = parent
    return path


def _download_release_update_mac(release_zip_url: str, log, on_progress=None) -> str:
    """The mac twin of the Windows exe staging below: downloads the release
    zip and extracts its whole .app BUNDLE (a directory tree, not a single
    file) into "<current>.app.update" NEXT TO the running bundle -- same
    volume, so the helper's swap is two cheap renames. Returns the staged
    bundle's path (stage_app_update writes the swap helper for it).

    zipfile drops Unix permissions and symlinks by default, and a .app
    whose Contents/MacOS binary lost its exec bit simply won't launch --
    so both are restored by hand from each entry's external_attr (mode
    bits in the high 16; a symlink entry's file content IS its target).
    The add-only Assets merge rides along from the same download, exactly
    like the Windows path."""
    app_path = _current_app_bundle_path()
    staged = app_path + ".update"
    tmp_root = tempfile.mkdtemp(prefix="aecm_update_")
    zip_path = os.path.join(tmp_root, RELEASE_ZIP_NAME)
    try:
        resp = _get_release_zip_with_fallback(release_zip_url, log)
        total = int(resp.headers.get("content-length") or 0)
        downloaded = 0
        with open(zip_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                f.write(chunk)
                downloaded += len(chunk)
                if on_progress:
                    on_progress(downloaded, total)

        with zipfile.ZipFile(zip_path) as zf:
            # The .app can sit at the zip root OR one wrapper folder down --
            # ditto's --keepParent wrapped every zip up to v0.6.2 in a
            # "package/" folder (confirmed against the real published zip),
            # and the workflow fix that drops the wrapper must not strand
            # updates FROM those older zips if one is ever re-fetched.
            prefix = None
            for n in zf.namelist():
                parts = n.split("/")
                for i, part in enumerate(parts[:2]):
                    if part.endswith(".app"):
                        prefix = "/".join(parts[:i + 1]) + "/"
                        break
                if prefix:
                    break
            if prefix is None:
                raise RuntimeError(f"No .app bundle found inside {RELEASE_ZIP_NAME} -- can't stage the update.")
            if os.path.exists(staged):
                shutil.rmtree(staged)
            for info in zf.infolist():
                if not info.filename.startswith(prefix):
                    continue
                rel = info.filename[len(prefix):]
                if not rel or ".." in rel.split("/"):
                    continue  # bundle root itself / no zip-slip escapes
                dest = os.path.join(staged, *rel.split("/"))
                if info.is_dir():
                    os.makedirs(dest, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    os.symlink(zf.read(info).decode("utf-8"), dest)
                    continue
                with zf.open(info) as src, open(dest, "wb") as out:
                    while True:
                        chunk = src.read(1 << 16)
                        if not chunk:
                            break
                        out.write(chunk)
                if mode:
                    os.chmod(dest, mode & 0o7777)

        try:
            _extract_assets_zip_addonly(zip_path, log)
        except Exception as exc:
            log(f"[Update] Assets merge skipped ({exc}) -- existing Assets folder left as-is.")
        return staged
    finally:
        # rmtree, not remove()+rmdir(): when the download never got as far as
        # creating the zip (offline, or every candidate name 404s) the
        # os.remove raised, so the os.rmdir never ran and an empty aecm_*
        # folder was left in %TEMP% after every failed attempt.
        shutil.rmtree(tmp_root, ignore_errors=True)


def stage_app_update(staged_app_path: str) -> str:
    """Writes the mac swap helper for a bundle staged by
    _download_release_update_mac: wait for this process to exit, rename the
    old bundle aside, rename the staged one into place (restoring the old
    on failure rather than leaving no app at all), clear quarantine
    defensively, relaunch, clean up. Every step logs to _update.log next
    to the bundle -- same no-black-boxes policy the Windows .bat learned
    the hard way."""
    app_path = _current_app_bundle_path()
    parent = os.path.dirname(app_path)
    log_path = os.path.join(parent, "_update.log")
    helper_path = os.path.join(parent, "_update.sh")
    old_path = app_path + ".old"
    pid = os.getpid()
    script = f"""#!/bin/bash
LOG="{log_path}"
echo "---- $(date) ----" > "$LOG"
echo "[1/4] Waiting for the app (pid {pid}) to exit..." >> "$LOG"
for _ in $(seq 1 120); do
    kill -0 {pid} 2>/dev/null || break
    sleep 0.5
done
echo "[2/4] Swapping the bundle..." >> "$LOG"
rm -rf "{old_path}" >> "$LOG" 2>&1
mv "{app_path}" "{old_path}" >> "$LOG" 2>&1
if ! mv "{staged_app_path}" "{app_path}" >> "$LOG" 2>&1; then
    echo "Swap failed -- restoring the previous version." >> "$LOG"
    mv "{old_path}" "{app_path}" >> "$LOG" 2>&1
    open "{app_path}"
    exit 1
fi
xattr -dr com.apple.quarantine "{app_path}" >> "$LOG" 2>&1
echo "[3/4] Relaunching..." >> "$LOG"
open "{app_path}" >> "$LOG" 2>&1
echo "[4/4] Cleaning up." >> "$LOG"
rm -rf "{old_path}" >> "$LOG" 2>&1
rm -f "{helper_path}"
"""
    with open(helper_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(script)
    os.chmod(helper_path, 0o755)
    return helper_path


def download_release_update(release_zip_url: str, log, on_progress=None) -> str:
    """Downloads the release zip and stages BOTH halves of a frozen-build
    update from that single file: the new exe is extracted alongside the
    running one (as `<exe>.update`, not overwriting it yet -- the running
    exe's file is likely still locked; stage_exe_update's helper swaps it
    in after this process exits), and any NEW Assets images are add-only
    merged immediately. Releases used to ship the bare exe and a separate
    Assets.zip so these were two downloads -- folded into the one zip the
    release publishes anyway (see RELEASE_ZIP_NAME). Returns the staged
    exe's path.

    The Assets merge is best-effort: a corrupt/odd Assets entry logs and
    moves on rather than aborting an exe update that's already fully
    downloaded. A MISSING exe inside the zip, though, is a real failure --
    there'd be nothing to update to -- so that raises.

    on_progress(downloaded_bytes, total_bytes), if given, is called after
    every chunk -- see stage_source_update's docstring for what total=0
    means.

    On macOS the staged payload is the whole .app bundle, not a single
    exe -- see _download_release_update_mac (whose return value goes to
    stage_app_update instead of stage_exe_update).
    """
    if sys.platform == "darwin":
        return _download_release_update_mac(release_zip_url, log, on_progress)
    current_exe = _current_exe_path()
    new_exe = current_exe + ".update"
    tmp_root = tempfile.mkdtemp(prefix="aecm_update_")
    zip_path = os.path.join(tmp_root, RELEASE_ZIP_NAME)
    try:
        resp = _get_release_zip_with_fallback(release_zip_url, log)
        total = int(resp.headers.get("content-length") or 0)
        downloaded = 0
        with open(zip_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                f.write(chunk)
                downloaded += len(chunk)
                if on_progress:
                    on_progress(downloaded, total)

        with zipfile.ZipFile(zip_path) as zf:
            # The app exe sits at the zip's root (release.yml packages
            # "<exe>" + "Assets/" side by side) -- matched by position and
            # extension rather than exact name so a future exe rename
            # doesn't silently break updating.
            exe_info = next(
                (i for i in zf.infolist()
                 if not i.is_dir()
                 and "/" not in i.filename.replace("\\", "/")
                 and i.filename.lower().endswith(".exe")),
                None)
            if exe_info is None:
                raise RuntimeError(f"No app exe found inside {RELEASE_ZIP_NAME} -- can't stage the update.")
            with zf.open(exe_info) as src, open(new_exe, "wb") as out:
                while True:
                    chunk = src.read(1 << 16)
                    if not chunk:
                        break
                    out.write(chunk)

        try:
            _extract_assets_zip_addonly(zip_path, log)
        except Exception as exc:
            log(f"[Update] Assets merge skipped ({exc}) -- existing Assets folder left as-is.")
        return new_exe
    finally:
        # rmtree, not remove()+rmdir(): when the download never got as far as
        # creating the zip (offline, or every candidate name 404s) the
        # os.remove raised, so the os.rmdir never ran and an empty aecm_*
        # folder was left in %TEMP% after every failed attempt.
        shutil.rmtree(tmp_root, ignore_errors=True)


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
    # This script runs fully detached, allowing the main app process to exit while the batch script waits for Windows file handle locks to release.
    script = f"""@echo off
setlocal enabledelayedexpansion
set LOG="{log_path}"
echo ---- %date% %time% ---- > %LOG%
echo Updating Cream's Macro -- please wait, this window closes itself...
echo [1/5] Waiting for the app to close itself (image: {exe_name})... >> %LOG%
rem taskkill is the SAFETY NET for a shutdown that hangs, not the way the app
rem normally closes -- so wait for the app to go on its own FIRST and only
rem force it if it doesn't.
rem
rem main.Api.apply_update launches this helper and only then schedules
rem close_window() at +0.4s, which un-parents the docked Roblox window,
rem persists all-time stats, and closes the capture and log handles. This
rem used to force-kill immediately: measured at +0.28s, ahead of that 0.4s
rem timer every time, so on a frozen build none of that cleanup ever ran.
rem
rem A fixed grace period before the kill is not enough either. The real
rem build takes seconds to tear down (webview, capture and OCR handles, a
rem 90MB onefile's own exit), and any constant long enough for a slow
rem machine is dead time on a fast one. Polling costs nothing when the app
rem is already gone and adapts to whatever the machine actually needs.
rem
rem "ping" instead of "timeout" -- timeout needs a real console input
rem handle, which this .bat (launched detached, see launch_helper) doesn't
rem reliably have; same trick _write_source_helper_script already uses.
set _wait=0
:waitloop
ping -n 2 127.0.0.1 >nul
tasklist /FI "IMAGENAME eq {exe_name}" /NH 2>nul | findstr /i "{exe_name}" >nul
if errorlevel 1 goto closeditself
set /a _wait+=1
rem Bounded, not infinite -- a process that never actually dies (locked by
rem AV, a permission mismatch, a protected-process edge case, ...) used to
rem leave this waiting forever with the window just sitting there showing
rem nothing happening. After ~15s, force it and proceed anyway: a failed
rem move below at least surfaces a real error instead of hanging
rem indefinitely with no explanation.
if !_wait! lss 15 goto waitloop
echo Still running after ~15s -- forcing it closed and continuing anyway. >>%LOG%
taskkill /F /IM "{exe_name}" >>%LOG% 2>&1
ping -n 3 127.0.0.1 >nul
goto proceed
:closeditself
echo App closed itself cleanly. >>%LOG%
:proceed
echo [2/5] Old process confirmed gone (or timed out waiting). >>%LOG%
rem Wait mandatory cooldown for Windows file system and antivirus real-time scanner to release file handles.
ping -n 3 127.0.0.1 >nul
for /d %%i in ("%TEMP%\\_MEI*") do rd /s /q "%%i" >nul 2>&1
for /d %%i in ("%TEMP%\\onefile_*") do rd /s /q "%%i" >nul 2>&1
if exist "{old_exe}" del /f "{old_exe}" >>%LOG% 2>&1
echo [3/5] Moving current exe to "{old_exe}"... >>%LOG%
set _moveretries=0
:moveoldloop
move /y "{current_exe}" "{old_exe}" >>%LOG% 2>&1
if exist "{old_exe}" goto moveolddone
set /a _moveretries+=1
if !_moveretries! lss 15 (
    ping -n 2 127.0.0.1 >nul
    goto moveoldloop
)
:moveolddone
if not exist "{old_exe}" (
    echo [FAILED] Could not move the running exe aside after retries -- it may still be locked. >>%LOG%
    echo Update aborted. Attempting to relaunch original app... >>%LOG%
    cd /d "{exe_dir}"
    start "" "{current_exe}"
    goto :eof
)
echo [4/5] Moving downloaded update into place... >>%LOG%
set _movenewretries=0
:movenewloop
move /y "{new_exe_path}" "{current_exe}" >>%LOG% 2>&1
if exist "{current_exe}" goto movenewdone
set /a _movenewretries+=1
if !_movenewretries! lss 15 (
    ping -n 2 127.0.0.1 >nul
    goto movenewloop
)
:movenewdone
if not exist "{current_exe}" (
    echo [FAILED] Could not move the downloaded update into place after retries. >>%LOG%
    echo Restoring the previous exe from "{old_exe}" so the app still runs... >>%LOG%
    set _restoreretries=0
    :restoreloop
    move /y "{old_exe}" "{current_exe}" >>%LOG% 2>&1
    if exist "{current_exe}" goto restoredone
    set /a _restoreretries+=1
    if !_restoreretries! lss 10 (
        ping -n 2 127.0.0.1 >nul
        goto restoreloop
    )
    :restoredone
    cd /d "{exe_dir}"
    start "" "{current_exe}"
    echo Update failed -- reverted to the previous version and relaunched it. >>%LOG%
    goto :eof
)
echo [5/5] Relaunching... >>%LOG%
cd /d "{exe_dir}"
ping -n 2 127.0.0.1 >nul
start "" "{current_exe}"
del /f "{old_exe}" >>%LOG% 2>&1
echo Update finished successfully. >>%LOG%
del "%~f0"
"""
    with open(helper_path, "w", encoding="utf-8") as f:
        f.write(script)
    return helper_path


# ---------------------------------------------------------------------------

def relaunch_env() -> dict:
    """This process's environment with PyInstaller's private bootloader
    handshake removed, so a child can start a onefile exe from scratch.

    A onefile build runs twice: the bootloader extracts the bundle to
    %TEMP%\\_MEI<random>, sets

        _PYI_ARCHIVE_FILE        the exe the bundle came out of
        _PYI_APPLICATION_HOME_DIR   the _MEI dir it was extracted to
        _PYI_PARENT_PROCESS_LEVEL   how deep we are

    and re-runs itself. Our Python code is that second process, so those
    three are sitting in os.environ and every child we spawn inherits them.

    That is what broke Update & Restart. The helper is a child of the app,
    so `start "" "<exe>"` handed the fresh build an _PYI_ARCHIVE_FILE equal
    to its OWN path -- because the update swaps the new exe onto the old
    one's path. The bootloader reads that as "I am already extracted",
    skips extraction, and loads the interpreter out of
    _PYI_APPLICATION_HOME_DIR -- the previous process's _MEI dir, which
    step [2/5] of the helper deletes. Measured, driving a real frozen build
    through the real helper:

        [PYI-28940:ERROR] Failed to load Python DLL
            'C:\\Users\\...\\Temp\\_MEI302922\\python313.dll'.
        LoadLibrary: The specified module could not be found.

    The exe swap itself always worked; the app just never came back, with
    that error going to a console nobody sees. It also explains why running
    the same _update.bat by hand works -- Explorer starts it with a clean
    environment.

    The path match is the whole trigger, which is why this is specific to
    updating and not to launching in general: bootstrap.exe starting the
    app exe passes the same variables down and is fine, because
    _PYI_ARCHIVE_FILE names bootstrap.exe rather than the exe being
    started, and the bootloader extracts normally.

    Matched by prefix rather than by listing the three names: PyInstaller
    has renamed these before (5.x used a single _MEIPASS2, kept here for
    anyone still building with it), they are private to the bootloader, and
    a rename would bring this failure back silently.
    """
    return {k: v for k, v in os.environ.items()
            if not k.startswith("_PYI_") and k != "_MEIPASS2"}


def launch_helper(helper_path: str) -> None:
    if sys.platform == "darwin":
        # start_new_session detaches from this process's group, so the helper
        # survives the app exiting right after this call.
        subprocess.Popen(["/bin/bash", helper_path], start_new_session=True,
                         close_fds=True, env=relaunch_env())
        return
    # CREATE_NO_WINDOW, *not* DETACHED_PROCESS.
    #
    # Both hide the window and both outlive this process (which exits ~0.4s
    # after this call -- see main.Api.apply_update). The difference is that
    # DETACHED_PROCESS gives the helper no console AT ALL, and the helper is
    # a .bat: the first plain `echo` with no redirect writes to a stdout
    # handle that does not exist, cmd gives up there, and nothing after it
    # runs. That is one line into a five-step script -- before the exe is
    # ever swapped -- so the app just closed and never came back, the
    # downloaded "<exe>.update" stayed on disk, and relaunching still ran the
    # old build with no error anywhere except a one-line _update.log.
    #
    # Redirecting stdio to DEVNULL is not enough on its own; the taskkill/
    # tasklist steps want a console too. CREATE_NO_WINDOW gives the helper a
    # real (hidden) console, which is what the rest of this codebase already
    # uses for silent child processes -- see core/ocr.py and
    # core/tesseract_installer.py.
    subprocess.Popen(
        ["cmd.exe", "/c", helper_path],
        creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
        env=relaunch_env(),
    )
