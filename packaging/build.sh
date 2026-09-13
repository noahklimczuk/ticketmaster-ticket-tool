#!/usr/bin/env sh
# Build a standalone ticketwatch binary for THIS platform (macOS or Linux).
# For a Windows .exe, run packaging/build_exe.bat on Windows, or let the
# build-exe GitHub Actions workflow do it.
set -e
cd "$(dirname "$0")/.."
python3 -m pip install --quiet --upgrade pyinstaller
python3 -m PyInstaller --clean --noconfirm \
    --distpath dist --workpath build/pyinstaller \
    packaging/ticketwatch.spec
echo
echo "Done: dist/ticketwatch"
echo "Run it to open the control panel."
