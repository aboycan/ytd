#!/usr/bin/env python3
"""Command-line entry point for the YouTube download utility.

Usage:
    python youtube_downloader.py --url "<YOUTUBE_URL>" --resolution 1080p
    python youtube_downloader.py            # interactive mode

Run ``python youtube_downloader.py --help`` for all options.
"""

from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info < (3, 10):  # pragma: no cover - guard for old interpreters
    sys.exit("Python 3.10 or newer is required.")

# Make the package in ./src importable without installing it.
_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from youtube_download.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
