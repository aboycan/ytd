"""Validation and normalization of YouTube video URLs.

The URL is untrusted input. Validation is purely syntactic (no network
access): the host must be a known YouTube host and an 11-character video id
must be present in one of the supported URL shapes. Valid URLs are normalized to
``https://www.youtube.com/watch?v=<id>`` so that extra query parameters
(playlists, tracking, timestamps) never reach the downloader.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from .exceptions import InvalidURLError

logger = logging.getLogger(__name__)

VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
MAX_URL_LENGTH = 2048

_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}
_SHORT_HOSTS = {"youtu.be", "www.youtu.be"}
#: Path prefixes of the form ``/<prefix>/<video_id>``.
_PATH_ID_PREFIXES = ("shorts", "embed", "live", "v", "e")


@dataclass(frozen=True)
class ValidatedURL:
    """A validated YouTube video reference.

    ``playlist_id`` records a ``list=`` parameter if the link had one; it is
    *not* part of ``canonical_url``, so a single-video download never pulls
    in the rest of the playlist.
    """

    video_id: str
    original_url: str
    playlist_id: str | None = None

    @property
    def canonical_url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


@dataclass(frozen=True)
class ValidatedPlaylist:
    """A validated YouTube playlist (or Mix) reference."""

    playlist_id: str
    original_url: str
    video_id: str | None = None

    @property
    def is_mix(self) -> bool:
        """YouTube "Mix"/radio lists (``RD...``) are generated on the fly per viewer."""
        return self.playlist_id.upper().startswith("RD")

    @property
    def canonical_url(self) -> str:
        # Mixes have no playlist page; they are only reachable through a video.
        if self.is_mix and self.video_id:
            return f"https://www.youtube.com/watch?v={self.video_id}&list={self.playlist_id}"
        return f"https://www.youtube.com/playlist?list={self.playlist_id}"


PLAYLIST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{2,64}$")


def _extract_video_id(host: str, path: str, query: str) -> str | None:
    segments = [s for s in path.split("/") if s]
    if host in _SHORT_HOSTS:
        return segments[0] if segments else None
    if segments[:1] == ["watch"] and len(segments) == 1:
        values = parse_qs(query).get("v")
        return values[0] if values else None
    if len(segments) >= 2 and segments[0] in _PATH_ID_PREFIXES:
        return segments[1]
    return None


def _extract_playlist_id(query: str) -> str | None:
    values = parse_qs(query).get("list")
    if values and PLAYLIST_ID_PATTERN.match(values[0]):
        return values[0]
    return None


def _parse(url: str | None) -> tuple[str, str, str, str]:
    """Return ``(original, host, path, query)`` for a syntactically valid YouTube URL."""
    if url is None or not str(url).strip():
        raise InvalidURLError("Invalid YouTube URL: no URL was provided.")
    original = str(url).strip()
    candidate = original
    if len(candidate) > MAX_URL_LENGTH or any(ch.isspace() for ch in candidate):
        raise InvalidURLError()
    if "://" not in candidate:
        candidate = "https://" + candidate

    try:
        parts = urlsplit(candidate)
        host = (parts.hostname or "").lower()
        _ = parts.port  # raises ValueError for malformed ports
    except ValueError:
        raise InvalidURLError() from None

    if parts.scheme.lower() not in {"http", "https"}:
        raise InvalidURLError()
    if parts.username or parts.password:
        raise InvalidURLError()
    if host not in _YOUTUBE_HOSTS and host not in _SHORT_HOSTS:
        raise InvalidURLError()
    return original, host, parts.path, parts.query


def validate_youtube_url(url: str | None) -> ValidatedURL:
    """Validate ``url`` and return the extracted *single video* reference.

    Supported shapes::

        https://www.youtube.com/watch?v=XXXXXXXXXXX
        https://www.youtube.com/watch?v=XXXXXXXXXXX&list=...   (only the video)
        https://m.youtube.com/watch?v=XXXXXXXXXXX
        https://youtu.be/XXXXXXXXXXX
        https://www.youtube.com/shorts/XXXXXXXXXXX
        https://www.youtube.com/embed/XXXXXXXXXXX
        https://www.youtube.com/live/XXXXXXXXXXX

    A missing scheme (``youtube.com/watch?v=...``) is accepted.

    Raises:
        InvalidURLError: if the URL is empty, malformed or not a YouTube video.
    """
    original, host, path, query = _parse(url)
    video_id = _extract_video_id(host, path, query)
    playlist_id = _extract_playlist_id(query)
    if not video_id and playlist_id:
        raise InvalidURLError(
            "This is a playlist link without a single video. "
            "Add --list to download the playlist."
        )
    if not video_id or not VIDEO_ID_PATTERN.match(video_id):
        raise InvalidURLError()

    logger.info("URL validated (video_id=%s, playlist=%s)", video_id, playlist_id)
    return ValidatedURL(video_id=video_id, original_url=original, playlist_id=playlist_id)


def validate_playlist_url(url: str | None) -> ValidatedPlaylist:
    """Validate a playlist link (``list=`` parameter required).

    Accepts ``/playlist?list=...`` and ``/watch?v=...&list=...`` (including
    YouTube Mixes, ``list=RD...``).

    Raises:
        InvalidURLError: malformed URL, or no playlist in the link.
    """
    original, host, path, query = _parse(url)
    playlist_id = _extract_playlist_id(query)
    if not playlist_id:
        raise InvalidURLError("This link does not contain a playlist (no 'list=' parameter).")
    video_id = _extract_video_id(host, path, query)
    if video_id and not VIDEO_ID_PATTERN.match(video_id):
        video_id = None
    logger.info("Playlist URL validated (playlist=%s, video_id=%s)", playlist_id, video_id)
    return ValidatedPlaylist(playlist_id=playlist_id, original_url=original, video_id=video_id)


def is_valid_youtube_url(url: str | None) -> bool:
    """Boolean convenience wrapper around :func:`validate_youtube_url`."""
    try:
        validate_youtube_url(url)
    except InvalidURLError:
        return False
    return True
