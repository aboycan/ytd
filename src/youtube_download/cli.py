"""Command-line interface.

The CLI only handles user interaction and presentation; all download logic
lives in :class:`~youtube_download.downloader.YouTubeDownloader`.
"""

from __future__ import annotations

import argparse
import logging
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Sequence, TextIO

from . import __version__
from .config import (
    AUDIO_BITRATE_KBPS,
    AUDIO_OUTPUT_CODEC,
    AppConfig,
    FallbackPolicy,
    MediaType,
    load_config,
    normalize_resolution_label,
    supported_resolution_labels,
)
from .downloader import (
    DownloadPlan,
    DownloadResult,
    PlaylistInfo,
    ProgressEvent,
    YouTubeDownloader,
    ensure_interpreter_bin_on_path,
)
from .exceptions import (
    ConfigurationError,
    DependencyMissingError,
    DownloadFailedError,
    FFmpegNotFoundError,
    InvalidURLError,
    OutputPathError,
    UnsupportedResolutionError,
    YouTubeDownloadError,
)
from .url_validator import validate_playlist_url, validate_youtube_url
from .ffmpeg_manager import FFmpegManager
from .logging_config import configure_logging
from .metadata import VideoMetadata, format_bytes, format_duration

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_PLAYLIST_PARTIAL = 11  # some playlist items failed
EXIT_INTERRUPTED = 130

DownloaderFactory = Callable[[AppConfig], YouTubeDownloader]


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #
def _resolution_arg(value: str) -> str:
    try:
        return normalize_resolution_label(value)
    except UnsupportedResolutionError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


#: ``key=value`` words accepted in addition to ``--key value`` (``ytd URL type=audio``).
_KEYWORD_ARGS = {"type": "--type", "resolution": "--resolution", "res": "--resolution",
                 "output": "--output", "url": "--url"}
_URL_START = re.compile(r"^-{1,2}(?=(https?:|www\.|m\.youtube|music\.youtube|youtube\.|youtu\.be))",
                        re.IGNORECASE)


def normalize_argv(argv: Sequence[str]) -> list[str]:
    """Accept friendly spellings before argparse sees the arguments.

    - ``type=audio`` / ``resolution=720p`` → ``--type audio`` / ``--resolution 720p``
    - ``--"https://youtu.be/..."`` (the shell turns it into ``--https://...``) → the URL
    """
    result: list[str] = []
    for token in argv:
        token = _URL_START.sub("", token)
        key, sep, value = token.partition("=")
        if sep and not token.startswith("-") and key.lower() in _KEYWORD_ARGS:
            result += [_KEYWORD_ARGS[key.lower()], value]
        else:
            result.append(token)
    return result


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number < 1:
        raise argparse.ArgumentTypeError("must be a whole number of at least 1")
    return number


