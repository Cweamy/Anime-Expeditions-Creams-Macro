"""
Build the tiny bootstrapper exe with PyInstaller. It only imports
`requests` + stdlib (no OpenCV/numpy/pywebview/mss/keyboard), so it comes
out much smaller than the full app -- small enough to share as a single
file (e.g. on Discord). On first run it downloads the real exe from
GitHub Releases and launches it; see bootstrap.py.

Requires:
    py -3.12 -m pip install pyinstaller
    py -3.12 build_bootstrap.py

Output: dist/Cream's Macro - Anime Expeditions Bootstrapper.exe
"""
import subprocess
import sys
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
EXE_NAME = "Creams Macro - Anime Expeditions Bootstrapper"  # see build_pyinstaller.py's EXE_NAME comment

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--onefile",
    "--windowed",
    "--noconfirm",
    f"--name={EXE_NAME}",
    f"--icon={os.path.join(ROOT, 'logo.ico')}",
    "--distpath=dist",
    "--workpath=build",
    os.path.join(ROOT, "bootstrap.py"),
]

print("Building bootstrapper exe with PyInstaller...")
result = subprocess.run(cmd, cwd=ROOT)
if result.returncode != 0:
    print("\nBuild FAILED!")
    sys.exit(1)
print(f"\nDone! Check dist/{EXE_NAME}.exe")
