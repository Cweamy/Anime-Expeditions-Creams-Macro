"""One-click Tesseract OCR engine install via winget, for Settings' "Install
Tesseract" button -- so someone who hits TesseractNotAvailable doesn't have
to go find/download/run the UB-Mannheim installer by hand.

winget (not a hosted download URL) on purpose: UB-Mannheim's installer
filenames are versioned and change over time with no stable "latest"
download link to hardcode, whereas winget's package id stays constant and
it handles the download/verify/install itself. It also ships preinstalled
on Windows 10 1809+/Windows 11 as "App Installer", so this covers the vast
majority of users this macro already targets (see README's Windows 10/11
requirement) without bundling or downloading anything ourselves.
"""
import subprocess

WINGET_PACKAGE_ID = "UB-Mannheim.TesseractOCR"
INSTALL_TIMEOUT = 300.0  # winget downloads ~50MB -- generous for a slow connection


def install_tesseract(log=None) -> bool:
    """Blocking -- run this off the UI thread. Returns whether the install
    actually succeeded. `log`, if given, is called with progress/result
    strings (same convention as core.updater.check_for_update)."""
    log = log or (lambda msg: None)

    try:
        subprocess.run(
            ["winget", "--version"], capture_output=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        log("[Tesseract] winget isn't available on this system -- install manually from "
            "https://github.com/UB-Mannheim/tesseract/wiki instead.")
        return False

    log("[Tesseract] Installing via winget -- this can take a minute...")
    try:
        result = subprocess.run(
            ["winget", "install", "--id", WINGET_PACKAGE_ID, "-e", "--silent",
             "--accept-package-agreements", "--accept-source-agreements"],
            capture_output=True, text=True, timeout=INSTALL_TIMEOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        log(f"[Tesseract] Install timed out after {INSTALL_TIMEOUT:.0f}s.")
        return False
    except OSError as exc:
        log(f"[Tesseract] Couldn't launch winget: {exc}")
        return False

    output = (result.stdout or "").strip() or (result.stderr or "").strip()
    # winget returns 0 both for a fresh install AND for "already installed,
    # nothing to do" -- either way tesseract.exe should now be reachable,
    # so both count as success here.
    if result.returncode != 0:
        log(f"[Tesseract] winget install failed (exit {result.returncode}): {output or 'no output'}")
        return False

    log("[Tesseract] Installed successfully.")
    return True
