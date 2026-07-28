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
import os
import shutil
import subprocess

WINGET_PACKAGE_ID = "UB-Mannheim.TesseractOCR"
INSTALL_TIMEOUT = 300.0  # winget downloads ~50MB -- generous for a slow connection

# winget returns 0x8A15002B or 0x8A15002C when the package is already
# installed at its latest version and no update is available. Both the
# unsigned and the sign-extended reading of each code are listed, since which
# one subprocess surfaces as returncode depends on how the exit status is
# interpreted. NOTE: 0x8A15002B and 2316632107 are the SAME number written two
# ways (likewise 0x8A15002C and 2316632084/0x8A150014 etc.).
_NO_UPDATE_APPLICABLE = {
    0x8A15002B, -1978335189, 2316632107,
    0x8A15002C, -1978335188, 2316632108,
    0x8A150014, -1978335212, 2316632084,
}


def install_tesseract(log=None) -> bool:
    """Blocking -- run this off the UI thread. Returns whether the install
    actually succeeded. `log`, if given, is called with progress/result
    strings (same convention as core.updater.check_for_update)."""
    log = log or (lambda msg: None)

    try:
        subprocess.run(
            ["winget", "--version"], capture_output=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
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
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        log(f"[Tesseract] Install timed out after {INSTALL_TIMEOUT:.0f}s.")
        return False
    except OSError as exc:
        log(f"[Tesseract] Couldn't launch winget: {exc}")
        return False

    output = (result.stdout or "").strip() or (result.stderr or "").strip()
    output_lower = output.lower()

    # winget returns 0 for a fresh install. 0x8A15002B / 0x8A15002C means "already
    # installed, no update available" -- tesseract.exe is already on disk,
    # so that counts as success too.
    if result.returncode == 0:
        log("[Tesseract] Installed successfully.")
        return True

    if result.returncode in _NO_UPDATE_APPLICABLE or any(phrase in output_lower for phrase in (
        "pacote existente", "already installed", "no available upgrade",
        "nenhuma atualização disponível", "no upgrade found", "ja instalado", "já instalado"
    )):
        log("[Tesseract] Already installed and up to date.")
        return True

    # Fallback check: if winget returned another code but Tesseract binary is already executable
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    fallback_paths = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.join(local_appdata, r"Programs\Tesseract-OCR\tesseract.exe"),
        os.path.join(local_appdata, r"Tesseract-OCR\tesseract.exe"),
        "tesseract",
    ]
    which_tess = shutil.which("tesseract")
    if which_tess:
        fallback_paths.insert(0, which_tess)

    for path in fallback_paths:
        if not path:
            continue
        try:
            res = subprocess.run(
                [path, "--version"], capture_output=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if res.returncode == 0:
                log("[Tesseract] Already installed and operational.")
                return True
        except Exception:
            pass

    log(f"[Tesseract] winget install failed (exit {result.returncode}): {output or 'no output'}")
    return False

