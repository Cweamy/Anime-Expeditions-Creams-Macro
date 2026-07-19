"""
Build the tiny bootstrapper exe with Nuitka. It only imports `requests` +
stdlib (no OpenCV/numpy/pywebview/mss/keyboard), so it comes out at a few
MB instead of the full app's 40+ MB -- small enough to share as a single
file (e.g. on Discord). On first run it downloads the real exe from
GitHub Releases and launches it; see bootstrap.py.

Requires a python.org CPython (NOT the Microsoft Store build -- Nuitka rejects it):
    py -3.12 -m pip install nuitka
    py -3.12 build_bootstrap.py

Output: dist-nuitka/Cream's Macro - Anime Expeditions Bootstrapper.exe
"""
import subprocess
import sys
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
EXE_NAME = "Cream's Macro - Anime Expeditions Bootstrapper.exe"

# Keep this build lean -- nothing here should ever need heavy stdlib bits.
NOFOLLOW = [
    "tkinter", "unittest", "pydoc", "doctest", "pdb", "test", "tests",
    "distutils", "setuptools", "pip", "pkg_resources", "lib2to3",
    "ensurepip", "venv", "sqlite3", "xmlrpc", "turtledemo",
]

cmd = [
    sys.executable, "-m", "nuitka",
    "--onefile",
    "--windows-console-mode=disable",
    f"--windows-icon-from-ico={os.path.join(ROOT, 'logo.ico')}",
    "--python-flag=no_site",
    "--python-flag=no_asserts",
    "--python-flag=no_docstrings",
    "--lto=no",
    f"--jobs={os.cpu_count() or 4}",
    "--assume-yes-for-downloads",
    f"--output-filename={EXE_NAME}",
    "--output-dir=dist-nuitka",
]

if os.environ.get("GITHUB_ACTIONS") == "true":
    cmd.append("--msvc=latest")

for mod in NOFOLLOW:
    cmd += [f"--nofollow-import-to={mod}"]

cmd.append(os.path.join(ROOT, "bootstrap.py"))

print("Building bootstrapper exe with Nuitka...")
result = subprocess.run(cmd, cwd=ROOT)
if result.returncode != 0:
    print("\nBuild FAILED!")
    sys.exit(1)
print(f"\nDone! Check dist-nuitka/{EXE_NAME}")
