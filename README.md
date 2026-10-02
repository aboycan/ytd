# ytd — YouTube Media Downloader

`ytd` is a lightweight, cross-platform command-line utility for downloading YouTube video or audio content to a local folder.

It is designed for **personal and legitimate use**. The project does not host or distribute downloaded media.

> **Responsible use:** Only download content that you own, have permission to download, is explicitly made available for download, or that you are otherwise legally permitted to download. Users are responsible for complying with YouTube's Terms of Service, applicable copyright laws, and the rights of content creators.
>
> `ytd` uses the normal functionality provided by `yt-dlp` and does not intentionally bypass DRM, authentication, access controls, paid-content restrictions, or platform anti-bot mechanisms.

---

## Features

- Video downloads at **1080p** by default, with optional **720p** selection.
- Audio-only downloads as **MP3 320 kbps**.
- Uses FFmpeg to merge separate video/audio streams and perform audio conversion.
- Supports individual videos, Shorts, and playlist/Mix URLs.
- A playlist URL downloads only the referenced video unless `--list` is specified.
- Playlist downloads can be limited with `--limit`.
- Automatic fallback to a lower available resolution, or strict failure with `--no-fallback`.
- `--info` previews metadata and the selected format without downloading.
- `--list-formats` shows available media formats without downloading.
- `--doctor` checks the required runtime dependencies.
- `--update` updates `yt-dlp` and Deno used by the local runtime.
- Safe filenames and output-path validation.
- Existing files are not overwritten.
- Interrupted downloads can be resumed by running the same command again.
- Clear errors, logging, and exit codes.
- Logs redact signed URLs and tokens.
- Portable setup for Windows, macOS, and Linux.

---

## Supported Platforms

| Platform | Architecture | Status |
|---|---|---|
| Windows 10 / 11 | x64 | Supported |
| Windows 10 / 11 | ARM64 | Supported |
| macOS 12+ | x64 | Supported |
| macOS 12+ | ARM64 / Apple Silicon | Supported |
| Linux | x64 | Supported |
| Linux | ARM64 | Supported |

Internet access is required for the initial runtime setup and for downloading media.

---

## Quick Start

### Windows

The simplest option is to double-click:

```text
Start ytd.cmd
```

Or use PowerShell / Command Prompt:

```powershell
cd path\to\ytd-1.2.1
.\ytd.cmd "https://www.youtube.com/watch?v=XXXXXXXXXXX"
```

### macOS / Linux

```bash
cd path/to/ytd-1.2.1
./ytd "https://www.youtube.com/watch?v=XXXXXXXXXXX"
```

If the shell reports `permission denied`, run:

```bash
sh ytd "https://www.youtube.com/watch?v=XXXXXXXXXXX"
```

The first run prepares a local runtime under the project folder. Subsequent runs reuse it.

---

## First-Run Setup

`ytd` is designed as a portable application. The launcher automatically prepares the required runtime when it is first executed.

Depending on the platform and what is already installed, the setup may provide:

- Python
- `yt-dlp`
- FFmpeg / `ffprobe`
- Deno

The runtime is kept under:

```text
runtime/<platform>-<architecture>/
```

The setup scripts verify downloaded runtime components using SHA-256 checksums where applicable.

No downloaded YouTube media is included with the application.

To explicitly redo the setup:

```bash
./ytd --setup
```

Windows:

```powershell
.\ytd.cmd --setup
```

---

## Usage

### Download a video

```bash
ytd "https://www.youtube.com/watch?v=XXXXXXXXXXX"
```

The default target is 1080p.

### Download 720p video

```bash
ytd "https://www.youtube.com/watch?v=XXXXXXXXXXX" --resolution 720p
```

Short form:

```bash
ytd "https://www.youtube.com/watch?v=XXXXXXXXXXX" -r 720p
```

### Download audio only

```bash
ytd "https://www.youtube.com/watch?v=XXXXXXXXXXX" --type audio
```

This produces a single MP3 file targeted at 320 kbps.

### Preview without downloading

```bash
ytd "https://www.youtube.com/watch?v=XXXXXXXXXXX" --info
```

### List available formats

```bash
ytd "https://www.youtube.com/watch?v=XXXXXXXXXXX" --list-formats
```

### Download a playlist

Without `--list`, a URL containing a playlist identifier downloads only the referenced video.

To download the playlist:

```bash
ytd "<playlist URL>" --list
```

