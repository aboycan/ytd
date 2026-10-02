"""Central configuration: supported resolutions and application settings.

``SUPPORTED_RESOLUTIONS`` is the single source of truth for which resolutions
the utility accepts. To add a resolution (e.g. ``1440p``), add one entry here;
the CLI choices, validation and format selection all derive from this mapping.

Settings are resolved with the precedence::

    command-line argument  >  config file (config/config.json)  >  built-in default
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, fields, replace
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from .exceptions import ConfigurationError, UnsupportedResolutionError

logger = logging.getLogger(__name__)

#: Resolution label -> maximum video height in pixels.
#: Extend this mapping to support more resolutions, e.g.
#: ``"360p": 360, "480p": 480, "1440p": 1440, "2160p": 2160``.
SUPPORTED_RESOLUTIONS: dict[str, int] = {
    "720p": 720,
    "1080p": 1080,
}

DEFAULT_RESOLUTION = "1080p"

#: Audio-only downloads are converted to this single format.
AUDIO_OUTPUT_CODEC = "mp3"
AUDIO_BITRATE_KBPS = 320

#: YouTube "Mix" (list=RD...) playlists are generated endlessly; without an
#: explicit --limit only this many items are downloaded.
DEFAULT_MIX_LIMIT = 25

#: Project root (the ``YoutubeDownloadPlugin`` folder).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "downloads"


class MediaType(str, Enum):
    """What to download."""

    VIDEO = "video"
    AUDIO = "audio"

    @classmethod
    def parse(cls, value: "str | MediaType | None") -> "MediaType":
        if isinstance(value, MediaType):
            return value
        try:
            return cls(str(value or "video").strip().lower())
        except ValueError:
            raise ConfigurationError(
                f"Unknown type '{value}'. Use 'video' or 'audio'."
            ) from None


class FallbackPolicy(str, Enum):
    """What to do when the requested resolution is not available."""

    #: Download the highest resolution *below* the requested one and warn.
    LOWER = "lower"
    #: Stop with :class:`ResolutionUnavailableError`.
    ERROR = "error"


def resolution_to_height(label: str) -> int:
    """Return the pixel height for a supported resolution label.

    Accepts case-insensitive labels and a bare number (``"1080"``).
    """
    normalized = (label or "").strip().lower()
    if normalized and not normalized.endswith("p"):
        normalized += "p"
    try:
        return SUPPORTED_RESOLUTIONS[normalized]
    except KeyError:
        supported = ", ".join(supported_resolution_labels())
        raise UnsupportedResolutionError(
            f"Unsupported resolution '{label}'. Supported: {supported}"
        ) from None


def normalize_resolution_label(label: str) -> str:
    """Return the canonical label (e.g. ``"1080P"`` -> ``"1080p"``)."""
    height = resolution_to_height(label)
    return f"{height}p"


def supported_resolution_labels() -> list[str]:
    """Supported labels ordered from highest to lowest."""
    return sorted(SUPPORTED_RESOLUTIONS, key=SUPPORTED_RESOLUTIONS.__getitem__, reverse=True)


@dataclass(frozen=True)
class AppConfig:
    """Runtime settings for a download."""

    output_dir: Path = DEFAULT_OUTPUT_DIR
    default_resolution: str = DEFAULT_RESOLUTION
    fallback: FallbackPolicy = FallbackPolicy.LOWER
    ffmpeg_path: Path | None = None
    prefer_mp4: bool = True
    retries: int = 3
    retry_backoff_seconds: float = 2.0
    socket_timeout_seconds: float = 30.0
    #: Extra free space required on top of the estimated file size (fraction).
    disk_space_margin: float = 0.10
    validate_output: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    def with_overrides(self, **overrides: Any) -> "AppConfig":
        """Return a copy with every non-``None`` override applied."""
        clean = {k: v for k, v in overrides.items() if v is not None}
        return replace(self, **clean)


def _coerce(name: str, value: Any, base_dir: Path) -> Any:
    if name in {"output_dir", "ffmpeg_path"}:
        if value in (None, ""):
            return None
        path = Path(str(value)).expanduser()
        return path if path.is_absolute() else (base_dir / path)
    if name == "fallback":
        try:
            return FallbackPolicy(str(value).lower())
        except ValueError:
            raise ConfigurationError(
                f"Invalid fallback policy '{value}'. Use 'lower' or 'error'."
            ) from None
    if name == "default_resolution":
        return normalize_resolution_label(str(value))
    if name in {"retries"}:
        number = int(value)
        if number < 0 or number > 10:
            raise ConfigurationError("'retries' must be between 0 and 10.")
        return number
    if name in {"retry_backoff_seconds", "socket_timeout_seconds", "disk_space_margin"}:
        number = float(value)
        if number < 0:
            raise ConfigurationError(f"'{name}' must not be negative.")
        return number
    if name in {"prefer_mp4", "validate_output"}:
        if not isinstance(value, bool):
            raise ConfigurationError(f"'{name}' must be true or false.")
        return value
    return value


def config_from_mapping(data: Mapping[str, Any], base_dir: Path = PROJECT_ROOT) -> AppConfig:
    """Build an :class:`AppConfig` from a plain mapping (e.g. parsed JSON).

    Relative paths are resolved against ``base_dir`` (the project root), so the
    default ``"downloads"`` always means ``YoutubeDownloadPlugin/downloads``.
    Unknown keys are kept in ``extra`` and logged, never silently used.
    """
    known = {f.name for f in fields(AppConfig)} - {"extra"}
    values: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    for key, value in data.items():
        if key.startswith("_"):  # comments such as "_comment"
            continue
        if key in known:
            coerced = _coerce(key, value, base_dir)
            if coerced is not None:
                values[key] = coerced
        else:
            extra[key] = value
    if extra:
        logger.warning("Ignoring unknown configuration keys: %s", ", ".join(sorted(extra)))
    return AppConfig(**values, extra=extra)


def load_config(path: Path | None = None) -> AppConfig:
    """Load settings from a JSON file.

    If ``path`` is ``None`` the default ``config/config.json`` is used when it
    exists; otherwise built-in defaults apply. An explicitly given path that
    does not exist is an error.
    """
    explicit = path is not None
    config_path = Path(path).expanduser() if path else DEFAULT_CONFIG_PATH
    if not config_path.is_file():
        if explicit:
            raise ConfigurationError(f"Configuration file not found: {config_path}")
        logger.debug("No config file at %s; using defaults", config_path)
        return AppConfig()
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Could not read configuration file {config_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigurationError(f"Configuration file {config_path} must contain a JSON object.")
    logger.debug("Loaded configuration from %s", config_path)
    # Relative paths in the default config are relative to the project root.
    base_dir = PROJECT_ROOT if config_path == DEFAULT_CONFIG_PATH else config_path.parent
    return config_from_mapping(data, base_dir=base_dir)
