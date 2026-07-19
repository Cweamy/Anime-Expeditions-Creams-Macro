"""Central path resolution, so a frozen (Nuitka/PyInstaller) build resolves
its files correctly instead of every module independently deriving a path
off its own __file__ -- which breaks silently once frozen, since __file__
no longer sits inside the real project layout.

Two different roots, because they behave differently once frozen:

BUNDLE_DIR -- read-only bundled resources shipped with the app (ui/,
Assets/). Safe to read from wherever the frozen build unpacked itself to
(a onefile build's temp extraction dir, or right next to the exe for a
standalone build).

APP_DIR -- writable, user-owned data (settings.json, debug/, Paths/,
Templates/, debug.log, VERSION) that has to live beside the actual exe the
user downloaded, NOT wherever a onefile build happens to extract itself to
this run (that temp dir can differ, or get wiped, between runs) -- losing
settings.json every launch would make Settings pointless.

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

UI_DIR = os.path.join(BUNDLE_DIR, "ui")
ASSETS_DIR = os.path.join(BUNDLE_DIR, "Assets")