Audio-only playlist:

```bash
ytd "<playlist URL>" --list --type audio
```

Limit the number of playlist items:

```bash
ytd "<playlist URL>" --list --limit 10
```

### Choose an output directory

```bash
ytd "https://www.youtube.com/watch?v=XXXXXXXXXXX" --output ./downloads
```

Short form:

```bash
ytd "https://www.youtube.com/watch?v=XXXXXXXXXXX" -o ./downloads
```

### Disable resolution fallback

By default, if the requested resolution is unavailable, `ytd` can use a lower available resolution.

To fail instead:

```bash
ytd "https://www.youtube.com/watch?v=XXXXXXXXXXX" --resolution 1080p --no-fallback
```

### Check the installation

```bash
ytd --doctor
```

### Update the local downloader runtime

```bash
ytd --update
```

### Debug a problem

```bash
ytd "https://www.youtube.com/watch?v=XXXXXXXXXXX" --verbose --log-file logs/debug.log
```

Always quote YouTube URLs, especially URLs containing `&list=...` or other query parameters.

---

## Command Reference

```text
ytd [-h]
    [--url URL]
    [--type {video,audio}]
    [--resolution 720p/1080p]
    [--list]
    [--limit N]
    [--output OUTPUT]
    [--ffmpeg-path FFMPEG_PATH]
    [--config CONFIG]
    [--no-fallback]
    [--info | --list-formats]
    [--verbose]
    [--log-level {DEBUG,INFO,WARNING,ERROR}]
    [--log-file LOG_FILE]
    [--doctor | --update]
    [--version]
    [URL]
```

| Option | Description |
|---|---|
| `URL` | YouTube video URL |
| `--url`, `-u` | Alternative way to provide the URL |
| `--type`, `-t` | `video` or `audio` |
| `--resolution`, `-r` | `720p` or `1080p` |
| `--list`, `-l` | Download the whole playlist |
| `--limit N` | Maximum number of playlist items |
| `--output`, `-o` | Output directory |
| `--ffmpeg-path` | FFmpeg executable or directory |
| `--config` | Path to a JSON configuration file |
| `--no-fallback` | Fail when the requested resolution is unavailable |
| `--info` | Display metadata and selected format without downloading |
| `--list-formats` | Display available media formats without downloading |
| `--verbose`, `-v` | Enable DEBUG logging |
| `--log-level` | Set console log level |
| `--log-file` | Write a DEBUG log to a file |
| `--doctor` | Check Python, `yt-dlp`, FFmpeg, and Deno |
| `--update` | Update `yt-dlp` and Deno |
| `--version` | Display the application version |

Run `ytd --help` for the authoritative command-line help for the installed version.

---

## Output

By default, downloaded media is stored under:

```text
downloads/
```

Example:

```text
downloads/
├── Example Video [1080p].mp4
├── Example Video [720p].mp4
└── Example Video [320kbps].mp3
```

The application sanitizes filenames for cross-platform compatibility and checks that the final output remains inside the configured output directory.

Existing files are not overwritten; a unique filename is generated when necessary.

The `downloads/` directory in this repository contains only a `.gitkeep` file. **Do not commit downloaded media to the repository.**

---

## Configuration

Default configuration is stored in:

```text
config/config.json
```

Current defaults:

```json
{
  "output_dir": "downloads",
  "default_resolution": "1080p",
  "fallback": "lower",
  "ffmpeg_path": null,
  "prefer_mp4": true,
  "retries": 3,
  "retry_backoff_seconds": 2.0,
  "socket_timeout_seconds": 30,
  "disk_space_margin": 0.10,
  "validate_output": true
}
```

Command-line options override configuration values where applicable.

Relative paths are resolved relative to the project directory.

---

## How Media Selection Works

For video downloads, `ytd` selects a stream at or below the requested resolution and pairs it with an appropriate audio stream. FFmpeg is used when the selected video and audio need to be merged.

The application prefers broadly compatible formats where possible and validates the resulting media with `ffprobe`.

If the requested resolution is unavailable, the default behavior is to select a lower available resolution and report the fallback. Use `--no-fallback` when a lower resolution is not acceptable.

For audio-only downloads, the best available source audio is converted to MP3 using FFmpeg.

> **Quality note:** Converting source audio to MP3 is a lossy conversion. Selecting 320 kbps for the MP3 output does not make the source audio lossless or increase the quality of the original source.

---

## Architecture

