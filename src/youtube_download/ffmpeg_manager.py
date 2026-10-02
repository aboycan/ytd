"""FFmpeg detection, configuration and media probing.

FFmpeg is required whenever the video and audio streams are downloaded
separately and must be merged (always the case for 1080p on YouTube, and
usually for 720p). ``ffprobe`` (shipped with FFmpeg) is used, when available,
to validate the downloaded file.

Lookup order for the executable:

1. An explicit path (``--ffmpeg-path`` or ``ffmpeg_path`` in the config).
   It may point to the ``ffmpeg`` binary itself or to the folder containing it.
2. ``ffmpeg`` on the system ``PATH``.

External programs are always invoked with an argument list (never
``shell=True``) and a timeout.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .exceptions import DownloadFailedError, FFmpegNotFoundError

logger = logging.getLogger(__name__)

# Generous: on the first start of a freshly downloaded ffmpeg.exe, antivirus
# scanning (e.g. Microsoft Defender) can take well over 15 seconds.
_VERSION_TIMEOUT_SECONDS = 120
_PROBE_TIMEOUT_SECONDS = 60
_CONVERT_TIMEOUT_SECONDS = 3 * 60 * 60  # very long videos


def _executable_name(tool: str) -> str:
    return f"{tool}.exe" if os.name == "nt" else tool


@dataclass(frozen=True)
class MediaInfo:
    """Stream summary of a media file, as reported by ffprobe."""

    has_video: bool
    has_audio: bool
    width: int | None
    height: int | None
    duration_seconds: float | None
    video_codec: str | None
    audio_codec: str | None
    audio_bitrate_kbps: int | None = None

    @property
    def resolution(self) -> int | None:
        """Shorter side of the frame (``1080`` for 1920x1080 and 1080x1920)."""
        if self.width and self.height:
            return min(self.width, self.height)
        return self.height


class FFmpegManager:
    """Locates FFmpeg/ffprobe and runs them safely."""

    def __init__(self, explicit_path: Path | str | None = None) -> None:
        self._explicit_path = Path(explicit_path).expanduser() if explicit_path else None
        self._ffmpeg: Path | None = None
        self._version: str | None = None
        #: Why the last FFmpeg found could not be used (shown in the error message).
        self._problem: str | None = None

    # ------------------------------------------------------------------ lookup
    def _candidate(self, tool: str) -> Path | None:
        if self._explicit_path is not None:
            path = self._explicit_path
            if path.is_dir():
                path = path / _executable_name(tool)
            elif tool != "ffmpeg":
                # Explicit ffmpeg binary given: look for ffprobe beside it.
                path = path.with_name(_executable_name(tool))
            return path if path.is_file() else None
        found = shutil.which(tool)
        return Path(found) if found else None

    def _all_on_path(self, tool: str) -> list[Path]:
        """Every ``tool`` executable on PATH, in PATH order (duplicates removed)."""
        found: list[Path] = []
        seen: set[str] = set()
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            if not directory:
                continue
            hit = shutil.which(tool, path=directory)
            if hit and os.path.normcase(os.path.abspath(hit)) not in seen:
                seen.add(os.path.normcase(os.path.abspath(hit)))
                found.append(Path(hit))
        return found

    def find_ffmpeg(self) -> Path | None:
        """Return the verified FFmpeg path, or ``None`` if it is unavailable.

        Every FFmpeg on PATH is tried in order: a copy that cannot start (for
        example one blocked by Windows Smart App Control or antivirus) is
        skipped in favour of the next working one.
        """
        if self._ffmpeg is not None:
            return self._ffmpeg
        if self._explicit_path is not None:
            candidates = [c for c in [self._candidate("ffmpeg")] if c]
        else:
            candidates = self._all_on_path("ffmpeg")
        if not candidates:
            if self._explicit_path is not None:
                logger.warning("FFmpeg not found at configured path: %s", self._explicit_path)
            else:
                logger.debug("FFmpeg not found on PATH")
            return None
        problems = []
        for candidate in candidates:
            version, problem = self._read_version(candidate)
            if version is not None:
                self._ffmpeg, self._version = candidate, version
                logger.info("FFmpeg detected: %s (%s)", candidate, version)
                return candidate
            problems.append(f"found {candidate}, but it could not be started ({problem})")
        self._problem = "; ".join(problems)
        return None

    def is_available(self) -> bool:
        return self.find_ffmpeg() is not None

    def require_ffmpeg(self) -> Path:
        """Return the FFmpeg path or raise :class:`FFmpegNotFoundError`."""
        path = self.find_ffmpeg()
        if path is None:
            if self._explicit_path is not None:
                raise FFmpegNotFoundError(
                    "FFmpeg is required to merge the selected video and audio streams,\n"
                    f"but no working FFmpeg was found at: {self._explicit_path}\n"
                    "Please install FFmpeg or correct --ffmpeg-path and try again."
                )
            if self._problem:
                raise FFmpegNotFoundError(
                    "FFmpeg is required to merge the selected video and audio streams,\n"
                    f"but {self._problem}.\n"
                    "If antivirus software blocked it, allow the file and run the command again; "
                    "otherwise run 'ytd --setup' (portable zip) or reinstall FFmpeg."
                )
            raise FFmpegNotFoundError()
        return path

    @property
    def version(self) -> str | None:
        self.find_ffmpeg()
        return self._version

    @property
    def location_for_backend(self) -> str | None:
        """Path to hand to the download backend (directory or binary)."""
        path = self.find_ffmpeg()
        return str(path) if path else None

    def find_ffprobe(self) -> Path | None:
        """ffprobe from the same folder as the working FFmpeg, else from PATH."""
        ffmpeg = self._ffmpeg or (None if self._explicit_path else self.find_ffmpeg())
        if ffmpeg is not None:
            beside = ffmpeg.with_name(_executable_name("ffprobe"))
            if beside.is_file():
                return beside
        return self._candidate("ffprobe")

    def ffprobe_problem(self) -> str | None:
        """``None`` when ffprobe starts, else why not (used by ``--doctor``)."""
        ffprobe = self.find_ffprobe()
        if ffprobe is None:
            return "not found"
        version, problem = self._read_version(ffprobe)
        return None if version else f"{ffprobe} could not be started ({problem})"

    @property
    def problem(self) -> str | None:
        """Why no working FFmpeg was found (after :meth:`find_ffmpeg`)."""
        return self._problem

    @staticmethod
    def _read_version(executable: Path) -> tuple[str | None, str | None]:
        """Return ``(version line, None)`` or ``(None, reason it failed)``."""
        try:
            completed = subprocess.run(
                [str(executable), "-version"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=_VERSION_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            reason = f"no response within {_VERSION_TIMEOUT_SECONDS} s"
            logger.warning("Could not run FFmpeg at %s: %s", executable, reason)
            return None, reason
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("Could not run FFmpeg at %s: %s", executable, exc)
            return None, str(exc)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip().splitlines()[-1:]
            reason = f"exit code {completed.returncode}" + (f": {detail[0]}" if detail else "")
            logger.warning("FFmpeg at %s failed: %s", executable, reason)
            return None, reason
        first_line = (completed.stdout or "").splitlines()[:1]
        return (first_line[0].strip() if first_line else "unknown version"), None

    # -------------------------------------------------------------- conversion
    def convert_to_mp3(
        self,
        source: Path,
        target: Path,
        bitrate_kbps: int,
        tags: dict[str, str] | None = None,
    ) -> Path:
        """Encode the first audio stream of ``source`` into an MP3 at ``target``.

        Quality settings (the best MP3 that LAME can produce):

        - constant bitrate ``bitrate_kbps`` (320 = MP3 maximum),
        - ``-compression_level 0`` = LAME algorithm quality 0 (best, slowest),
        - the source sample rate is kept (no resampling) and the audio is
          decoded exactly once, so the only loss is the single MP3 encode.

        ``target`` must not exist (``-n``); the file is never overwritten.
        """
        ffmpeg = self.require_ffmpeg()
        command = [
            str(ffmpeg), "-hide_banner", "-nostdin", "-v", "error", "-n",
            "-i", str(Path(source).resolve()),
            "-map", "0:a:0", "-vn", "-sn", "-dn",
            "-c:a", "libmp3lame", "-b:a", f"{int(bitrate_kbps)}k", "-compression_level", "0",
            "-map_metadata", "-1", "-id3v2_version", "3",
        ]
        for key, value in (tags or {}).items():
            if value:
                command += ["-metadata", f"{key}={value}"]
        command.append(str(Path(target).resolve()))
        logger.info("Converting to MP3 (%d kbps CBR, LAME q0): %s", bitrate_kbps, Path(target).name)
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True,
                timeout=_CONVERT_TIMEOUT_SECONDS, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            Path(target).unlink(missing_ok=True)
            raise DownloadFailedError(f"MP3 conversion failed: {exc}") from exc
        if completed.returncode != 0:
            Path(target).unlink(missing_ok=True)
            detail = (completed.stderr or "").strip().splitlines()[-1:] or ["unknown error"]
            if "libmp3lame" in (completed.stderr or ""):
                raise DownloadFailedError(
                    "This FFmpeg build has no MP3 encoder (libmp3lame). "
                    "Install a full FFmpeg build (the installer does this)."
                )
            raise DownloadFailedError(f"MP3 conversion failed: {detail[0]}")
        return Path(target)

    # ----------------------------------------------------------------- probing
    def probe(self, media_path: Path) -> MediaInfo | None:
        """Describe ``media_path`` with ffprobe; ``None`` if ffprobe is missing."""
        ffprobe = self.find_ffprobe()
        if ffprobe is None:
            logger.info("ffprobe not available; skipping stream validation")
            return None
        command = [
            str(ffprobe),
            "-v", "error",
            "-print_format", "json",
            "-show_streams",
            "-show_format",
            "-i",
            str(Path(media_path).resolve()),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("ffprobe failed: %s", exc)
            return None
        if completed.returncode != 0:
            logger.warning("ffprobe could not read %s: %s", media_path.name, completed.stderr.strip())
            return MediaInfo(False, False, None, None, None, None, None)
        return parse_ffprobe_output(completed.stdout)


def parse_ffprobe_output(raw_json: str) -> MediaInfo:
    """Convert ``ffprobe -print_format json`` output into :class:`MediaInfo`."""
    try:
        data = json.loads(raw_json or "{}")
    except json.JSONDecodeError:
        data = {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration_raw = (data.get("format") or {}).get("duration")
    try:
        duration = float(duration_raw) if duration_raw is not None else None
    except (TypeError, ValueError):
        duration = None
    return MediaInfo(
        has_video=video is not None,
        has_audio=audio is not None,
        width=int(video["width"]) if video and video.get("width") else None,
        height=int(video["height"]) if video and video.get("height") else None,
        duration_seconds=duration,
        video_codec=video.get("codec_name") if video else None,
        audio_codec=audio.get("codec_name") if audio else None,
        audio_bitrate_kbps=_kbps(audio.get("bit_rate")) if audio else None,
    )


def _kbps(value: object) -> int | None:
    try:
        return round(int(str(value)) / 1000) if value is not None else None
    except ValueError:
        return None
