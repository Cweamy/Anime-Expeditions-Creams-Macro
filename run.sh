#!/bin/bash
# macOS launcher -- run.bat's twin (see that file). From this folder:
#   chmod +x run.sh   (first time only)
#   ./run.sh
#
# First-run macOS checklist (the app logs these too, see
# core/window_mac.py):
#   System Settings > Privacy & Security > Accessibility   -> allow your
#     terminal (or the packaged app) -- window arranging + input need it.
#   System Settings > Privacy & Security > Screen Recording -> allow it
#     too, or every capture comes back black.
cd "$(dirname "$0")"
exec python3 main.py "$@"
