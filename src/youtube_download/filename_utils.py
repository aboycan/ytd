"""Safe, cross-platform output filenames.

Titles come from the internet and are untrusted. They are turned into a single
path component that is valid on Windows, macOS and Linux, can never contain a
directory separator or ``..`` traversal, and never overwrites an existing file.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from .exceptions import OutputPathError

#: Characters invalid on Windows (a superset of macOS/Linux restrictions).
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_WHITESPACE = re.compile(r"\s+")
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
#: Most filesystems limit a single name to 255 *bytes*.
MAX_NAME_BYTES = 255
DEFAULT_STEM = "video"


def _truncate_utf8(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip()


def sanitize_filename(title: str | None, max_bytes: int = 180) -> str:
    """Return ``title`` converted to a safe filename stem (no extension).

    - Unicode is kept (NFC-normalized), so non-Latin titles stay readable.
    - ``< > : " / \\ | ? *`` and control characters become ``_``.
    - Whitespace is collapsed; leading/trailing dots and spaces are removed
      (Windows forbids trailing ones; leading dots would create hidden files).
    - Windows reserved device names (``CON``, ``NUL``, ``COM1`` ...) are prefixed.
    - The result is limited to ``max_bytes`` UTF-8 bytes, leaving room for the
      ``" [1080p] (2).mp4"`` suffix within the 255-byte filesystem limit.
    """
    text = unicodedata.normalize("NFC", title or "")
    text = _WHITESPACE.sub(" ", text)  # tabs/newlines become plain spaces
    text = _INVALID_CHARS.sub("_", text).strip(" .")
    text = re.sub(r"_{2,}", "_", text)
    text = _truncate_utf8(text, max_bytes).strip(" .")
    if not text or set(text) <= {"_"}:
        return DEFAULT_STEM
    if text.split(".")[0].upper() in _WINDOWS_RESERVED:
        text = f"_{text}"
    return text


def ensure_within_directory(path: Path, directory: Path) -> Path:
    """Return ``path`` resolved, refusing anything outside ``directory``."""
    resolved_dir = directory.resolve()
    resolved = path.resolve()
    if resolved != resolved_dir and resolved_dir not in resolved.parents:
        raise OutputPathError(f"Refusing to write outside the output directory: {path}")
    return resolved


def build_output_path(
    output_dir: Path,
    title: str | None,
    resolution_label: str,
    extension: str,
    prefix: str | None = None,
    reuse_existing: bool = False,
) -> Path:
    """Return a non-existing path like ``<dir>/<Title> [1080p].mp4``.

    If the file already exists a counter is appended (``... [1080p] (1).mp4``)
    so an earlier download is never overwritten. An interrupted download
    leaves no final file, so the same name is chosen again and the backend
    can resume its partial data.

    ``prefix`` (e.g. ``"03 - "`` for playlist items) is prepended to the name.
    With ``reuse_existing=True`` an existing file is returned instead of a new
    numbered name, so callers can skip items that were already downloaded.
    """
    ext = re.sub(r"[^A-Za-z0-9]", "", extension or "") or "mp4"
    label = re.sub(r"[^A-Za-z0-9]", "", resolution_label or "") or "video"
    safe_prefix = re.sub(r"[^0-9A-Za-z _-]", "", prefix or "")
    stem = f"{safe_prefix}{sanitize_filename(title)} [{label}]"
    candidate = output_dir / f"{stem}.{ext}"
    if reuse_existing and candidate.exists():
        return ensure_within_directory(candidate, output_dir)
    counter = 1
    while candidate.exists():
        candidate = output_dir / f"{stem} ({counter}).{ext}"
        counter += 1
        if counter > 9999:
            raise OutputPathError("Could not find a free output filename.")
    return ensure_within_directory(candidate, output_dir)

