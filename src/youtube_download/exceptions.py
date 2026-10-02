"""Custom exception hierarchy for the YouTube download utility.

Every error raised deliberately by this package derives from
:class:`YouTubeDownloadError`. Each subclass carries a user-friendly message and
a process exit code so that the CLI (or any other front end) can report the
problem without inspecting library internals.
"""

from __future__ import annotations


class YouTubeDownloadError(Exception):
    """Base class for all errors raised by this package."""

    exit_code: int = 1
    #: Whether retrying the same operation might succeed (e.g. network blips).
    transient: bool = False

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message())

    @classmethod
    def default_message(cls) -> str:
        return "An unexpected error occurred while downloading the video."

    @property
    def message(self) -> str:
        return str(self)


class InvalidURLError(YouTubeDownloadError):
    exit_code = 2

    @classmethod
    def default_message(cls) -> str:
        return "Invalid YouTube URL."


class UnsupportedResolutionError(YouTubeDownloadError):
    exit_code = 2

    @classmethod
    def default_message(cls) -> str:
        return "The requested resolution is not supported."


class ConfigurationError(YouTubeDownloadError):
    exit_code = 2

    @classmethod
    def default_message(cls) -> str:
        return "The configuration is invalid."


class VideoUnavailableError(YouTubeDownloadError):
    exit_code = 3

    @classmethod
    def default_message(cls) -> str:
        return "The requested video is unavailable or cannot be accessed."


class PrivateVideoError(VideoUnavailableError):
    @classmethod
    def default_message(cls) -> str:
        return (
            "This video is private. It cannot be downloaded without "
            "appropriate access/authentication."
        )


class AgeRestrictedError(VideoUnavailableError):
    @classmethod
    def default_message(cls) -> str:
        return (
            "This video is age-restricted and requires a signed-in account "
            "that is allowed to view it. It cannot be downloaded anonymously."
        )


class AuthenticationRequiredError(VideoUnavailableError):
    @classmethod
    def default_message(cls) -> str:
        return (
            "YouTube requires sign-in to access this video (for example "
            "members-only content or a sign-in/bot check)."
        )


class ResolutionUnavailableError(YouTubeDownloadError):
    exit_code = 4

    def __init__(
        self,
        requested_label: str,
        max_available_height: int | None,
        message: str | None = None,
    ) -> None:
        self.requested_label = requested_label
        self.max_available_height = max_available_height
        if message is None:
            available = (
                f"{max_available_height}p" if max_available_height else "none"
            )
            message = (
                f"{requested_label} is not available for this video.\n"
                f"Available maximum resolution: {available}"
            )
        super().__init__(message)


class FFmpegNotFoundError(YouTubeDownloadError):
    exit_code = 5

    @classmethod
    def default_message(cls) -> str:
        return (
            "FFmpeg is required to merge the selected video and audio streams.\n"
            "Please install FFmpeg and try again (or pass --ffmpeg-path)."
        )


class NetworkError(YouTubeDownloadError):
    exit_code = 6
    transient = True

    @classmethod
    def default_message(cls) -> str:
        return "A network error occurred. Check your connection and try again."


class OutputPathError(YouTubeDownloadError):
    exit_code = 7

    @classmethod
    def default_message(cls) -> str:
        return "The output directory is invalid or not writable."


class InsufficientDiskSpaceError(OutputPathError):
    @classmethod
    def default_message(cls) -> str:
        return "There is not enough free disk space to save the video."


class DownloadFailedError(YouTubeDownloadError):
    exit_code = 8

    @classmethod
    def default_message(cls) -> str:
        return "The download failed."


class OutputValidationError(YouTubeDownloadError):
    exit_code = 9

    @classmethod
    def default_message(cls) -> str:
        return "The downloaded file failed validation."


class DependencyMissingError(YouTubeDownloadError):
    exit_code = 10

    @classmethod
    def default_message(cls) -> str:
        return (
            "The 'yt-dlp' package is not installed. "
            "Run: pip install -r requirements.txt"
        )
