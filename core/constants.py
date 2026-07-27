"""Central path resolution, so a frozen (Nuitka/PyInstaller) build resolves
its files correctly instead of every module independently deriving a path
off its own __file__ -- which breaks silently once frozen, since __file__
no longer sits inside the real project layout.

Two different roots, because they behave differently once frozen:

BUNDLE_DIR -- read-only bundled resources shipped inside the app (ui/,
Paths/defaults/, VERSION). Safe to read from wherever the frozen build
unpacked itself to (a onefile build's temp extraction dir, or right next to
the exe for a standalone build).

APP_DIR -- writable, user-owned data (settings.json, debug/, Paths/,
Templates/, debug.log, and -- since Assets stopped being bundled, see
ASSETS_DIR below -- Assets/) that has to live beside the actual exe the
user downloaded, NOT wherever a onefile build happens to extract itself to
this run (that temp dir can differ, or get wiped, between runs) -- losing
settings.json every launch would make Settings pointless.

On macOS, "beside the exe" would mean inside the .app bundle
(<App>.app/Contents/MacOS), which is wrong on two counts -- see the
darwin override below -- so there APP_DIR is redirected to
~/Library/Application Support instead.

See build_nuitka.py's comment on the pywebview/pythonnet crash this (and
the correct webview backend --include-module flags) were needed to fix --
this file existing at all is a direct consequence of that: every core/*.py
module used to compute os.path.dirname(os.path.dirname(os.path.abspath(
__file__))) itself, which is correct un-frozen but wrong once bundled.
"""
import os
import sys

IS_FROZEN = hasattr(sys, "_MEIPASS") or getattr(sys, "frozen", False) or "__compiled__" in dir()

if hasattr(sys, "_MEIPASS"):
    # PyInstaller onefile
    BUNDLE_DIR = sys._MEIPASS
    APP_DIR = os.path.dirname(sys.executable)
elif getattr(sys, "frozen", False) or "__compiled__" in dir():
    # Nuitka standalone/onefile
    BUNDLE_DIR = os.path.dirname(sys.executable)
    APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
else:
    BUNDLE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    APP_DIR = BUNDLE_DIR

# macOS: a frozen build's APP_DIR (above) lands INSIDE the .app bundle
# (<App>.app/Contents/MacOS) -- the wrong home for user data on two counts.
# 1. Gatekeeper App Translocation runs a freshly-downloaded, still-quarantined
#    .app from a randomized READ-ONLY mount (/private/var/folders/.../
#    AppTranslocation/.../d/<App>.app), so every write beside the binary dies
#    with [Errno 30] Read-only file system -- which is exactly why the Assets
#    download (and settings saves) were failing at launch.
# 2. Even un-translocated, writing user data inside the bundle breaks its code
#    signature and gets wiped every time the .app is replaced on self-update.
# ~/Library/Application Support/<app> is the macOS-standard home for this:
# writable, persistent across updates, and immune to translocation.
if IS_FROZEN and sys.platform == "darwin":
    APP_DIR = os.path.join(
        os.path.expanduser("~/Library/Application Support"),
        "Creams Macro - Anime Expeditions",
    )
    try:
        os.makedirs(APP_DIR, exist_ok=True)
    except OSError:
        pass

UI_DIR = os.path.join(BUNDLE_DIR, "ui")

# APP_DIR, not BUNDLE_DIR, ON PURPOSE: Assets/ is deliberately NOT bundled
# into the exe anymore (see build_pyinstaller.py -- releases ship a zip with
# the exe and a loose Assets/ folder side by side). The whole point is that
# every reference image the macro searches for is a plain .png the user can
# open, replace, or add variants to (see core.vision.template_variant_paths
# and the Image Manager in Settings > Debug) -- which only works if the
# folder lives persistently beside the exe, not inside a onefile build's
# ephemeral temp extraction that gets re-created fresh every launch.
#
# This replaced the old two-tier scheme (bundled ASSETS_DIR + a separate
# ASSETS_OVERRIDE_DIR beside the exe that won lookups): with nothing bundled
# there's exactly one Assets location and editing it directly IS the way to
# customize, no override indirection needed. If the folder is missing at
# launch (someone downloaded/kept only the bare exe), core.updater.
# ensure_assets_present fetches the current release zip's Assets to restore
# it rather than leaving every image search dead.
ASSETS_DIR = os.path.join(APP_DIR, "Assets")

# Kept as an alias for the handful of callers (Settings' "Open Assets
# Folder", etc.) written against the old override-dir name -- same folder
# now, see above.
ASSETS_OVERRIDE_DIR = ASSETS_DIR

# Fixed spot (relative to the docked game window's top-left) where the
# Gained Rewards panel's scrollbar track renders -- shared by main.py's
# on-demand read and core.runner's automatic post-match read.  Defined
# here (not in core.rewards) to avoid forcing an eager cv2/numpy import
# in every consumer.
REWARD_SCROLLBAR_PROBE = (710, 428, 4, 2)   # (x, y, width, height)
REWARD_SCROLLBAR_COLOR = 0x373737

