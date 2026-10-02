"""Download orchestration and the replaceable download backend.

Architecture::

    YouTubeDownloader                      (this module, backend-agnostic)
        ├── url_validator.validate_youtube_url
        ├── DownloaderBackend.extract_info  → metadata.VideoMetadata
        ├── format_selector.select_formats
        ├── FFmpegManager.require_ffmpeg    (only when streams must be merged)
        ├── output directory / disk-space checks, safe filename
        ├── DownloaderBackend.download      (download + merge)
        └── output validation via ffprobe

Only :class:`YtDlpBackend` knows about yt-dlp. Another library can be used by
implementing the small :class:`DownloaderBackend` protocol.
"""

from __future__ import annotations

import errno
import logging
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, TypeVar

from .config import (
    AUDIO_BITRATE_KBPS,
    AUDIO_OUTPUT_CODEC,
    DEFAULT_MIX_LIMIT,
    AppConfig,
    MediaType,
    normalize_resolution_label,
    resolution_to_height,
)
from .exceptions import (
    AgeRestrictedError,
    AuthenticationRequiredError,
    DependencyMissingError,
    DownloadFailedError,
    InsufficientDiskSpaceError,
    NetworkError,
    OutputPathError,
    OutputValidationError,
    PrivateVideoError,
    VideoUnavailableError,
    YouTubeDownloadError,
)
from .ffmpeg_manager import FFmpegManager, MediaInfo
from .filename_utils import build_output_path, ensure_within_directory
from .format_selector import AudioSelection, FormatSelection, select_audio_only, select_formats
from .metadata import VideoMetadata
from .filename_utils import sanitize_filename
from .url_validator import (
    VIDEO_ID_PATTERN,
    ValidatedPlaylist,
    ValidatedURL,
    validate_playlist_url,
    validate_youtube_url,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


# --------------------------------------------------------------------------- #
# Progress reporting
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ProgressEvent:
    """A progress update emitted during a download.

    ``stage`` is one of ``"downloading"``, ``"finished"`` (one stream done),
    ``"merging"``/``"merged"`` or ``"converting"``/``"converted"``. ``stream`` is ``"video"``, ``"audio"`` or
    ``"video+audio"`` when known.
    """

    stage: str
    stream: str | None = None
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    speed_bytes_per_second: float | None = None
    eta_seconds: int | None = None

    @property
    def fraction(self) -> float | None:
        if self.downloaded_bytes is None or not self.total_bytes:
            return None
        return max(0.0, min(1.0, self.downloaded_bytes / self.total_bytes))


ProgressCallback = Callable[[ProgressEvent], None]


# --------------------------------------------------------------------------- #
# Backend abstraction
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class BackendDownloadRequest:
    """Everything a backend needs to download one selection."""

    url: str
    format_spec: str
    output_path: Path  # final file path, including extension
    merge_container: str | None  # set when streams must be merged
    ffmpeg_location: str | None
    stream_names: Mapping[str, str]  # format_id -> "video"/"audio"/"video+audio"


class DownloaderBackend(Protocol):
    """Minimal interface a download library adapter must implement."""

    def extract_info(self, url: str) -> Mapping[str, Any]:
        """Return raw metadata (id, title, uploader, duration, formats...)."""

    def download(
        self, request: BackendDownloadRequest, progress: ProgressCallback | None
    ) -> Path:
        """Download (and merge) ``request`` and return the final file path."""

    def extract_playlist(self, url: str, limit: int | None) -> Mapping[str, Any]:
        """Return the playlist title and its entries (id + title), without media."""


# Substrings of backend error messages mapped to user-facing exceptions.
# Order matters: the first match wins.
_ERROR_PATTERNS: tuple[tuple[tuple[str, ...], type[YouTubeDownloadError]], ...] = (
    (("private video",), PrivateVideoError),
    (("confirm your age", "age-restricted", "age restricted",
      "inappropriate for some users"), AgeRestrictedError),
    (("members-only", "join this channel", "sign in to confirm you",
      "requires authentication", "login required", "use --cookies"),
     AuthenticationRequiredError),
    (("video unavailable", "this video is not available", "has been removed",
      "no longer available", "does not exist", "http error 404",
      "not available in your country", "premieres in", "this live event"),
     VideoUnavailableError),
    (("no space left",), InsufficientDiskSpaceError),
    (("permission denied", "read-only file system"), OutputPathError),
    (("unable to download webpage", "timed out", "timeout", "connection",
      "temporary failure", "name or service not known", "network is unreachable",
      "http error 5", "http error 429", "incompleteread", "ssl:",
      "certificate verify"), NetworkError),
)


def map_backend_error(message: str) -> YouTubeDownloadError:
    """Translate a backend error message into a package exception."""
    lowered = (message or "").lower()
    for needles, exc_type in _ERROR_PATTERNS:
        if any(needle in lowered for needle in needles):
            if exc_type is NetworkError:
                return NetworkError(f"A network error occurred: {_short(message)}")
            if exc_type in (OutputPathError,):
                return OutputPathError(f"Cannot write the output file: {_short(message)}")
            return exc_type()
    return DownloadFailedError(f"The download failed: {_short(message)}")


def _short(message: str, limit: int = 300) -> str:
    text = " ".join((message or "").replace("ERROR:", "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


class _YtDlpLogger:
    """Routes yt-dlp's own messages into :mod:`logging` (at DEBUG/WARNING)."""

    def __init__(self) -> None:
        self._log = logging.getLogger(f"{__name__}.yt_dlp")

    def debug(self, msg: str) -> None:
        self._log.debug(msg.removeprefix("[debug] "))

    def info(self, msg: str) -> None:
        self._log.debug(msg)

    def warning(self, msg: str) -> None:
        self._log.warning(msg)

    def error(self, msg: str) -> None:
        # Errors are re-raised as exceptions and reported by the caller.
        self._log.debug(msg)


class YtDlpBackend:
    """:class:`DownloaderBackend` implementation using the yt-dlp library."""

    def __init__(self, retries: int = 3, socket_timeout: float = 30.0) -> None:
        self._retries = retries
        self._socket_timeout = socket_timeout

    @staticmethod
    def _module() -> Any:
        try:
            import yt_dlp  # noqa: PLC0415 - optional import, checked at runtime
        except ImportError as exc:
            raise DependencyMissingError() from exc
        return yt_dlp

    def _base_options(self) -> dict[str, Any]:
        return {
            "quiet": True,
            "no_warnings": False,
            "noprogress": True,
            "noplaylist": True,
            "logger": _YtDlpLogger(),
            "socket_timeout": self._socket_timeout,
            "extractor_retries": self._retries,
            "retries": self._retries,
            "fragment_retries": self._retries,
        }

    def _call(self, func: Callable[[], T]) -> T:
        yt_dlp = self._module()
        try:
            return func()
        except yt_dlp.utils.DownloadError as exc:
            raise map_backend_error(str(exc)) from exc
        except yt_dlp.utils.YoutubeDLError as exc:
            raise map_backend_error(str(exc)) from exc
        except OSError as exc:
            raise _map_os_error(exc) from exc

    def extract_info(self, url: str) -> Mapping[str, Any]:
        yt_dlp = self._module()
        options = {**self._base_options(), "skip_download": True}

        def run() -> Mapping[str, Any]:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
                return ydl.sanitize_info(info) if info else {}

        info = self._call(run)
        if not info:
            raise VideoUnavailableError()
        if info.get("_type") == "playlist":
            raise DownloadFailedError(
                "This link is a playlist; add --list to download the playlist."
            )
        return info

    def extract_playlist(self, url: str, limit: int | None) -> Mapping[str, Any]:
        yt_dlp = self._module()
        options: dict[str, Any] = {
            **self._base_options(),
            "skip_download": True,
            "noplaylist": False,
            "extract_flat": "in_playlist",  # list entries only; no per-video requests
        }
        if limit:
            options["playlistend"] = int(limit)

        def run() -> Mapping[str, Any]:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
                return ydl.sanitize_info(info) if info else {}

        info = self._call(run)
        if not info or info.get("_type") != "playlist":
            raise VideoUnavailableError("The playlist is unavailable or empty.")
        return info

    def download(
        self, request: BackendDownloadRequest, progress: ProgressCallback | None
    ) -> Path:
        yt_dlp = self._module()
        final_paths: list[str] = []
        stem = str(request.output_path.with_suffix(""))
        # '%' starts a yt-dlp template field; escape it in the literal path.
        template = stem.replace("%", "%%") + ".%(ext)s"

        def on_progress(data: Mapping[str, Any]) -> None:
            if progress is None:
                return
            info = data.get("info_dict") or {}
            stream = request.stream_names.get(str(info.get("format_id")))
            status = data.get("status")
            if status in {"downloading", "finished"}:
                progress(ProgressEvent(
                    stage=status,
                    stream=stream,
                    downloaded_bytes=data.get("downloaded_bytes"),
                    total_bytes=data.get("total_bytes") or data.get("total_bytes_estimate"),
                    speed_bytes_per_second=data.get("speed"),
                    eta_seconds=data.get("eta"),
                ))

        def on_postprocess(data: Mapping[str, Any]) -> None:
            status = data.get("status")
            if progress is None or status not in {"started", "finished"}:
                return
            if data.get("postprocessor") == "Merger":
                stage = "merging" if status == "started" else "merged"
                progress(ProgressEvent(stage=stage, stream="video+audio"))

        options: dict[str, Any] = {
            **self._base_options(),
            "format": request.format_spec,
            "outtmpl": {"default": template},
            "overwrites": False,
            "continuedl": True,
            "keepvideo": False,
            "progress_hooks": [on_progress],
            "postprocessor_hooks": [on_postprocess],
            "post_hooks": [final_paths.append],
        }
        if request.merge_container:
            options["merge_output_format"] = request.merge_container
        if request.ffmpeg_location:
            options["ffmpeg_location"] = request.ffmpeg_location

        def run() -> int:
            with yt_dlp.YoutubeDL(options) as ydl:
                return ydl.download([request.url])

        return_code = self._call(run)
        if return_code:
            raise DownloadFailedError(f"The downloader reported failure (code {return_code}).")
        return Path(final_paths[-1]) if final_paths else request.output_path


def _map_os_error(exc: OSError) -> YouTubeDownloadError:
    if exc.errno == errno.ENOSPC:
        return InsufficientDiskSpaceError()
    if exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
        return OutputPathError(f"Permission denied while writing: {exc.filename or exc}")
    return DownloadFailedError(f"The download failed: {exc}")


# --------------------------------------------------------------------------- #
# Output directory helpers
# --------------------------------------------------------------------------- #
def prepare_output_directory(directory: Path) -> Path:
    """Create ``directory`` if needed and verify it is writable."""
    directory = Path(directory).expanduser()
    if directory.exists() and not directory.is_dir():
        raise OutputPathError(f"Output path is not a directory: {directory}")
    try:
        directory.mkdir(parents=True, exist_ok=True)
        # Creating a real file is the only reliable cross-platform write test.
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".write-test-"):
            pass
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise InsufficientDiskSpaceError() from exc
        raise OutputPathError(
            f"The output directory is invalid or not writable: {directory} ({exc.strerror or exc})"
        ) from exc
    resolved = directory.resolve()
    logger.info("Output directory ready: %s", resolved)
    return resolved


def check_disk_space(directory: Path, required_bytes: int | None, margin: float) -> None:
    """Raise if the estimated download size does not fit on the disk."""
    if not required_bytes:
        logger.debug("File size unknown; skipping disk space check")
        return
    free = shutil.disk_usage(directory).free
    needed = int(required_bytes * (1.0 + margin))
    if needed > free:
        raise InsufficientDiskSpaceError(
            "There is not enough free disk space to save the video "
            f"(needs about {needed // (1024 * 1024)} MiB, "
            f"{free // (1024 * 1024)} MiB available)."
        )


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DownloadPlan:
    """What will be downloaded, determined before any media is transferred."""

    url: ValidatedURL
    metadata: VideoMetadata
    requested_label: str
    selection: FormatSelection | AudioSelection
    output_dir: Path

    @property
    def media_type(self) -> MediaType:
        return MediaType.AUDIO if self.selection.is_audio_only else MediaType.VIDEO

    @property
    def requires_ffmpeg(self) -> bool:
        return self.selection.requires_ffmpeg


@dataclass(frozen=True)
class DownloadResult:
    """Outcome of a completed download."""

    path: Path
    plan: DownloadPlan
    media_info: MediaInfo | None
    #: True when the file already existed (playlist re-run) and nothing was downloaded.
    skipped: bool = False

    @property
    def size_bytes(self) -> int:
        return self.path.stat().st_size


_UNAVAILABLE_TITLES = {"[private video]", "[deleted video]", "[unavailable video]"}


@dataclass(frozen=True)
class PlaylistEntry:
    """One video of a playlist."""

    index: int  # 1-based position in the (possibly limited) list
    video_id: str
    title: str | None

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


@dataclass(frozen=True)
class PlaylistInfo:
    """A playlist resolved to its downloadable entries."""

    playlist: ValidatedPlaylist
    title: str
    entries: tuple[PlaylistEntry, ...]
    unavailable: int  # private/deleted entries that were left out
    limit: int | None

    @property
    def folder_name(self) -> str:
        return sanitize_filename(self.title, max_bytes=120)

    @property
    def index_width(self) -> int:
        return max(2, len(str(len(self.entries))))

    def name_prefix(self, entry: PlaylistEntry) -> str:
        """``"03 - "``: keeps files in playlist order in any file browser."""
        return f"{entry.index:0{self.index_width}d} - "


class YouTubeDownloader:
    """High-level API used by the CLI (and future GUI/REST front ends)."""

    def __init__(
        self,
        config: AppConfig | None = None,
        backend: DownloaderBackend | None = None,
        ffmpeg: FFmpegManager | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config or AppConfig()
        self.backend = backend or YtDlpBackend(
            retries=self.config.retries, socket_timeout=self.config.socket_timeout_seconds
        )
        self.ffmpeg = ffmpeg or FFmpegManager(self.config.ffmpeg_path)
        self._sleep = sleep

    # ----------------------------------------------------------- public API
    def fetch_metadata(self, url: str) -> tuple[ValidatedURL, VideoMetadata]:
        """Validate ``url`` and retrieve its metadata (no media download)."""
        validated = validate_youtube_url(url)
        logger.info("Retrieving metadata (video_id=%s)", validated.video_id)
        info = self._with_retries(
            "metadata retrieval", lambda: self.backend.extract_info(validated.canonical_url)
        )
        metadata = VideoMetadata.from_info(info)
        if metadata.is_live:
            raise DownloadFailedError("Live streams in progress are not supported.")
        logger.info(
            "Metadata retrieved (video_id=%s, duration=%s, formats=%d, max=%sp)",
            metadata.video_id, metadata.duration_seconds, len(metadata.formats), metadata.max_height,
        )
        return validated, metadata

    def fetch_playlist(self, url: str, limit: int | None = None) -> PlaylistInfo:
        """Resolve a playlist link to its entries (no media is downloaded).

        YouTube Mixes (``list=RD...``) never end, so they are capped at
        ``DEFAULT_MIX_LIMIT`` items unless ``limit`` is given.
        """
        playlist = validate_playlist_url(url)
        if limit is not None and limit < 1:
            raise DownloadFailedError("--limit must be at least 1.")
        effective_limit = limit or (DEFAULT_MIX_LIMIT if playlist.is_mix else None)
        logger.info("Retrieving playlist (id=%s, limit=%s)", playlist.playlist_id, effective_limit)
        info = self._with_retries(
            "playlist retrieval",
            lambda: self.backend.extract_playlist(playlist.canonical_url, effective_limit),
        )
        entries: list[PlaylistEntry] = []
        unavailable = 0
        seen: set[str] = set()
        for raw in info.get("entries") or []:
            if not raw:
                unavailable += 1
                continue
            video_id = str(raw.get("id") or "")
            title = raw.get("title")
            if (not VIDEO_ID_PATTERN.match(video_id)
                    or str(title or "").strip().lower() in _UNAVAILABLE_TITLES):
                unavailable += 1
                continue
            if video_id in seen:  # Mixes can repeat a video
                continue
            seen.add(video_id)
            entries.append(PlaylistEntry(len(entries) + 1, video_id, title))
            if effective_limit and len(entries) >= effective_limit:
                break
        if not entries:
            raise VideoUnavailableError("The playlist has no downloadable videos.")
        title = str(info.get("title") or f"Playlist {playlist.playlist_id}")
        logger.info("Playlist retrieved (id=%s, items=%d, unavailable=%d)",
                    playlist.playlist_id, len(entries), unavailable)
        return PlaylistInfo(playlist, title, tuple(entries), unavailable, effective_limit)

    def plan(
        self,
        url: str,
        resolution: str | None = None,
        media_type: MediaType | str = MediaType.VIDEO,
        output_dir: Path | None = None,
    ) -> DownloadPlan:
        """Validate, fetch metadata and choose formats without downloading.

        ``media_type="audio"`` plans an audio-only download converted to
        ``AUDIO_OUTPUT_CODEC`` at ``AUDIO_BITRATE_KBPS`` (MP3 320 kbps);
        ``resolution`` is ignored in that case.
        """
        media_type = MediaType.parse(media_type)
        if media_type is MediaType.AUDIO:
            validated, metadata = self.fetch_metadata(url)
            audio = select_audio_only(
                metadata.formats, AUDIO_OUTPUT_CODEC, AUDIO_BITRATE_KBPS, metadata.duration_seconds
            )
            label = f"{AUDIO_OUTPUT_CODEC.upper()} {AUDIO_BITRATE_KBPS} kbps"
            return DownloadPlan(
                validated, metadata, label, audio, Path(output_dir or self.config.output_dir)
            )

        label = normalize_resolution_label(resolution or self.config.default_resolution)
        height = resolution_to_height(label)
        validated, metadata = self.fetch_metadata(url)
        selection = select_formats(
            metadata.formats,
            requested_height=height,
            requested_label=label,
            fallback=self.config.fallback,
            prefer_mp4=self.config.prefer_mp4,
        )
        if selection.is_fallback:
            logger.warning(
                "%s not available; falling back to %s (max available)",
                label, selection.selected_label,
            )
        return DownloadPlan(
            validated, metadata, label, selection, Path(output_dir or self.config.output_dir)
        )

    def execute(
        self,
        plan: DownloadPlan,
        progress: ProgressCallback | None = None,
        name_prefix: str | None = None,
        skip_existing: bool = False,
    ) -> DownloadResult:
        """Download the streams chosen in ``plan`` and validate the result.

        ``name_prefix`` is prepended to the file name (playlist numbering).
        With ``skip_existing`` a file that is already present is left alone
        and reported as ``skipped`` (used to resume playlists).
        """
        selection = plan.selection
        ffmpeg_location = None
        if selection.requires_ffmpeg:
            ffmpeg_location = str(self.ffmpeg.require_ffmpeg())
        elif self.ffmpeg.is_available():
            ffmpeg_location = self.ffmpeg.location_for_backend

        output_dir = prepare_output_directory(plan.output_dir)
        output_path = build_output_path(
            output_dir, plan.metadata.title, selection.selected_label, selection.container,
            prefix=name_prefix, reuse_existing=skip_existing,
        )
        if skip_existing and output_path.exists():
            logger.info("Already downloaded, skipping: %s", output_path.name)
            return DownloadResult(output_path, plan, None, skipped=True)
        check_disk_space(output_dir, selection.estimated_size, self.config.disk_space_margin)

        if isinstance(selection, AudioSelection):
            stream_names = {selection.audio.format_id: "audio"}
        else:
            stream_names = {
                selection.video.format_id: "video" if selection.audio else "video+audio"
            }
            if selection.audio:
                stream_names[selection.audio.format_id] = "audio"
        is_audio = isinstance(selection, AudioSelection)
        # Audio: download the original stream untouched next to the target,
        # then encode it to MP3 exactly once (see FFmpegManager.convert_to_mp3).
        download_path = (
            output_path.with_name(f"{output_path.stem}.source.{selection.audio.ext or 'audio'}")
            if is_audio else output_path
        )
        request = BackendDownloadRequest(
            url=plan.url.canonical_url,
            format_spec=selection.format_spec,
            output_path=download_path,
            merge_container=selection.container if selection.requires_merge else None,
            ffmpeg_location=ffmpeg_location,
            stream_names=stream_names,
        )
        logger.info(
            "Download started (video_id=%s, formats=%s, output=%s)",
            plan.url.video_id, selection.format_spec, output_path.name,
        )
        final_path = self._with_retries("download", lambda: self.backend.download(request, progress))
        final_path = ensure_within_directory(Path(final_path), output_dir)
        logger.info("Download completed: %s", final_path.name)
        if is_audio:
            final_path = self._convert_audio(final_path, output_path, plan, progress)
        media_info = self._validate_output(final_path, selection)
        return DownloadResult(final_path, plan, media_info)

    def download(
        self,
        url: str,
        resolution: str | None = None,
        progress: ProgressCallback | None = None,
        media_type: MediaType | str = MediaType.VIDEO,
    ) -> DownloadResult:
        """Convenience wrapper: :meth:`plan` followed by :meth:`execute`."""
        return self.execute(self.plan(url, resolution, media_type), progress)

    # -------------------------------------------------------------- helpers
    def _convert_audio(
        self,
        source: Path,
        target: Path,
        plan: DownloadPlan,
        progress: ProgressCallback | None,
    ) -> Path:
        selection = plan.selection
        if progress:
            progress(ProgressEvent(stage="converting", stream="audio"))
        tags = {
            "title": plan.metadata.title,
            "artist": plan.metadata.uploader or "",
            "comment": plan.url.canonical_url,
        }
        converted = self.ffmpeg.convert_to_mp3(source, target, selection.bitrate_kbps, tags)
        # The original stream was only an intermediate; keep it if conversion failed
        # (exception above) so a re-run can reuse it, remove it once the MP3 exists.
        try:
            source.unlink()
        except OSError as exc:
            logger.warning("Could not remove intermediate file %s: %s", source.name, exc)
        if progress:
            progress(ProgressEvent(stage="converted", stream="audio"))
        return ensure_within_directory(converted, target.parent)

    def _with_retries(self, operation: str, func: Callable[[], T]) -> T:
        attempts = self.config.retries + 1
        for attempt in range(1, attempts + 1):
            try:
                return func()
            except YouTubeDownloadError as exc:
                if not exc.transient or attempt == attempts:
                    logger.debug("%s failed: %s", operation.capitalize(), exc)
                    raise
                delay = self.config.retry_backoff_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "%s failed (attempt %d/%d): %s; retrying in %.1fs",
                    operation.capitalize(), attempt, attempts, exc, delay,
                )
                self._sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    def _validate_output(
        self, path: Path, selection: FormatSelection | AudioSelection
    ) -> MediaInfo | None:
        if not path.is_file() or path.stat().st_size == 0:
            raise OutputValidationError(f"The downloaded file is missing or empty: {path.name}")
        if not self.config.validate_output:
            return None
        info = self.ffmpeg.probe(path)
        if info is None:
            return None
        if isinstance(selection, AudioSelection):
            return self._validate_audio(info, selection)
        if not info.has_video:
            raise OutputValidationError("The downloaded file contains no readable video stream.")
        if not info.has_audio:
            raise OutputValidationError("The downloaded file contains no audio stream.")
        if info.resolution and info.resolution != selection.selected_height:
            logger.warning(
                "Output resolution %sp differs from selected %s",
                info.resolution, selection.selected_label,
            )
        logger.info(
            "Output validated (resolution=%sp, video=%s, audio=%s)",
            info.resolution, info.video_codec, info.audio_codec,
        )
        return info

    @staticmethod
    def _validate_audio(info: MediaInfo, selection: AudioSelection) -> MediaInfo:
        if not info.has_audio:
            raise OutputValidationError("The converted file contains no audio stream.")
        if info.audio_codec and info.audio_codec != selection.codec:
            raise OutputValidationError(
                f"Expected {selection.codec} audio but found {info.audio_codec}."
            )
        bitrate = info.audio_bitrate_kbps
        if bitrate and abs(bitrate - selection.bitrate_kbps) > selection.bitrate_kbps * 0.1:
            logger.warning(
                "Audio bitrate %s kbps differs from target %s kbps", bitrate, selection.bitrate_kbps
            )
        logger.info("Output validated (audio=%s, bitrate=%s kbps)", info.audio_codec, bitrate)
        return info


def ensure_interpreter_bin_on_path() -> None:
    """Put the running interpreter's ``bin``/``Scripts`` folder on ``PATH``.

    Tools installed into the virtual environment (for example the ``deno``
    JavaScript runtime that yt-dlp needs for YouTube) are then found even when
    the environment was not "activated" (as with the ``ytd`` command).
    """
    bin_dir = str(Path(sys.executable).parent)
    parts = os.environ.get("PATH", "").split(os.pathsep)
    if bin_dir not in parts:
        os.environ["PATH"] = os.pathsep.join([bin_dir, *[p for p in parts if p]])
