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
# misses this one, same bug Nuitka's bundled plugin had.
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

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--onefile",
    "--windowed",  # no console window
    "--noconfirm",
    f"--name={EXE_NAME}",
    f"--icon={os.path.join(ROOT, 'logo.ico')}",
    "--distpath=dist",
    "--workpath=build",
]
for mod in HIDDEN_IMPORTS:
    cmd += [f"--hidden-import={mod}"]
for src, dest in ADD_DATA:
    cmd += [f"--add-data={os.path.join(ROOT, src)};{dest}"]
cmd.append(os.path.join(ROOT, "main.py"))

print("Building exe with PyInstaller...")
result = subprocess.run(cmd, cwd=ROOT)
if result.returncode != 0:
    print("\nBuild FAILED!")
    sys.exit(1)
print(f"\nDone! Check dist/{EXE_NAME}.exe")
