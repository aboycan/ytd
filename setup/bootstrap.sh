#!/bin/sh
# First-run setup for macOS and Linux (called by ./ytd). No admin rights needed.
#   1. Find Python 3.10+ on this machine, or download a portable Python
#      (python-build-standalone, checksum-verified) into the runtime folder.
#   2. Run setup/bootstrap.py with it (yt-dlp, FFmpeg, Deno - all inside this folder).
# Usage: bootstrap.sh <ytd-root> <runtime-dir>
set -eu

ROOT="$1"
RUNTIME="$2"
GITHUB_API="${YTD_GITHUB_API:-https://api.github.com}"
PBS_REPO="astral-sh/python-build-standalone"
PY_SERIES="3.12"

say() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

python_ok() {  # interpreter >= 3.10 with venv + ensurepip
  "$1" -c 'import sys, venv, ensurepip; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    >/dev/null 2>&1
}

find_python() {
  for candidate in "$RUNTIME/python/bin/python3" python3 python3.14 python3.13 python3.12 \
                   python3.11 python3.10 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    path="$(command -v "$candidate" 2>/dev/null || true)"
    [ -n "$path" ] || continue
    case "$path" in "$RUNTIME/venv/"*) continue ;; esac     # never our own venv
    if python_ok "$path"; then
      # If this Python lives inside a virtual environment (e.g. an activated
      # .venv of another project), use the real installation behind it instead.
      "$path" -c 'import sys; print(getattr(sys, "_base_executable", "") if sys.prefix != sys.base_prefix and getattr(sys, "_base_executable", "") else sys.executable)'
      return 0
    fi
  done
  return 1
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

download_python() {
  case "$(uname -s)-$(uname -m)" in
    Darwin-arm64)             triple="aarch64-apple-darwin" ;;
    Darwin-x86_64)            triple="x86_64-apple-darwin" ;;
    Linux-x86_64|Linux-amd64) triple="x86_64-unknown-linux-gnu" ;;
    Linux-aarch64|Linux-arm64) triple="aarch64-unknown-linux-gnu" ;;
    *) die "No portable Python available for $(uname -s) $(uname -m). Please install Python 3.10+." ;;
  esac
  command -v curl >/dev/null 2>&1 || die "curl is required to download Python."
  say "Downloading a portable Python $PY_SERIES for $triple (one time, about 30 MB)"
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  curl -fsSL --retry 3 -A "ytd-setup" "$GITHUB_API/repos/$PBS_REPO/releases/latest" -o "$tmp/release.json" \
    || die "Could not reach GitHub to download Python."
  # Pick the newest $PY_SERIES "install_only_stripped" build for this platform.
  series_re="$(printf '%s' "$PY_SERIES" | sed 's/\./\\./g')"
  url="$(grep -Eo "\"browser_download_url\": *\"[^\"]*cpython-$series_re\.[0-9]+(\+|%2B)[0-9]+-$triple-install_only_stripped\.tar\.gz\"" \
          "$tmp/release.json" | sed 's/.*"\(http[^"]*\)"$/\1/' | sort -V | tail -n 1)"
  [ -n "$url" ] || die "No portable Python build found for $triple."
  sums_url="$(grep -o '"browser_download_url": *"[^"]*/SHA256SUMS"' "$tmp/release.json" \
               | sed 's/.*"\(http[^"]*\)"$/\1/' | head -n 1)"
  [ -n "$sums_url" ] || die "The Python release has no SHA256SUMS file; refusing to continue."
  file="${url##*/}"
  file_decoded="$(printf '%s' "$file" | sed 's/%2B/+/g')"
  curl -fsSL --retry 3 "$url" -o "$tmp/python.tar.gz" || die "Python download failed."
  curl -fsSL --retry 3 "$sums_url" -o "$tmp/SHA256SUMS" || die "Checksum download failed."
  expected="$(awk -v f="$file_decoded" '{n=$2; sub(/^\*/, "", n)} n == f {print $1}' "$tmp/SHA256SUMS" | head -n 1)"
  [ -n "$expected" ] || die "No checksum published for $file_decoded."
  [ "$(sha256_of "$tmp/python.tar.gz")" = "$expected" ] || die "Python download failed checksum verification."
  echo "    OK checksum verified: $file_decoded"
  rm -rf "$RUNTIME/python"
  mkdir -p "$RUNTIME"
  tar -xzf "$tmp/python.tar.gz" -C "$RUNTIME"   # creates $RUNTIME/python/
  rm -rf "$tmp"; trap - EXIT
  [ -x "$RUNTIME/python/bin/python3" ] || die "Portable Python is incomplete."
}

mkdir -p "$RUNTIME"
PYTHON=""
# YTD_FORCE_PORTABLE_PYTHON=1 skips any installed Python (used by CI to test the download path).
[ "${YTD_FORCE_PORTABLE_PYTHON:-0}" = "1" ] || PYTHON="$(find_python || true)"
if [ -z "$PYTHON" ]; then
  download_python
  PYTHON="$RUNTIME/python/bin/python3"
fi
echo "    OK Python: $("$PYTHON" --version 2>&1) ($PYTHON)"
exec "$PYTHON" "$ROOT/setup/bootstrap.py" --root "$ROOT" --runtime "$RUNTIME"
