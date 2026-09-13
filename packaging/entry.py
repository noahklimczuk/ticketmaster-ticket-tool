"""Entry point for the packaged executable.

Double-clicking ticketwatch.exe should open the control panel, so a frozen
build with no arguments defaults to `gui` instead of `watch`.
"""

from __future__ import annotations

import sys

from ticketwatch.cli import main

if __name__ == "__main__":
    sys.exit(main())
