"""Logging setup built on the standard :mod:`logging` module.

Log records use a ``key=value`` friendly single-line format. A redaction
filter strips query strings from any URL that appears in a log message, so
signed media URLs, tokens or cookies passed in URLs are never written to logs.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

PACKAGE_LOGGER = "youtube_download"
LOG_FORMAT = "%(asctime)s level=%(levelname)s logger=%(name)s msg=%(message)s"
_URL_QUERY = re.compile(r"(https?://[^\s?#'\"]+)\?[^\s'\"]*")

LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


def redact(text: str) -> str:
    """Remove query strings from URLs, keeping YouTube ``watch?v=`` ids."""

    def _replace(match: re.Match[str]) -> str:
        base = match.group(1)
        query = match.group(0)[len(base) + 1:]
        video = re.match(r"v=([A-Za-z0-9_-]{11})", query)
        if base.rstrip("/").endswith("youtube.com/watch") and video:
            return f"{base}?v={video.group(1)}"
        return f"{base}?<redacted>"

    return _URL_QUERY.sub(_replace, text)


class RedactingFilter(logging.Filter):
    """Applies :func:`redact` to every formatted log message."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - malformed record, leave untouched
            return True
        record.msg = redact(message)
        record.args = None
        return True


class ConsoleSuppressFilter(logging.Filter):
    """Drops records logged with ``extra={"console": False}``.

    Used for errors the CLI already shows to the user in plain language: they
    still reach the log file, but are not printed twice on the console.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return getattr(record, "console", True)


def configure_logging(
    level: str | int = "WARNING",
    log_file: Path | None = None,
) -> logging.Logger:
    """Configure the package logger and return it.

    Console logs go to stderr (keeping stdout for user-facing output). An
    optional ``log_file`` always records at DEBUG level. Safe to call more than
    once: previously installed handlers are replaced.
    """
    numeric = LEVELS.get(str(level).upper(), level) if isinstance(level, str) else level
    if not isinstance(numeric, int):
        raise ValueError(f"Unknown log level: {level}")

    logger = logging.getLogger(PACKAGE_LOGGER)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    formatter = logging.Formatter(LOG_FORMAT)
    redactor = RedactingFilter()

    console = logging.StreamHandler()
    console.setLevel(numeric)
    console.setFormatter(formatter)
    console.addFilter(ConsoleSuppressFilter())
    console.addFilter(redactor)
    logger.addHandler(console)

    if log_file is not None:
        log_file = Path(log_file).expanduser()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(redactor)
        logger.addHandler(file_handler)

    return logger