The project separates the command-line interface from the download and media-selection logic:

```text
src/youtube_download/
├── __main__.py
├── __init__.py
├── cli.py
├── config.py
├── downloader.py
├── exceptions.py
├── ffmpeg_manager.py
├── filename_utils.py
├── format_selector.py
├── logging_config.py
├── metadata.py
└── url_validator.py
```

The main responsibilities are:

- `cli.py` — command-line interface and argument handling
- `downloader.py` — download orchestration
- `format_selector.py` — media stream selection
- `url_validator.py` — URL validation and normalization
- `ffmpeg_manager.py` — FFmpeg/ffprobe integration
- `filename_utils.py` — safe filenames and output paths
- `config.py` — application configuration
- `metadata.py` — media metadata models and formatting
- `logging_config.py` — application logging and sensitive URL/token redaction
- `exceptions.py` — application-specific errors

---

## Dependencies

The project currently depends on:

- **Python 3.10+** — application runtime
- **yt-dlp** — media retrieval and YouTube interaction
- **FFmpeg / ffprobe** — media merging, conversion, and validation
- **Deno** — JavaScript runtime used by current `yt-dlp` YouTube support

Python dependency information is maintained in:

```text
setup/requirements.txt
```

Third-party software remains subject to its own licenses and terms. See the respective projects for their current licensing information.

---

## Privacy and Security

`ytd` is designed as a local utility.

It does not intentionally upload downloaded media to a project-controlled server or provide a project-controlled media-hosting service.

The repository should never contain:

- YouTube cookies
- Browser profiles
- Authentication tokens
- API keys
- Passwords
- Personal credentials
- Downloaded copyrighted media

If you add authentication-related functionality in the future, treat cookies and tokens as sensitive credentials and ensure they cannot be written to Git-tracked files.

---

## Responsible Use and Legal Notice

This software is provided as a general-purpose utility for personal and legitimate use.

Users are responsible for determining whether a particular download is permitted. This includes complying with:

- YouTube's Terms of Service
- Applicable copyright and intellectual-property laws
- The rights of copyright holders and content creators
- Any restrictions attached to the content being accessed

Only download content that you own, have permission to download, is explicitly made available for download, or that you are otherwise legally permitted to download.

This project does not host, distribute, or provide copyrighted video or audio content.

The project does not intentionally provide functionality to bypass DRM, authentication requirements, access controls, paid-content restrictions, or platform anti-bot mechanisms.

A repository disclaimer does not grant permission to download content where permission is otherwise required.

---

## Troubleshooting

For common setup and runtime problems, see:

```text
TROUBLESHOOTING.txt
```

The first diagnostic command to run is:

```bash
ytd --doctor
```

If a download fails after YouTube changes, try:

```bash
ytd --update
```

Then retry the download.

For detailed diagnostics:

```bash
ytd "<URL>" --verbose --log-file logs/debug.log
```

Do not publish debug logs containing private information, local paths you do not want to disclose, or authentication material.

---

## Updating

The portable runtime can be updated using:

```bash
ytd --update
```

If the application itself has been updated, replace the project folder with the new release and allow the first-run setup to prepare the corresponding runtime.

---

## Uninstall

Because the portable runtime is kept inside the project directory, removing the project folder removes the application runtime created by `ytd`.

If you used `--add-to-path`, also remove the corresponding PATH entry or launcher link created by that command.

System-wide Python, FFmpeg, or Deno installations are independent of the portable runtime and should only be removed separately if you no longer need them for other applications.

---

## Project Status

Current version:

```text
1.2.1
```

See `VERSION` for the release version used by the application.

---

## Contributing

Contributions and bug reports are welcome.

Before opening a pull request:

1. Keep changes focused.
2. Do not commit downloaded media.
3. Do not commit cookies, tokens, passwords, API keys, or browser profiles.
4. Update documentation when command-line behavior changes.
5. Verify that changes do not intentionally bypass DRM, authentication, access controls, or other platform protections.
6. Test the affected functionality on the relevant supported platform where possible.

---

## License

Add the project's chosen open-source license in a `LICENSE` file before publishing the repository.

The license for this project applies to **your source code**. It does not grant rights to third-party media downloaded using the software, and it does not replace the licenses of third-party dependencies.

---

## Acknowledgements

This project relies on open-source software, including `yt-dlp` and FFmpeg.

Please refer to the respective projects for their current documentation, licensing, and attribution requirements.
