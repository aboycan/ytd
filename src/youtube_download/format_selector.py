"""Deterministic video/audio format selection.

Selection logic (pure function, no network access)
--------------------------------------------------
Given the list of formats reported for a video and a requested maximum height:

1. **Candidates** – keep downloadable formats (no DRM, no storyboards) that
   contain a video stream with a known height. A stream's resolution is its
   *shorter* side, as YouTube labels it (1080x1920 Shorts are "1080p").
2. **Height** – take the *highest* resolution ``<=`` the requested one.
   A higher resolution than requested is never selected.
3. **Fallback** – if that height is lower than requested the selection is
   marked ``is_fallback``. With ``FallbackPolicy.ERROR`` a
   :class:`ResolutionUnavailableError` is raised instead. The caller must
   always report a fallback to the user; it is never silent.
4. **Video stream** at that height, ranked by (in order):
   MP4 container when ``prefer_mp4`` → widely playable codec when
   ``prefer_mp4`` (H.264 > HEVC > AV1 > VP9, so the file opens in QuickTime
   and Windows players) → direct HTTPS over HLS/DASH streams → frame rate →
   total bitrate → file size → format id (tie-breaker, keeps the result
   deterministic).
5. **Audio stream** (only when the video stream has no audio), ranked by:
   original audio over YouTube's dynamic-range-compressed "DRC" variant →
   M4A/MP4 audio when the video is MP4 (so the result can stay in MP4) →
   direct HTTPS → audio bitrate → file size → format id.
6. **Container** – separate streams are merged by FFmpeg into ``mp4`` when
   both streams are MP4-family, otherwise into ``mkv`` (which accepts every
   codec YouTube uses, e.g. VP9/Opus). A single muxed stream keeps its own
   extension and needs no merge.

The chosen format ids are passed to the backend explicitly (``"137+140"``), so
the file that is downloaded is exactly the one reported to the user. For
reference, the behaviour is close to the yt-dlp selector::

    bv*[height<=H][ext=mp4]+ba[ext=m4a] / bv*[height<=H]+ba / b[height<=H]
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

from .config import FallbackPolicy
from .exceptions import DownloadFailedError, ResolutionUnavailableError
from .metadata import MediaFormat

logger = logging.getLogger(__name__)

MP4_VIDEO_EXTS = {"mp4"}
MP4_AUDIO_EXTS = {"m4a", "mp4"}
MERGE_CONTAINER_MP4 = "mp4"
MERGE_CONTAINER_FALLBACK = "mkv"


@dataclass(frozen=True)
class FormatSelection:
    """The streams chosen for a download."""

    requested_height: int
    video: MediaFormat
    audio: MediaFormat | None
    container: str

    is_audio_only = False

    @property
    def requires_ffmpeg(self) -> bool:
        return self.requires_merge

    @property
    def selected_height(self) -> int:
        return int(self.video.resolution or 0)

    @property
    def selected_label(self) -> str:
        return f"{self.selected_height}p"

    @property
    def requires_merge(self) -> bool:
        return self.audio is not None

    @property
    def is_fallback(self) -> bool:
        return self.selected_height < self.requested_height

    @property
    def format_spec(self) -> str:
        """Backend format expression, e.g. ``"137+140"`` or ``"18"``."""
        if self.audio is None:
            return self.video.format_id
        return f"{self.video.format_id}+{self.audio.format_id}"

    @property
    def estimated_size(self) -> int | None:
        """Sum of the known stream sizes, or ``None`` if any is unknown."""
        sizes = [self.video.filesize] + ([self.audio.filesize] if self.audio else [])
        if any(size is None for size in sizes):
            return None
        return sum(int(size) for size in sizes if size is not None)


#: Playback compatibility of video codecs (higher is better). H.264 plays
#: everywhere, including QuickTime/Windows Media Player; AV1 and VP9 do not.
_CODEC_COMPAT = (("avc1", 3), ("h264", 3), ("hev1", 2), ("hvc1", 2), ("av01", 1))


def _codec_compat(fmt: MediaFormat) -> int:
    codec = (fmt.vcodec or "").lower()
    return next((score for prefix, score in _CODEC_COMPAT if codec.startswith(prefix)), 0)


def _video_rank(fmt: MediaFormat, prefer_mp4: bool) -> tuple:
    return (
        1 if prefer_mp4 and fmt.ext in MP4_VIDEO_EXTS else 0,
        _codec_compat(fmt) if prefer_mp4 else 0,
        1 if fmt.is_direct else 0,
        fmt.fps or 0.0,
        fmt.tbr or 0.0,
        fmt.filesize or 0,
        fmt.format_id,
    )


def _audio_rank(fmt: MediaFormat, want_mp4_audio: bool) -> tuple:
    return (
        0 if fmt.is_drc else 1,
        1 if want_mp4_audio and fmt.ext in MP4_AUDIO_EXTS else 0,
        1 if fmt.is_direct else 0,
        fmt.abr or fmt.tbr or 0.0,
        fmt.filesize or 0,
        fmt.format_id,
    )


def _merge_container(video: MediaFormat, audio: MediaFormat, prefer_mp4: bool) -> str:
    if prefer_mp4 and video.ext in MP4_VIDEO_EXTS and audio.ext in MP4_AUDIO_EXTS:
        return MERGE_CONTAINER_MP4
    if not prefer_mp4 and video.ext == "webm" and audio.ext == "webm":
        return "webm"
    return MERGE_CONTAINER_FALLBACK


def select_formats(
    formats: Iterable[MediaFormat],
    requested_height: int,
    requested_label: str | None = None,
    fallback: FallbackPolicy = FallbackPolicy.LOWER,
    prefer_mp4: bool = True,
) -> FormatSelection:
    """Choose the video (and audio) streams for ``requested_height``.

    Raises:
        ResolutionUnavailableError: no suitable resolution (or fallback refused).
        DownloadFailedError: video streams exist but no audio can be paired.
    """
    label = requested_label or f"{requested_height}p"
    usable = [f for f in formats if f.is_downloadable]
    videos = [f for f in usable if f.has_video and f.resolution]
    audios = [f for f in usable if f.is_audio_only]

    if not videos:
        raise ResolutionUnavailableError(
            label, None, "No downloadable video streams were found for this video."
        )

    max_available = max(int(f.resolution or 0) for f in videos)
    eligible = [f for f in videos if int(f.resolution or 0) <= requested_height]
    if not audios:
        # Without a separate audio stream only muxed formats yield a file with sound.
        eligible = [f for f in eligible if f.is_muxed]
    if not eligible:
        if not audios and any(int(f.resolution or 0) <= requested_height for f in videos):
            raise DownloadFailedError("No audio stream is available for this video.")
        raise ResolutionUnavailableError(
            label,
            max_available,
            f"{label} is not available for this video, and no lower resolution "
            f"exists. Lowest available resolution: "
            f"{min(int(f.resolution or 0) for f in videos)}p",
        )

    best_height = max(int(f.resolution or 0) for f in eligible)
    if best_height < requested_height and fallback is FallbackPolicy.ERROR:
        raise ResolutionUnavailableError(label, best_height)

    at_height = [f for f in eligible if f.resolution == best_height]
    video = max(at_height, key=lambda f: _video_rank(f, prefer_mp4))

    if video.is_muxed:
        selection = FormatSelection(requested_height, video, None, video.ext)
    else:
        want_mp4_audio = prefer_mp4 and video.ext in MP4_VIDEO_EXTS
        audio = max(audios, key=lambda f: _audio_rank(f, want_mp4_audio))
        selection = FormatSelection(
            requested_height, video, audio, _merge_container(video, audio, prefer_mp4)
        )

    logger.info(
        "Formats selected: requested=%s selected=%s spec=%s container=%s fallback=%s",
        label,
        selection.selected_label,
        selection.format_spec,
        selection.container,
        selection.is_fallback,
    )
    return selection


# --------------------------------------------------------------------------- #
# Audio-only selection
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AudioSelection:
    """Audio-only download: one source stream converted to ``codec``.

    The source is YouTube's best audio stream (usually Opus at ~130-160 kbps),
    downloaded untouched and then encoded exactly once to ``codec`` at
    ``bitrate_kbps`` constant bitrate with the encoder's highest quality
    setting. MP3 is lossy, so this single encode is the only loss; a higher
    bitrate than the source cannot add back detail YouTube never delivered.
    """

    audio: MediaFormat
    codec: str
    bitrate_kbps: int
    duration_seconds: int | None = None

    is_audio_only = True
    video = None
    requires_merge = False
    requires_ffmpeg = True  # conversion always needs FFmpeg
    is_fallback = False
    selected_height = 0

    @property
    def container(self) -> str:
        return self.codec

    @property
    def selected_label(self) -> str:
        return f"{self.bitrate_kbps}kbps"

    @property
    def format_spec(self) -> str:
        return self.audio.format_id

    @property
    def estimated_size(self) -> int | None:
        """Size of the converted file (constant bitrate x duration)."""
        if not self.duration_seconds:
            return None
        return int(self.duration_seconds * self.bitrate_kbps * 1000 / 8)


def _audio_source_rank(fmt: MediaFormat) -> tuple:
    # The file is re-encoded anyway, so container does not matter: take the
    # highest-quality original (non-DRC) audio, direct download preferred.
    return (
        0 if fmt.is_drc else 1,
        fmt.abr or fmt.tbr or 0.0,
        1 if fmt.is_direct else 0,
        fmt.filesize or 0,
        fmt.format_id,
    )


def select_audio_only(
    formats: Iterable[MediaFormat],
    codec: str,
    bitrate_kbps: int,
    duration_seconds: int | None = None,
) -> AudioSelection:
    """Choose the best audio source for an audio-only download.

    Uses an audio-only stream when available; otherwise the smallest muxed
    (video+audio) stream, from which the audio is extracted.

    Raises:
        DownloadFailedError: the video offers no audio at all.
    """
    usable = [f for f in formats if f.is_downloadable]
    audios = [f for f in usable if f.is_audio_only]
    if audios:
        source = max(audios, key=_audio_source_rank)
    else:
        muxed = [f for f in usable if f.is_muxed]
        if not muxed:
            raise DownloadFailedError("No audio stream is available for this video.")
        source = min(muxed, key=lambda f: (f.resolution or 0, f.format_id))
    selection = AudioSelection(source, codec, bitrate_kbps, duration_seconds)
    logger.info(
        "Audio selected: source=%s (%s, %s kbps) target=%s %dkbps",
        source.format_id, source.acodec, source.abr, codec, bitrate_kbps,
    )
    return selection
