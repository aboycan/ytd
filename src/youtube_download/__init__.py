"""YouTube video download utility.

Public API::

    from youtube_download import YouTubeDownloader, AppConfig

    downloader = YouTubeDownloader(AppConfig())
    result = downloader.download("https://youtu.be/XXXXXXXXXXX", "1080p")
    print(result.path)
"""

import logging as _logging

__version__ = "1.2.1"

# Library best practice: stay silent unless the application configures logging.
_logging.getLogger(__name__).addHandler(_logging.NullHandler())

from .config import SUPPORTED_RESOLUTIONS, AppConfig, FallbackPolicy, load_config  # noqa: E402
from .downloader import (  # noqa: E402
    DownloadPlan,
    DownloadResult,
    ProgressEvent,
    YouTubeDownloader,
    YtDlpBackend,
)
from .exceptions import YouTubeDownloadError  # noqa: E402

__all__ = [
    "SUPPORTED_RESOLUTIONS",
    "AppConfig",
    "DownloadPlan",
    "DownloadResult",
    "FallbackPolicy",
    "ProgressEvent",
    "YouTubeDownloadError",
    "YouTubeDownloader",
    "YtDlpBackend",
    "__version__",
    "load_config",
]
