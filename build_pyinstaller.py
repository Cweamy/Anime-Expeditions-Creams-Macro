"""
Build the exe with PyInstaller -- bundles the interpreter + bytecode with no
compile step, so CI builds finish in well under a minute instead of
Nuitka's 10-20+ minute C compile. Trade-off: bundled Python bytecode is far
easier to decompile back to readable source than Nuitka's compiled machine
code -- an acceptable trade here since this whole repo is public source
anyway (nothing proprietary to protect by making it hard to reverse).

The very first packaged build of this app used PyInstaller and crashed on
launch ("Failed to resolve Python.Runtime.Loader") because PyInstaller's
own pywebview hook doesn't collect webview.platforms.win32, which
winforms.py imports unconditionally -- the exact same root cause Nuitka's
bundled plugin had (see the git history for build_nuitka.py, since
replaced by this file). Fixed here with explicit --hidden-import flags
instead of switching build tools blind a second time.

Requires:
    py -3.12 -m pip install pyinstaller
    py -3.12 build_pyinstaller.py

Output: dist/Cream's Macro - Anime Expeditions.exe
"""
import subprocess
import sys
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
# No apostrophe -- PyInstaller writes --name straight into an
# auto-generated .spec file as an unescaped Python string literal
# ("Cream's Macro..." breaks that file's own syntax). Nuitka took the name
# as a plain filename argument, so this never came up there.
EXE_NAME = "Creams Macro - Anime Expeditions"

# winforms.py imports win32 unconditionally even though edgechromium is the
# backend actually used at runtime -- PyInstaller's own pywebview hook
# misses this one, same bug Nuitka's bundled plugin had. The mac build
# needs the cocoa backend collected instead (PyInstaller only bundles the
# host platform's modules -- each OS builds its own binary).
if sys.platform == "darwin":
    HIDDEN_IMPORTS = ["webview.platforms.cocoa"]
else:
    HIDDEN_IMPORTS = [
        "webview.platforms.winforms",
        "webview.platforms.edgechromium",
        "webview.platforms.win32",
    ]

# Data PyInstaller wouldn't otherwise know to bundle -- extracted to
# sys._MEIPASS at runtime (see core/constants.py's BUNDLE_DIR, which reads
# sys._MEIPASS specifically for this).
#
# Assets/ is deliberately NOT in this list anymore: releases ship it as a
# loose folder in a zip beside the exe (see release.yml) so users can open,
# replace, and add reference images (Assets/ui/<name>/ variant folders --
# see core/vision.py and the Image Manager in Settings > Debug) without a
# rebuild. Bundling it too would mean two competing copies -- the ephemeral
# extracted one and the editable one -- which is exactly the confusion the
# old ASSETS_OVERRIDE_DIR scheme existed to paper over. core/constants.py's
# ASSETS_DIR points beside the exe (APP_DIR) accordingly.
ADD_DATA = [
    ("ui", "ui"),
    # Known-good default walk paths (see core/paths.py's DEFAULT_PATHS_DIR)
    # -- NOT the rest of Paths/, which is your own personal recordings and
    # never meant to ship.
    (os.path.join("Paths", "defaults"), os.path.join("Paths", "defaults")),
    ("logo.ico", "."),
    ("VERSION", "."),
]

def _mac_icns_path():
    """The .icns for the mac .app bundle -- macOS won't take the .ico
    (that's a Windows format; passing it straight through is what failed
    the first mac CI build), so one is converted from logo.ico at build
    time with Pillow. A hand-made logo.icns in the repo root wins if one
    ever gets added; the converted one lands in build/ (gitignored)
    because it's a derived artifact, not a source file.

    logo.ico's largest frame is only 64x64, so the 512px base this writes
    is a LANCZOS upscale -- soft at full Finder-preview size, but the Dock
    and title bars render it at ~16-128px where it looks right. Pillow's
    ICNS writer generates all the standard smaller sizes from the base
    image on its own, so one save covers the whole set.

    Returns None (build keeps PyInstaller's default icon, exactly the old
    behavior) rather than failing the build if Pillow's missing or the
    conversion chokes -- an icon is never worth a broken release."""
    hand_made = os.path.join(ROOT, "logo.icns")
    if os.path.exists(hand_made):
        return hand_made
    out = os.path.join(ROOT, "build", "logo.icns")
    try:
        from PIL import Image
        icon = Image.open(os.path.join(ROOT, "logo.ico"))  # .ico opens at its largest frame
        icon = icon.convert("RGBA").resize((512, 512), Image.LANCZOS)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        icon.save(out, format="ICNS")
        print(f"Converted logo.ico -> {out} for the .app icon.")
        return out
    except ImportError:
        print("Pillow isn't installed -- building with PyInstaller's default icon. "
              "`pip install pillow` to get the custom one.")
    except Exception as exc:
        print(f"Couldn't convert logo.ico to .icns ({exc}) -- building with PyInstaller's default icon.")
    return None


cmd = [
    sys.executable, "-m", "PyInstaller",
    "--onefile",
    "--windowed",  # no console window (macOS: also produces the .app bundle)
    "--noconfirm",
    f"--name={EXE_NAME}",
    "--distpath=dist",
    "--workpath=build",
]
if sys.platform == "darwin":
    icns = _mac_icns_path()
    if icns:
        cmd.append(f"--icon={icns}")
else:
    cmd.append(f"--icon={os.path.join(ROOT, 'logo.ico')}")
for mod in HIDDEN_IMPORTS:
    cmd += [f"--hidden-import={mod}"]
# winsdk (Windows OCR) is a namespace-package WinRT projection PyInstaller's
# static analysis can't follow -- the winsdk.windows.* submodules are only
# imported lazily inside core/ocr_windows.py, so they must be collected
# explicitly or Windows OCR silently falls back to Tesseract in the build.
if sys.platform != "darwin":
    cmd += ["--collect-submodules=winsdk"]
for src, dest in ADD_DATA:
    # --add-data's separator is ';' on Windows but ':' on POSIX -- exactly
    # what os.pathsep is. Hardcoded ';' was the mac CI build's first
    # failure ("Wrong syntax, should be --add-data=SOURCE:DEST").
    cmd += [f"--add-data={os.path.join(ROOT, src)}{os.pathsep}{dest}"]
cmd.append(os.path.join(ROOT, "main.py"))

print("Building exe with PyInstaller...")
result = subprocess.run(cmd, cwd=ROOT)
if result.returncode != 0:
    print("\nBuild FAILED!")
    sys.exit(1)
print(f"\nDone! Check dist/{EXE_NAME}.exe")