def build_parser() -> argparse.ArgumentParser:
    labels = "/".join(sorted(supported_resolution_labels(), key=lambda s: int(s[:-1])))
    parser = argparse.ArgumentParser(
        prog="ytd",
        description=(
            "Download a YouTube video (default 1080p) or its audio "
            f"({AUDIO_OUTPUT_CODEC.upper()} {AUDIO_BITRATE_KBPS} kbps)."
        ),
        epilog=(
            "Examples:\n"
            '  ytd "https://youtu.be/XXXXXXXXXXX"                  video, 1080p\n'
            '  ytd "https://youtu.be/XXXXXXXXXXX" -r 720p          video, 720p\n'
            '  ytd "https://youtu.be/XXXXXXXXXXX" --type audio     MP3 320 kbps\n'
            '  ytd "https://youtu.be/XXXXXXXXXXX" type=audio       same as above\n'
            '  ytd "https://www.youtube.com/watch?v=X&list=Y"      only that one video\n'
            '  ytd "https://www.youtube.com/watch?v=X&list=Y" --list   the whole playlist\n'
            '  ytd "<playlist URL>" --list --type audio            playlist as MP3s\n'
            "  ytd                                                 interactive mode\n"
            "  ytd --doctor                                        check dependencies\n\n"
            "Only download content you have the right to download."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("url_arg", nargs="?", metavar="URL",
                        help="YouTube video URL (watch, youtu.be, shorts).")
    parser.add_argument("--url", "-u", help="YouTube video URL (alternative to the positional URL).")
    parser.add_argument(
        "--type", "-t", dest="media_type", type=str.lower, choices=[m.value for m in MediaType],
        help=f"video (default) or audio (single {AUDIO_OUTPUT_CODEC.upper()} "
             f"{AUDIO_BITRATE_KBPS} kbps file).",
    )
    parser.add_argument(
        "--resolution", "-r", type=_resolution_arg, metavar=labels,
        help=f"Video resolution ({labels}). Default: 1080p.",
    )
    parser.add_argument(
        "--list", "-l", dest="playlist", action="store_true",
        help="Download the whole playlist the link belongs to (without it, only the one video).",
    )
    parser.add_argument(
        "--limit", type=_positive_int, metavar="N",
        help="With --list: download at most N items (YouTube Mixes default to 25).",
    )
    parser.add_argument(
        "--output", "-o", type=Path,
        help="Output directory. Default: the project's downloads/ folder.",
    )
    parser.add_argument(
        "--ffmpeg-path", type=Path,
        help="Path to the ffmpeg executable or to the folder containing it.",
    )
    parser.add_argument(
        "--config", type=Path,
        help="Path to a JSON config file. Default: config/config.json.",
    )
    parser.add_argument(
        "--no-fallback", action="store_true",
        help="Fail instead of downloading a lower resolution when the requested one is unavailable.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--info", action="store_true",
                      help="Show video information and the format that would be selected; do not download.")
    mode.add_argument("--list-formats", action="store_true",
                      help="List the available video/audio formats; do not download.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show DEBUG logs.")
    parser.add_argument(
        "--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], type=str.upper,
        help="Console log level (default ERROR; --verbose implies DEBUG).",
    )
    parser.add_argument("--log-file", type=Path, help="Also write a DEBUG log to this file.")
    tools = parser.add_mutually_exclusive_group()
    tools.add_argument("--doctor", action="store_true",
                       help="Check Python, yt-dlp, FFmpeg and Deno, then exit.")
    tools.add_argument("--update", action="store_true",
                       help="Update yt-dlp and Deno (fixes most YouTube breakages), then exit.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


# --------------------------------------------------------------------------- #
# Presentation
# --------------------------------------------------------------------------- #
class ProgressPrinter:
    """Renders :class:`ProgressEvent` updates as a text progress bar."""

    WIDTH = 28

    def __init__(self, stream: TextIO) -> None:
        self.stream = stream
        self.interactive = bool(getattr(stream, "isatty", lambda: False)())
        encoding = getattr(stream, "encoding", None) or "ascii"
        try:
            "█░".encode(encoding)
            self.fill, self.empty = "█", "░"
        except (UnicodeEncodeError, LookupError):
            self.fill, self.empty = "#", "-"
        self._line_open = False

    def __call__(self, event: ProgressEvent) -> None:
        label = f"{(event.stream or 'stream'):<11}"
        if event.stage == "downloading":
            if not self.interactive:
                return
            self.stream.write("\r" + self._bar_line(label, event))
            self.stream.flush()
            self._line_open = True
        elif event.stage == "finished":
            done = ProgressEvent("finished", event.stream, event.total_bytes or event.downloaded_bytes,
                                 event.total_bytes or event.downloaded_bytes)
            self.stream.write(("\r" if self._line_open else "") + self._bar_line(label, done) + "\n")
            self._line_open = False
        elif event.stage == "merging":
            self.stream.write("Merging video and audio with FFmpeg...\n")
        elif event.stage == "merged":
            self.stream.write("Merge complete.\n")
        elif event.stage == "converting":
            self.stream.write(
                f"Converting to {AUDIO_OUTPUT_CODEC.upper()} {AUDIO_BITRATE_KBPS} kbps with FFmpeg...\n"
            )
        elif event.stage == "converted":
            self.stream.write("Conversion complete.\n")
        self.stream.flush()

    def _bar_line(self, label: str, event: ProgressEvent) -> str:
        fraction = event.fraction
        if fraction is None:
            return f"{label} {format_bytes(event.downloaded_bytes)} downloaded"
        filled = int(round(fraction * self.WIDTH))
        bar = self.fill * filled + self.empty * (self.WIDTH - filled)
        parts = [f"{label} [{bar}] {fraction * 100:5.1f}%",
                 f"{format_bytes(event.downloaded_bytes)}/{format_bytes(event.total_bytes)}"]
        if event.stage == "downloading":
            if event.speed_bytes_per_second:
                parts.append(f"{format_bytes(int(event.speed_bytes_per_second))}/s")
            if event.eta_seconds is not None:
                parts.append(f"ETA {format_duration(int(event.eta_seconds))}")
        return "  ".join(parts).ljust(90)


def _print_audio_plan(plan: DownloadPlan, out: TextIO) -> None:
    meta, sel = plan.metadata, plan.selection
    src = sel.audio
    rate = f", {src.abr:g} kbps" if src.abr else ""
    rows = [
        ("Video", meta.title),
        ("Channel", meta.uploader or "unknown"),
        ("Duration", meta.duration_display),
        ("Type", "Audio only"),
        ("Format", plan.requested_label),
        ("Source", f"audio {src.format_id} ({src.ext}, {src.acodec}{rate}) - best original track"),
        ("Encoding", "single encode, LAME highest quality, CBR, sample rate kept"),
        ("Est. size", format_bytes(sel.estimated_size)),
        ("Output dir", str(plan.output_dir)),
    ]
    for key, value in rows:
        out.write(f"{key:<11}: {value}\n")


def _print_plan(plan: DownloadPlan, out: TextIO) -> None:
    if plan.selection.is_audio_only:
        _print_audio_plan(plan, out)
        return
    meta, sel = plan.metadata, plan.selection
    video = sel.video
    fps = f" {video.fps:g}fps" if video.fps else ""
    streams = f"video {video.format_id} ({video.ext}{fps}, {video.vcodec})"
    if sel.audio:
        streams += f" + audio {sel.audio.format_id} ({sel.audio.ext}, {sel.audio.acodec})"
    rows = [
        ("Video", meta.title),
        ("Channel", meta.uploader or "unknown"),
        ("Duration", meta.duration_display),
        ("Requested", plan.requested_label),
        ("Selected", sel.selected_label),
        ("Format", sel.container.upper()),
        ("Streams", streams),
        ("Merge", "yes (FFmpeg)" if sel.requires_merge else "no"),
        ("Est. size", format_bytes(sel.estimated_size)),
        ("Output dir", str(plan.output_dir)),
    ]
    for key, value in rows:
        out.write(f"{key:<11}: {value}\n")
    if sel.is_fallback:
        out.write(
            f"\nWARNING: {plan.requested_label} is not available for this video.\n"
            f"         Available maximum resolution: {sel.selected_label} "
            f"- downloading {sel.selected_label} instead.\n"
            "         Use --no-fallback to stop instead.\n"
        )


def _print_formats(meta: VideoMetadata, out: TextIO) -> None:
    out.write(f"Formats for: {meta.title}\n")
    out.write(f"{'ID':<8}{'EXT':<6}{'RES':<8}{'FPS':<6}{'VCODEC':<16}{'ACODEC':<14}{'SIZE':>11}\n")
    for f in sorted(meta.formats, key=lambda f: (f.resolution or 0, f.tbr or 0)):
        if not f.is_downloadable:
            continue
        res = f"{f.resolution}p" if f.has_video and f.resolution else "audio"
        out.write(
            f"{f.format_id:<8}{f.ext:<6}{res:<8}{(f'{f.fps:g}' if f.fps else ''):<6}"
            f"{(f.vcodec or '-')[:15]:<16}{(f.acodec or '-')[:13]:<14}{format_bytes(f.filesize):>11}\n"
        )
    heights = ", ".join(f"{h}p" for h in meta.available_heights) or "none"
    out.write(f"\nAvailable resolutions: {heights}\n")


def _print_result(result: DownloadResult, out: TextIO) -> None:
    out.write("\nDownload complete.\n")
    out.write(f"{'File':<11}: {result.path}\n")
    out.write(f"{'Size':<11}: {format_bytes(result.size_bytes)}\n")
    info = result.media_info
    if info is not None and result.plan.selection.is_audio_only:
        rate = f" {info.audio_bitrate_kbps} kbps" if info.audio_bitrate_kbps else ""
        out.write(f"{'Verified':<11}: audio={info.audio_codec}{rate}, "
                  f"{format_duration(int(info.duration_seconds or 0))}\n")
    elif info is not None:
        out.write(
            f"{'Verified':<11}: {info.width}x{info.height} "
            f"video={info.video_codec} audio={info.audio_codec or 'none'}\n"
        )


# --------------------------------------------------------------------------- #
# Interactive input
# --------------------------------------------------------------------------- #
def _prompt(question: str, input_func: Callable[[str], str]) -> str:
    try:
        return input_func(question).strip()
    except EOFError:
        return ""


def _interactive_inputs(
    args: argparse.Namespace, default_resolution: str, input_func: Callable[[str], str]
) -> tuple[str, str, MediaType]:
    url = _prompt("Enter YouTube URL: ", input_func)
    resolution = args.resolution
    media_type = MediaType.parse(args.media_type) if args.media_type else None
    if args.info or args.list_formats:
        return url, resolution or default_resolution, media_type or MediaType.VIDEO
    if media_type is None:
        answer = _prompt("Download type (video/audio) [video]: ", input_func)
        media_type = MediaType.parse(answer or "video")
    if media_type is MediaType.VIDEO and resolution is None:
        options = "/".join(sorted(supported_resolution_labels(), key=lambda s: int(s[:-1])))
        answer = _prompt(f"Enter resolution ({options}) [{default_resolution}]: ", input_func)
        resolution = normalize_resolution_label(answer) if answer else default_resolution
    return url, resolution or default_resolution, media_type


# --------------------------------------------------------------------------- #
# --doctor / --update
# --------------------------------------------------------------------------- #
def _tool_version(command: list[str]) -> str | None:
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    lines = (done.stdout or done.stderr).strip().splitlines()
    return lines[0] if lines else "installed"


def run_doctor(config: AppConfig, out: TextIO) -> int:
    """Print the status of every runtime dependency; 0 when all are present."""
    import shutil  # noqa: PLC0415 - only needed here

    checks: list[tuple[str, str | None, str]] = []
    checks.append((
        "Python",
        f"{platform.python_version()} ({platform.system()} {platform.machine()})"
        if sys.version_info >= (3, 10) else None,
        "Python 3.10+ is required",
    ))
    try:
        import yt_dlp.version  # noqa: PLC0415

        ytdlp = yt_dlp.version.__version__
    except ImportError:
        ytdlp = None
    checks.append(("yt-dlp", ytdlp, "run the installer or: ytd --update"))
    ffmpeg = FFmpegManager(config.ffmpeg_path)
    ffmpeg_path = ffmpeg.find_ffmpeg()
    checks.append(("FFmpeg", f"{ffmpeg.version} [{ffmpeg_path}]" if ffmpeg_path else None,
                   ffmpeg.problem or "install FFmpeg (the installer does this)"))
    ffprobe_problem = ffmpeg.ffprobe_problem()
    checks.append(("ffprobe", str(ffmpeg.find_ffprobe()) if not ffprobe_problem else None,
                   ffprobe_problem if ffprobe_problem != "not found" else "ships with FFmpeg"))
    deno = shutil.which("deno")
    deno_version = _tool_version([deno, "--version"]) if deno else None
    checks.append(("Deno", f"{deno_version} [{deno}]" if deno_version else None,
                   "needed by yt-dlp for YouTube (the installer does this)"))

    missing = 0
    for name, value, hint in checks:
        if value:
            out.write(f"  OK       {name:<8} {value}\n")
        else:
            missing += 1
            out.write(f"  MISSING  {name:<8} -> {hint}\n")
    out.write("\nAll dependencies found.\n" if not missing else f"\n{missing} problem(s) found.\n")
    return EXIT_OK if not missing else 1


def run_update(out: TextIO) -> int:
    """Upgrade yt-dlp (and Deno when it was installed with pip) in this environment."""
    command = [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp[default]"]
    try:
        from importlib.metadata import PackageNotFoundError, version  # noqa: PLC0415

        version("deno")
        command.append("deno")
    except PackageNotFoundError:
        pass
    out.write("Running: " + " ".join(command) + "\n")
    out.flush()
    return subprocess.run(command, check=False).returncode


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main(
    argv: Sequence[str] | None = None,
    *,
    downloader_factory: DownloaderFactory | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    stdin_is_tty: bool | None = None,
    input_func: Callable[[str], str] = input,
) -> int:
    """Run the CLI and return a process exit code."""
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    raw_args = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(normalize_argv(raw_args))
    if args.url and args.url_arg and args.url != args.url_arg:
        err.write("Error: give the URL once (either positionally or with --url).\n")
        return EXIT_USAGE
    args.url = args.url or args.url_arg
    ensure_interpreter_bin_on_path()  # finds deno/ffmpeg installed in the venv

    level = "DEBUG" if args.verbose else (args.log_level or "ERROR")
    configure_logging(level, args.log_file)

    try:
        config = load_config(args.config)
        config = config.with_overrides(
            output_dir=args.output.expanduser().resolve() if args.output else None,
            ffmpeg_path=args.ffmpeg_path,
            fallback=FallbackPolicy.ERROR if args.no_fallback else None,
        )

        if args.doctor:
            return run_doctor(config, out)
        if args.update:
            return run_update(out)

        interactive = sys.stdin.isatty() if stdin_is_tty is None else stdin_is_tty
        if args.url:
            url = args.url
            resolution = args.resolution or config.default_resolution
            media_type = MediaType.parse(args.media_type or "video")
        elif interactive:
            url, resolution, media_type = _interactive_inputs(
                args, config.default_resolution, input_func
            )
        else:
            err.write("Error: a URL is required when not running interactively. "
                      'Usage: ytd "<YOUTUBE_URL>"\n')
            return EXIT_USAGE
        if media_type is MediaType.AUDIO and args.resolution:
            err.write("Note: --resolution is ignored for audio downloads.\n")

        downloader = (downloader_factory or YouTubeDownloader)(config)

        playlist_mode = args.playlist
        if not playlist_mode and not args.list_formats:
            try:
                validate_playlist_url(url)
                has_list = True
            except InvalidURLError:
                has_list = False
            try:
                validate_youtube_url(url)
                has_video = True
            except InvalidURLError:
                has_video = False  # reported properly by plan() below
            if has_list and interactive and not args.url:
                question = ("This video is part of a playlist. Download the whole playlist? [y/N]: "
                            if has_video else "This is a playlist link. Download the playlist? [Y/n]: ")
                answer = _prompt(question, input_func).lower()
                playlist_mode = answer in {"y", "yes"} or (not has_video and answer in {"", "y", "yes"})
            elif has_list and has_video:
                out.write("Note: this link is part of a playlist - downloading only this video. "
                          "Add --list to download the whole playlist.\n\n")
        if playlist_mode:
            if args.list_formats:
                err.write("Error: --list-formats works with a single video, not with --list.\n")
                return EXIT_USAGE
            return run_playlist(downloader, url, resolution, media_type, args, out, err)
        if args.limit:
            err.write("Note: --limit only applies together with --list.\n")

        if args.list_formats:
            _, metadata = downloader.fetch_metadata(url)
            _print_formats(metadata, out)
            return EXIT_OK

        plan = downloader.plan(url, resolution, media_type)
        _print_plan(plan, out)
        if args.info:
            return EXIT_OK

        out.write("\nDownloading...\n")
        out.flush()
        result = downloader.execute(plan, ProgressPrinter(out))
        _print_result(result, out)
        return EXIT_OK

    except (UnsupportedResolutionError, ConfigurationError) as exc:
        err.write(f"\nError: {exc}\n")
        return exc.exit_code
    except YouTubeDownloadError as exc:
        logger.error("%s (exit_code=%d)", exc, exc.exit_code, extra={"console": False})
        err.write(f"\nError: {exc}\n")
        return exc.exit_code
    except KeyboardInterrupt:
        logger.warning("Cancelled by user", extra={"console": False})
        err.write("\nCancelled by user. Partial data (if any) is kept so the download can resume.\n")
        return EXIT_INTERRUPTED


# --------------------------------------------------------------------------- #
# Playlists
# --------------------------------------------------------------------------- #
#: Errors that would fail every remaining item the same way: stop the batch.
_FATAL_FOR_BATCH = (FFmpegNotFoundError, DependencyMissingError, OutputPathError, ConfigurationError)


def _print_playlist_header(info: PlaylistInfo, media_type: MediaType, resolution: str,
                           output_dir: Path, out: TextIO) -> None:
    kind = (f"Audio only ({AUDIO_OUTPUT_CODEC.upper()} {AUDIO_BITRATE_KBPS} kbps)"
            if media_type is MediaType.AUDIO else f"Video ({resolution})")
    rows = [
        ("Playlist", info.title),
        ("Items", f"{len(info.entries)}"
                  + (f" ({info.unavailable} private/deleted skipped)" if info.unavailable else "")),
        ("Type", kind),
        ("Output dir", str(output_dir)),
    ]
    for key, value in rows:
        out.write(f"{key:<11}: {value}\n")
    if info.playlist.is_mix:
        out.write(f"\nNote: this is a YouTube Mix, which never ends. Downloading the first "
                  f"{len(info.entries)} items (use --limit N to change).\n")


def _short_plan(plan: DownloadPlan) -> str:
    sel = plan.selection
    size = format_bytes(sel.estimated_size)
    if sel.is_audio_only:
        return f"{plan.requested_label} | {plan.metadata.duration_display} | ~{size}"
    text = f"{sel.selected_label} {sel.container.upper()} | {plan.metadata.duration_display} | ~{size}"
    if sel.is_fallback:
        text += f"  (WARNING: {plan.requested_label} not available, using {sel.selected_label})"
    return text


def run_playlist(downloader: YouTubeDownloader, url: str, resolution: str,
                 media_type: MediaType, args: argparse.Namespace,
                 out: TextIO, err: TextIO) -> int:
    """Download every item of a playlist into its own folder, continuing past failures."""
    info = downloader.fetch_playlist(url, args.limit)
    folder = Path(downloader.config.output_dir) / info.folder_name
    _print_playlist_header(info, media_type, resolution, folder, out)

    if args.info:
        out.write("\n")
        for entry in info.entries:
            out.write(f"  {info.name_prefix(entry)}{entry.title or entry.video_id}\n")
        return EXIT_OK

    downloaded, skipped, failed = 0, 0, []
    total = len(info.entries)
    for entry in info.entries:
        out.write(f"\n[{entry.index}/{total}] {entry.title or entry.video_id}\n")
        out.flush()
        try:
            plan = downloader.plan(entry.url, resolution, media_type, output_dir=folder)
            out.write(f"  {_short_plan(plan)}\n")
            result = downloader.execute(plan, ProgressPrinter(out),
                                        name_prefix=info.name_prefix(entry), skip_existing=True)
        except _FATAL_FOR_BATCH as exc:
            err.write(f"\nError: {exc}\nStopping the playlist download.\n")
            return exc.exit_code
        except YouTubeDownloadError as exc:
            logger.error("Playlist item %d failed: %s", entry.index, exc, extra={"console": False})
            out.write(f"  FAILED: {str(exc).splitlines()[0]}\n")
            failed.append((entry, str(exc).splitlines()[0]))
            continue
        if result.skipped:
            skipped += 1
            out.write(f"  Already downloaded, skipped: {result.path.name}\n")
        else:
            downloaded += 1
            out.write(f"  Saved: {result.path.name}\n")

    out.write(f"\nPlaylist finished: {downloaded} downloaded, {skipped} already present, "
              f"{len(failed)} failed.\nFolder: {folder}\n")
    for entry, reason in failed:
        out.write(f"  failed {info.name_prefix(entry)}{entry.title or entry.video_id}: {reason}\n")
    if failed:
        out.write("Re-run the same command to retry; finished items are skipped.\n")
    if failed and not (downloaded or skipped):
        return DownloadFailedError.exit_code
    return EXIT_PLAYLIST_PARTIAL if failed else EXIT_OK


def main_entry() -> None:
    """Console-script entry point (``ytd``) for installed use."""
    sys.exit(main())
