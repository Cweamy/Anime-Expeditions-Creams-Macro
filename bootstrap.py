"""
Tiny bootstrapper for Cream's Macro | Anime Expeditions.

Downloads the real app from GitHub Releases on first run (or when a newer
version is out) and launches it. Built as its own separate, much smaller
exe (see build_bootstrap.py) -- the full app is 40+ MB because of
OpenCV/numpy/pywebview, which this script never imports, so the
bootstrapper itself ends up small enough to share directly (e.g. on
Discord) instead of the full download.

Downloads the release ZIP (exe + the loose, user-editable Assets/ folder
side by side -- see release.yml's packaging step), not just the exe:
Assets stopped being bundled inside the exe (so users can open/replace/add
the macro's reference images without a rebuild -- see core/constants.py's
ASSETS_DIR), which means a bare exe alone can't find any of its reference
images. The exe is always replaced on update; Assets files are extracted
ADD-ONLY (never overwriting one already on disk) so an update can't wipe
out images the user has replaced or added -- same policy core/updater.py's
merge_assets_update applies for in-app updates.

    py -3.12 bootstrap.py
"""
import os
import sys
import ctypes
import subprocess
import zipfile
import requests

APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
GITHUB_REPO = "Cweamy/Anime-Expeditions-Creams-Macro"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"
API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
# Must match release.yml's packaged Windows zip name exactly (dashes on
# purpose -- GitHub rewrites spaces in asset filenames to dots, dashes
# stay put). The bootstrapper is Windows-only, so always the -Windows zip.
ZIP_ASSET_NAME = "Creams-Macro-Anime-Expeditions-Windows.zip"
LOCAL_EXE = os.path.join(APP_DIR, "Creams Macro - Anime Expeditions.exe")
LOCAL_ZIP = os.path.join(APP_DIR, ".bootstrap_download.zip")
VERSION_FILE = os.path.join(APP_DIR, ".bootstrap_version")

MB_OK = 0x40
MB_ERROR = 0x10


def _msg(text: str, icon: int = MB_OK):
    try:
        ctypes.windll.user32.MessageBoxW(0, text, "Cream's Macro", icon)
    except Exception:
        pass


def _latest_tag() -> str | None:
    """Same trick core/updater.py uses: the plain github.com releases page
    redirects to the tagged release, which tells us the latest version
    without touching the rate-limited api.github.com endpoint."""
    try:
        resp = requests.head(RELEASES_PAGE, allow_redirects=False, timeout=10)
        location = resp.headers.get("Location", "")
        if "/releases/tag/" in location:
            return location.rsplit("/releases/tag/", 1)[-1]
    except Exception:
        pass
    return None


def _find_zip_asset_url() -> str | None:
    """The packaged release zip's download URL (exe + Assets/, see module
    docstring). Falls back to a constructed /releases/latest/download/ link
    if the API call fails/rate-limits -- the asset name is fixed by
    release.yml, so the constructed URL is just as good when the API isn't."""
    try:
        resp = requests.get(API_URL, timeout=15)
        if resp.status_code == 200:
            for asset in resp.json().get("assets", []):
                if asset.get("name", "").lower() == ZIP_ASSET_NAME.lower():
                    return asset["browser_download_url"]
    except Exception:
        pass
    return f"https://github.com/{GITHUB_REPO}/releases/latest/download/{ZIP_ASSET_NAME}"


def _download_and_extract(url: str) -> bool:
    """Downloads the release zip and lays it out beside this bootstrapper:
    the exe always overwritten (that's the update), Assets files add-only
    (never clobbering an image the user has replaced/added -- see module
    docstring). The zip is fetched to a temp name first so a half-finished
    download can never masquerade as a good archive on the next run."""
    try:
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(LOCAL_ZIP, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
        with zipfile.ZipFile(LOCAL_ZIP) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                # Normalize + sanity-check each entry path: zip filenames are
                # untrusted input, so anything absolute or dotted-out of the
                # install folder is skipped outright.
                parts = info.filename.replace("\\", "/").split("/")
                if not parts or any(p in ("", ".", "..") for p in parts) or ":" in parts[0]:
                    continue
                dest = os.path.join(APP_DIR, *parts)
                is_asset = parts[0].lower() == "assets"
                if is_asset and os.path.exists(dest):
                    continue  # user's own/edited reference image -- keep it
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(info) as src, open(dest, "wb") as out:
                    out.write(src.read())
        return os.path.exists(LOCAL_EXE)
    except Exception:
        return False
    finally:
        try:
            os.remove(LOCAL_ZIP)
        except OSError:
            pass


def _local_version() -> str:
    try:
        with open(VERSION_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _save_local_version(tag: str):
    try:
        with open(VERSION_FILE, "w", encoding="utf-8") as f:
            f.write(tag)
    except OSError:
        pass


def ensure_app() -> bool:
    """Make sure the real exe (and its Assets folder) is present and up to
    date. Returns True if it's ready to launch, False if there's nothing
    usable at all."""
    latest = _latest_tag()
    have_exe = os.path.exists(LOCAL_EXE)
    # The Assets check matters for installs made by an OLD bootstrapper
    # (which only ever downloaded the bare exe): same tag, but no Assets
    # folder on disk -- re-extracting the zip fills it in without touching
    # the exe's version bookkeeping.
    have_assets = os.path.isdir(os.path.join(APP_DIR, "Assets", "ui"))

    if have_exe and have_assets and (not latest or latest == _local_version()):
        return True  # already up to date (or offline -- just use what we have)

    zip_url = _find_zip_asset_url()
    ok = _download_and_extract(zip_url)
    if ok and latest:
        _save_local_version(latest)
    return ok or have_exe


def main():
    if not ensure_app():
        _msg(
            "Couldn't download Cream's Macro. Check your internet connection "
            "and try again.",
            MB_ERROR,
        )
        sys.exit(1)

    subprocess.Popen([LOCAL_EXE], cwd=APP_DIR)


if __name__ == "__main__":
    main()
