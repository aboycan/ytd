"""Video metadata model.

Converts the raw information dictionary returned by the download backend into
small typed dataclasses. Only the fields this utility uses are kept, which
keeps the rest of the code independent of the backend's data layout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

#: Protocols that never contain downloadable media (storyboards etc.).
_IGNORED_PROTOCOLS = {"mhtml"}


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _codec(value: Any) -> str | None:
    if value in (None, "", "none"):
        return None
    return str(value)


@dataclass(frozen=True)
class MediaFormat:
    """One stream/format offered for a video."""

    format_id: str
    ext: str
    height: int | None = None
    width: int | None = None
    fps: float | None = None
    vcodec: str | None = None
    acodec: str | None = None
    tbr: float | None = None  # total bitrate, kbit/s
    abr: float | None = None  # audio bitrate, kbit/s
    filesize: int | None = None  # exact or approximate, bytes
    protocol: str | None = None
    format_note: str | None = None
    has_drm: bool = False

    @property
    def resolution(self) -> int | None:
        """Quality level in the YouTube sense: the *shorter* frame side.

        A landscape 1920x1080 stream and a vertical (Shorts) 1080x1920 stream
        are both "1080p". Falls back to ``height`` when the width is unknown.
        """
        if self.width and self.height:
            return min(self.width, self.height)
        return self.height

    @property
    def has_video(self) -> bool:
        return self.vcodec is not None

    @property
    def has_audio(self) -> bool:
        return self.acodec is not None

    @property
    def is_video_only(self) -> bool:
        return self.has_video and not self.has_audio

    @property
    def is_audio_only(self) -> bool:
        return self.has_audio and not self.has_video

    @property
    def is_muxed(self) -> bool:
        """True when a single file holds both video and audio."""
        return self.has_video and self.has_audio

    @property
    def is_direct(self) -> bool:
        """True for a plain HTTPS file (faster, known size) rather than HLS/DASH."""
        return (self.protocol or "https") in {"https", "http"}

    @property
    def is_drc(self) -> bool:
        """YouTube "DRC" audio: dynamic-range-compressed (quieter, lower fidelity)."""
        return "drc" in self.format_id.lower() or "drc" in (self.format_note or "").lower()

    @property
    def is_downloadable(self) -> bool:
        return not self.has_drm and (self.protocol or "") not in _IGNORED_PROTOCOLS

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MediaFormat":
        vcodec = _codec(data.get("vcodec"))
        acodec = _codec(data.get("acodec"))
        height = _as_int(data.get("height"))
        # yt-dlp uses the string "none" for an absent stream. A *missing* codec
        # field with a known height still means the format carries video.
        if data.get("vcodec") is None and height:
            vcodec = "unknown"
        return cls(
            format_id=str(data.get("format_id", "")),
            ext=str(data.get("ext") or ""),
            height=height,
            width=_as_int(data.get("width")),
            fps=_as_float(data.get("fps")),
            vcodec=vcodec,
            acodec=acodec,
            tbr=_as_float(data.get("tbr")),
            abr=_as_float(data.get("abr")),
            filesize=_as_int(data.get("filesize") or data.get("filesize_approx")),
            protocol=data.get("protocol"),
            format_note=data.get("format_note"),
            has_drm=bool(data.get("has_drm")),
        )


@dataclass(frozen=True)
class VideoMetadata:
    """Information about a video, retrieved before downloading."""

    video_id: str
    title: str
    uploader: str | None = None
    duration_seconds: int | None = None
    webpage_url: str | None = None
    is_live: bool = False
    formats: tuple[MediaFormat, ...] = field(default_factory=tuple)

    @property
    def available_heights(self) -> list[int]:
        """Distinct downloadable resolutions (see ``MediaFormat.resolution``), highest first."""
        heights = {
            f.resolution for f in self.formats
            if f.has_video and f.resolution and f.is_downloadable
        }
        return sorted(heights, reverse=True)

    @property
    def max_height(self) -> int | None:
        heights = self.available_heights
        return heights[0] if heights else None

    @property
    def duration_display(self) -> str:
        return format_duration(self.duration_seconds)

    @classmethod
    def from_info(cls, info: Mapping[str, Any]) -> "VideoMetadata":
        raw_formats: Sequence[Mapping[str, Any]] = info.get("formats") or []
        return cls(
            video_id=str(info.get("id", "")),
            title=str(info.get("title") or info.get("id") or "video"),
            uploader=info.get("uploader") or info.get("channel"),
            duration_seconds=_as_int(info.get("duration")),
            webpage_url=info.get("webpage_url"),
            is_live=bool(info.get("is_live")),
            formats=tuple(MediaFormat.from_dict(f) for f in raw_formats),
        )


def format_duration(seconds: int | None) -> str:
    """Format seconds as ``H:MM:SS`` / ``M:SS``; ``"unknown"`` when missing."""
    if seconds is None or seconds < 0:
        return "unknown"
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_bytes(size: int | None) -> str:
    """Human readable byte size (``"12.3 MiB"``); ``"unknown"`` when missing."""
    if size is None or size < 0:
        return "unknown"
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"  # pragma: no cover - unreachable
