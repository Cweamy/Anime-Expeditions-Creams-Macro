"""
Tiny bootstrapper for Cream's Macro | Anime Expeditions.

Downloads the real app exe from GitHub Releases on first run (or when a
newer version is out) and launches it. Built as its own separate, much
smaller exe (see build_bootstrap.py) -- the full app is 40+ MB because of
OpenCV/numpy/pywebview, which this script never imports, so the
bootstrapper itself ends up small enough to share directly (e.g. on
Discord) instead of the full exe.

    py -3.12 bootstrap.py
"""
import os
import sys
import ctypes
import subprocess
import requests

APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
GITHUB_REPO = "Cweamy/Anime-Expeditions-Creams-Macro"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"
API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
LOCAL_EXE = os.path.join(APP_DIR, "Cream's Macro - Anime Expeditions.exe")
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


def _find_exe_asset_url() -> str | None:
    try:
        resp = requests.get(API_URL, timeout=15)
        if resp.status_code != 200:
            return None
        for asset in resp.json().get("assets", []):
            name = asset.get("name", "")
            if name.lower().endswith(".exe") and "bootstrapper" not in name.lower():
                return asset["browser_download_url"]
    except Exception:
        pass
    return None


def _download(url: str) -> bool:
    tmp = LOCAL_EXE + ".download"
    try:
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
        os.replace(tmp, LOCAL_EXE)
        return True
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


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
    """Make sure the real exe is present and up to date. Returns True if
    it's ready to launch, False if there's nothing usable at all."""
    latest = _latest_tag()
    have_exe = os.path.exists(LOCAL_EXE)

    if have_exe and (not latest or latest == _local_version()):
        return True  # already up to date (or offline -- just use what we have)

    asset_url = _find_exe_asset_url()
    if not asset_url:
        return have_exe  # couldn't check/download -- fall back to cached copy if any

    ok = _download(asset_url)
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
