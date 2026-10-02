"""Portable first-run setup for the universal ytd zip.

Run automatically by the ``ytd`` / ``ytd.cmd`` launchers (via bootstrap.sh /
bootstrap.ps1, which first make sure some Python 3.10+ is available). It sets
up everything *inside the extracted folder*; nothing is installed system-wide
and no administrator rights are needed:

    runtime/<os>-<arch>/venv   Python environment with yt-dlp (+ Deno via pip)
    runtime/<os>-<arch>/bin    ffmpeg, ffprobe, deno (only when not already on PATH)
    runtime/<os>-<arch>/.ready marker written when setup completed

Every download is verified against a published SHA-256 checksum before use.
Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import venv
import zipfile
from pathlib import Path

VERSION = "1.2.1"
IS_WINDOWS = os.name == "nt"
EXE = ".exe" if IS_WINDOWS else ""

# Download sources (override with environment variables for mirrors/tests).
GITHUB_API = os.environ.get("YTD_GITHUB_API", "https://api.github.com")
FFMPEG_RELEASE = os.environ.get(
    "YTD_FFMPEG_RELEASE", "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest"
)
GYAN_BUILDS = os.environ.get("YTD_GYAN_BUILDS", "https://www.gyan.dev/ffmpeg/builds")
DENO_RELEASE = os.environ.get("YTD_DENO_RELEASE", "https://github.com/denoland/deno/releases")


# ------------------------------------------------------------------ helpers --
def log(message: str) -> None:
    print(f"==> {message}", flush=True)


def ok(message: str) -> None:
    print(f"    OK {message}", flush=True)


def warn(message: str) -> None:
    print(f"    WARNING: {message}", flush=True)


class SetupError(Exception):
    pass


def machine() -> tuple[str, str]:
    """Return ``(os, arch)`` like ``("macos", "arm64")``."""
    system = platform.system().lower()
    os_name = {"darwin": "macos", "linux": "linux", "windows": "windows"}.get(system, system)
    arch_raw = platform.machine().lower()
    if IS_WINDOWS:
        arch_raw = (os.environ.get("PROCESSOR_ARCHITEW6432")
                    or os.environ.get("PROCESSOR_ARCHITECTURE") or arch_raw).lower()
    arch = "arm64" if arch_raw in {"arm64", "aarch64"} else "x64"
    return os_name, arch


def fetch(url: str, dest: Path) -> Path:
    """Download ``url`` to ``dest`` (curl when available: it uses the OS trust store)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    curl = shutil.which("curl")
    if curl:
        result = subprocess.run(
            [curl, "-fsSL", "--retry", "3", "--connect-timeout", "30", "-A", f"ytd/{VERSION}",
             "-o", str(dest), url],
            check=False,
        )
        if result.returncode == 0:
            return dest
    request = urllib.request.Request(url, headers={"User-Agent": f"ytd/{VERSION}"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, open(dest, "wb") as fh:
            shutil.copyfileobj(response, fh)
    except OSError as exc:
        raise SetupError(f"Download failed: {url} ({exc})") from exc
    return dest


def fetch_text(url: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        return fetch(url, Path(tmp) / "data").read_text(encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(path: Path, expected: str) -> None:
    actual = sha256(path)
    if actual.lower() != expected.lower():
        raise SetupError(f"Checksum mismatch for {path.name} (possible corrupted or tampered download).")
    ok(f"checksum verified: {path.name}")


def checksum_from_list(text: str, filename: str) -> str:
    """Find ``filename`` in a ``<sha256>  <name>`` checksum listing.

    A file holding just one bare hash (gyan.dev's ``*.sha256``) is accepted too.
    """
    bare = text.strip().split()
    if len(bare) == 1 and re.fullmatch(r"[0-9a-fA-F]{64}", bare[0]):
        return bare[0]
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[-1].lstrip("*").split("/")[-1] == filename:
            return parts[0]
    raise SetupError(f"No published checksum found for {filename}.")


def extract(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    name = archive.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                target = (dest / member.filename).resolve()
                if dest.resolve() not in target.parents and target != dest.resolve():
                    raise SetupError("Unsafe path in archive.")
                zf.extract(member, dest)
                mode = member.external_attr >> 16
                if mode:
                    os.chmod(target, mode | 0o400)
    else:
        with tarfile.open(archive) as tf:
            for member in tf.getmembers():
                target = (dest / member.name).resolve()
                if dest.resolve() not in target.parents and target != dest.resolve():
                    raise SetupError("Unsafe path in archive.")
                if member.issym() or member.islnk():
                    link = (target.parent / member.linkname).resolve()
                    if dest.resolve() not in link.parents:
                        raise SetupError("Unsafe link in archive.")
            if hasattr(tarfile, "data_filter"):  # Python 3.10.12+: safe extraction filter
                tf.extractall(dest, filter="data")
            else:  # members validated above
                tf.extractall(dest)


def find_file(root: Path, filename: str) -> Path | None:
    for path in root.rglob(filename):
        if path.is_file():
            return path
    return None


def install_binary(source: Path, bin_dir: Path, name: str) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    target = bin_dir / f"{name}{EXE}"
    shutil.copy2(source, target)
    target.chmod(target.stat().st_mode | 0o755)
    return target


def runs(command: list[str]) -> bool:
    try:
        return subprocess.run(command, capture_output=True, timeout=60, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# --------------------------------------------------------------- components --
def setup_venv(runtime: Path, requirements: Path) -> Path:
    venv_dir = runtime / "venv"
    python = venv_dir / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
    if not (python.exists() and runs([str(python), "-c", "import sys"])):
        log(f"Creating Python environment ({sys.version.split()[0]})")
        if venv_dir.exists():
            shutil.rmtree(venv_dir)
        venv.EnvBuilder(with_pip=True, clear=True, symlinks=not IS_WINDOWS).create(venv_dir)
    log("Installing yt-dlp")
    # --no-cache-dir: a pip cache written by another pip version only produces
    # noisy "Cache entry deserialization failed" warnings during this one-time setup.
    pip = [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--quiet",
           "--no-cache-dir", "--upgrade"]
    if subprocess.run([*pip, "pip"], check=False).returncode != 0:
        warn("could not upgrade pip; continuing with the bundled version")
    if subprocess.run([*pip, "-r", str(requirements)], check=False).returncode != 0:
        # Some platforms (e.g. Windows on ARM) lack ready-made wheels for yt-dlp's
        # optional speed-ups; the core package + its YouTube JS helper are pure Python.
        warn("full yt-dlp install failed; retrying with the core package only")
        if subprocess.run([*pip, "yt-dlp", "yt-dlp-ejs"], check=False).returncode != 0:
            raise SetupError("Installing yt-dlp failed (check your internet connection).")
    ok("yt-dlp installed")
    return python


def _tool_starts(executable: Path) -> bool:
    """True when the program actually starts (not blocked by Smart App Control/antivirus)."""
    try:
        return subprocess.run([str(executable), "-version"], capture_output=True,
                              timeout=300, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _remove_local_ffmpeg(bin_dir: Path) -> None:
    for tool in ("ffmpeg", "ffprobe"):
        (bin_dir / f"{tool}{EXE}").unlink(missing_ok=True)


def _ffmpeg_sources(os_name: str, arch: str) -> list[tuple[str, str, str]]:
    """(archive URL, checksum URL, archive file name) to try, best first.

    Windows prefers gyan.dev's *release* build: it is the most widely used
    Windows FFmpeg, so Windows Smart App Control recognises it far more often
    than a daily build (x64; runs on ARM64 Windows through emulation).
    """
    sources: list[tuple[str, str, str]] = []
    if os_name == "windows":
        name = "ffmpeg-release-essentials.zip"
        sources.append((f"{GYAN_BUILDS}/{name}", f"{GYAN_BUILDS}/{name}.sha256", name))
    if os_name in {"linux", "windows"}:
        target = {("linux", "x64"): "linux64", ("linux", "arm64"): "linuxarm64",
                  ("windows", "x64"): "win64", ("windows", "arm64"): "winarm64"}[(os_name, arch)]
        ext = "zip" if os_name == "windows" else "tar.xz"
        name = f"ffmpeg-master-latest-{target}-lgpl.{ext}"
        sources.append((f"{FFMPEG_RELEASE}/{name}", f"{FFMPEG_RELEASE}/checksums.sha256", name))
    return sources


def setup_ffmpeg(runtime: Path, os_name: str, arch: str, python: Path) -> None:
    bin_dir = runtime / "bin"
    local = bin_dir / f"ffmpeg{EXE}"
    if local.exists() and (bin_dir / f"ffprobe{EXE}").exists():
        if _tool_starts(local):
            ok(f"FFmpeg: {local}")
            return
        warn("the FFmpeg in this folder is blocked by Windows/antivirus; replacing it")
        _remove_local_ffmpeg(bin_dir)
    # YTD_FORCE_PORTABLE_FFMPEG=1 ignores an installed FFmpeg (used by CI to test downloads).
    if os.environ.get("YTD_FORCE_PORTABLE_FFMPEG") != "1":
        system = shutil.which("ffmpeg")
        if system and shutil.which("ffprobe") and _tool_starts(Path(system)):
            ok(f"FFmpeg: {system}")
            return
    log("Setting up FFmpeg (portable, inside this folder)")
    blocked = False
    for url, sums_url, name in _ffmpeg_sources(os_name, arch):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                archive = fetch(url, Path(tmp) / name)
                verify(archive, checksum_from_list(fetch_text(sums_url), name))
                extract(archive, Path(tmp) / "x")
                for tool in ("ffmpeg", "ffprobe"):
                    found = find_file(Path(tmp) / "x", f"{tool}{EXE}")
                    if not found:
                        raise SetupError(f"{tool} missing from {name}")
                    install_binary(found, bin_dir, tool)
            log(f"Checking ffmpeg{EXE} (the first start can take a while during a virus scan)")
            if not _tool_starts(local):
                blocked = True
                _remove_local_ffmpeg(bin_dir)
                raise SetupError(f"Windows or antivirus software blocked the FFmpeg from {name}")
            ok(f"FFmpeg installed to {bin_dir}")
            return
        except SetupError as exc:
            warn(f"{str(exc).rstrip('.')}. Trying the next option.")
    if os_name == "macos" and shutil.which("brew"):
        log("Installing FFmpeg with Homebrew")
        if subprocess.run(["brew", "install", "ffmpeg"], check=False).returncode == 0:
            ok("FFmpeg installed (Homebrew)")
            return
    # Last resort: FFmpeg binary shipped in the imageio-ffmpeg wheel (no ffprobe;
    # output validation is then skipped, downloads still work).
    log("Installing FFmpeg from the imageio-ffmpeg package")
    if subprocess.run([str(python), "-m", "pip", "install", "--quiet", "imageio-ffmpeg"],
                      check=False).returncode == 0:
        exe = subprocess.run(
            [str(python), "-c", "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"],
            capture_output=True, text=True, check=False,
        ).stdout.strip()
        if exe and Path(exe).exists():
            install_binary(Path(exe), bin_dir, "ffmpeg")
            if _tool_starts(local):
                warn("ffprobe is not available in this FFmpeg build; file verification will be skipped")
                return
            blocked = True
            _remove_local_ffmpeg(bin_dir)
    if blocked and os_name == "windows":
        raise SetupError(
            "Windows blocked every downloaded FFmpeg (Smart App Control or antivirus).\n"
            "       Install FFmpeg through Windows instead, then run 'ytd.cmd --setup':\n"
            "           winget install Gyan.FFmpeg\n"
            "       (open a NEW terminal window afterwards)."
        )
    raise SetupError("Could not set up FFmpeg automatically.")


def setup_deno(runtime: Path, os_name: str, arch: str, python: Path) -> None:
    venv_bin = python.parent
    local = [venv_bin / f"deno{EXE}", runtime / "bin" / f"deno{EXE}"]
    if shutil.which("deno") or any(path.exists() for path in local):
        ok("Deno available")
        return
    log("Installing Deno (JavaScript runtime needed by yt-dlp for YouTube)")
    if subprocess.run([str(python), "-m", "pip", "install", "--quiet", "deno"],
                      check=False).returncode == 0 and (venv_bin / f"deno{EXE}").exists():
        ok("Deno installed into the Python environment")
        return
    triple = {("macos", "x64"): "x86_64-apple-darwin", ("macos", "arm64"): "aarch64-apple-darwin",
              ("linux", "x64"): "x86_64-unknown-linux-gnu",
              ("linux", "arm64"): "aarch64-unknown-linux-gnu",
              ("windows", "x64"): "x86_64-pc-windows-msvc",
              ("windows", "arm64"): "x86_64-pc-windows-msvc"}[(os_name, arch)]  # x64 runs emulated
    name = f"deno-{triple}.zip"
    with tempfile.TemporaryDirectory() as tmp:
        archive = fetch(f"{DENO_RELEASE}/latest/download/{name}", Path(tmp) / name)
        expected = fetch_text(f"{DENO_RELEASE}/latest/download/{name}.sha256sum").split()[0]
        verify(archive, expected)
        extract(archive, Path(tmp) / "x")
        found = find_file(Path(tmp) / "x", f"deno{EXE}")
        if not found:
            raise SetupError("deno missing from the downloaded archive")
        install_binary(found, runtime / "bin", "deno")
    ok("Deno installed")


# --------------------------------------------------------------------- main --
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Set up the portable ytd runtime.")
    parser.add_argument("--root", type=Path, required=True, help="Extracted ytd folder")
    parser.add_argument("--runtime", type=Path, required=True, help="runtime/<os>-<arch> folder")
    args = parser.parse_args(argv)

    os_name, arch = machine()
    runtime = args.runtime.resolve()
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / ".ready").unlink(missing_ok=True)
    log(f"Setting up ytd {VERSION} for {os_name} ({arch}) in {runtime}")
    try:
        python = setup_venv(runtime, args.root / "setup" / "requirements.txt")
        setup_ffmpeg(runtime, os_name, arch, python)
        setup_deno(runtime, os_name, arch, python)
    except SetupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nSetup cancelled.", file=sys.stderr)
        return 130
    (runtime / ".ready").write_text(json.dumps({"version": VERSION}), encoding="utf-8")
    log("Setup complete.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
