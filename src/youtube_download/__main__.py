"""Allow ``python -m youtube_download``."""

import sys

from .cli import main

sys.exit(main())
