#!/usr/bin/env sh
# Build HackCPUSim — a single-file windowed executable for the current OS.
#
#   ./build_exe.sh              -> build only
#   ./build_exe.sh --console    -> keep a console window (for debugging)
#
# Requires: pip install pyinstaller

set -e
cd "$(dirname "$0")"

EXTRA=""
if [ "$1" = "--console" ]; then
  EXTRA="--console"
fi

python -m PyInstaller \
  --onefile \
  --windowed \
  --name HackCPUSim \
  $EXTRA \
  main.py

echo
echo "Done. Executable: dist/HackCPUSim(.exe)"
echo "(No arguments: launches the GUI with the built-in demo.)"
