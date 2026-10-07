"""Configuration loading for audio-transcode-watcher."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Settings that older releases read and this one ignores. A config that
# still sets them loads fine; the first load logs one warning.
DEPRECATED_SETTINGS = {
    "whisper_fallback": "the Whisper lyrics fallback was removed in 0.6.0",
    "whisper_model": "the Whisper lyrics fallback was removed in 0.6.0",
}
_deprecation_warned = False


# Codec to file extension mapping
CODEC_EXTENSIONS = {
    "alac": ".m4a",
    "aac": ".m4a",
    "mp3": ".mp3",
    "opus": ".opus",
    "flac": ".flac",
    "wav": ".wav",
}

# Codecs that support embedded artwork
ARTWORK_SUPPORTED_CODECS = {"alac", "aac", "mp3", "flac"}

# Default bitrates for lossy codecs
DEFAULT_BITRATES = {
    "aac": "256k",
    "mp3": "256k",
    "opus": "128k",
}

# Portable-playback limits applied when an output does not set them.
# AAC goes to phones, AirPods and CarPlay: stereo, at most 48 kHz.
# Any codec can opt in by setting channels / max_sample_rate; 0 = no limit.
# What to do with a source that does not decode cleanly: "skip" writes
# nothing; "encode_anyway" retries with ffmpeg's error concealment.
CORRUPT_SOURCE_MODES = ("skip", "encode_anyway")

DEFAULT_CHANNELS = {"aac": 2}
DEFAULT_MAX_SAMPLE_RATE = {"aac": 48000}


def _check_corrupt_source(value: Any, label: str) -> None:
    if value not in CORRUPT_SOURCE_MODES:
        raise ValueError(f"{label} must be one of {', '.join(CORRUPT_SOURCE_MODES)}")


@dataclass
class OutputConfig:
    """Configuration for a single output destination."""

    name: str
    codec: str
    path: str
    bitrate: str = ""
    include_artwork: bool = True
    channels: int | None = None  # Downmix above this many channels; 0 = keep
    max_sample_rate: int | None = None  # Resample above this rate (Hz); 0 = keep
    corrupt_source: str | None = None  # "skip" | "encode_anyway"; None = global

    def __post_init__(self) -> None:
        """Validate and set defaults after initialization."""
        self.codec = self.codec.lower()

        if self.codec not in CODEC_EXTENSIONS:
            raise ValueError(
                f"Unknown codec '{self.codec}'. "
                f"Supported: {', '.join(CODEC_EXTENSIONS.keys())}"
            )

        # Set default bitrate for lossy codecs
        if not self.bitrate and self.codec in DEFAULT_BITRATES:
            self.bitrate = DEFAULT_BITRATES[self.codec]

        # Artwork not supported for some codecs
        if self.include_artwork and self.codec not in ARTWORK_SUPPORTED_CODECS:
            self.include_artwork = False

        if self.channels is None:
            self.channels = DEFAULT_CHANNELS.get(self.codec, 0)
        if self.max_sample_rate is None:
            self.max_sample_rate = DEFAULT_MAX_SAMPLE_RATE.get(self.codec, 0)
        if self.corrupt_source is not None:
            _check_corrupt_source(
                self.corrupt_source, f"Output '{self.name}': corrupt_source"
            )
        for key in ("channels", "max_sample_rate"):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(
                    f"Output '{self.name}': {key} must be a whole number, 0 for no limit"
                )

    @property
    def extension(self) -> str:
        """Get the file extension for this codec."""
        return CODEC_EXTENSIONS[self.codec]

    @property
    def is_lossless(self) -> bool:
        """Check if this codec is lossless."""
        return self.codec in {"alac", "flac", "wav"}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OutputConfig:
        """Create OutputConfig from a dictionary."""
        return cls(
            name=data["name"],
            codec=data["codec"],
            path=data["path"],
            bitrate=data.get("bitrate", ""),
            include_artwork=data.get("include_artwork", True),
            channels=data.get("channels"),
            max_sample_rate=data.get("max_sample_rate"),
            corrupt_source=data.get("corrupt_source"),
        )


@dataclass
class Config:
    """Main configuration for audio-transcode-watcher."""

    source_path: str
    outputs: list[OutputConfig] = field(default_factory=list)
    force_reencode: bool = False
    allow_initial_bulk_encode: bool = True  # Allow encoding when outputs are empty
    parallel_workers: int = 4  # Number of parallel encoding workers
    stability_timeout: float = 60.0
    min_stable_seconds: float = 1.0
    fetch_lyrics: bool = True  # Auto-fetch .lrc lyrics via syncedlyrics
    sync_interval_seconds: int = 300  # Seconds between periodic full syncs
    corrupt_source: str = "skip"  # Default for outputs that don't set it
    replaygain: bool = False  # Write ReplayGain 2.0 track tags (top-level key)

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        if not self.source_path:
            raise ValueError("source_path is required")

        if not isinstance(self.replaygain, bool):
            # ValueError, like every other config error, so main reports it.
            raise ValueError("replaygain must be true or false")  # noqa: TRY004

        _check_corrupt_source(self.corrupt_source, "settings.corrupt_source")

        interval = self.sync_interval_seconds
        if (
            isinstance(interval, bool)
            or not isinstance(interval, (int, float))
            or interval <= 0
        ):
            raise ValueError("sync_interval_seconds must be a number greater than 0")

        if not self.outputs:
            raise ValueError("At least one output is required")

        # Check for duplicate output names
        names = [o.name for o in self.outputs]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate output names detected")

        # Check for duplicate output paths
        paths = [o.path for o in self.outputs]
        if len(paths) != len(set(paths)):
            raise ValueError("Duplicate output paths detected")

    @property
    def output_paths(self) -> list[str]:
        """Get list of all output directory paths."""
        return [o.path for o in self.outputs]

    def corrupt_source_for(self, output: OutputConfig) -> str:
        """The corrupt_source mode for *output*: its own, else the global one."""
        return output.corrupt_source or self.corrupt_source

    def get_output_by_name(self, name: str) -> OutputConfig | None:
        """Get an output configuration by name."""
        for output in self.outputs:
            if output.name == name:
                return output
        return None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        """Create Config from a dictionary."""
        outputs = [OutputConfig.from_dict(o) for o in data.get("outputs", [])]
        settings = data.get("settings", {})
        _warn_deprecated_settings(settings)

        return cls(
            source_path=data.get("source", {}).get("path", ""),
            outputs=outputs,
            force_reencode=settings.get("force_reencode", False),
            allow_initial_bulk_encode=settings.get("allow_initial_bulk_encode", True),
            parallel_workers=settings.get("parallel_workers", 4),
            stability_timeout=settings.get("stability_timeout", 60.0),
            min_stable_seconds=settings.get("min_stable_seconds", 1.0),
            fetch_lyrics=settings.get("fetch_lyrics", True),
            sync_interval_seconds=settings.get("sync_interval_seconds", 300),
            corrupt_source=settings.get("corrupt_source", "skip"),
            # Documented at the top level; also read under settings, where
            # every other option lives, so either place works.
            replaygain=data.get("replaygain", settings.get("replaygain", False)),
        )

    @classmethod
    def from_yaml_file(cls, path: str) -> Config:
        """Load configuration from a YAML file."""
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data)

    @classmethod
    def from_json_string(cls, json_str: str) -> Config:
        """Load configuration from a JSON string."""
        data = json.loads(json_str)
        return cls.from_dict(data)


def _warn_deprecated_settings(settings: dict[str, Any]) -> None:
    """Log one warning per process for settings that are accepted but ignored."""
    global _deprecation_warned
    found = sorted(k for k in DEPRECATED_SETTINGS if k in settings)
    if not found or _deprecation_warned:
        return
    _deprecation_warned = True
    logger.warning(
        "Ignoring deprecated settings %s: %s. Remove them from your config.",
        ", ".join(found),
        DEPRECATED_SETTINGS[found[0]],
    )


def load_config() -> Config:
    """
    Load configuration from environment variables.

    Configuration is loaded from one of these sources (in priority order):
    1. CONFIG_FILE env var - path to a YAML config file
    2. CONFIG_JSON env var - JSON string with full configuration

    Raises:
        ValueError: If no valid configuration is found
    """
    # Try CONFIG_FILE first
    config_file = os.getenv("CONFIG_FILE")
    if config_file:
        if not Path(config_file).exists():
            raise ValueError(f"CONFIG_FILE not found: {config_file}")
        return Config.from_yaml_file(config_file)

    # Try CONFIG_JSON
    config_json = os.getenv("CONFIG_JSON")
    if config_json:
        return Config.from_json_string(config_json)

    # No configuration provided
    raise ValueError(
        "No configuration found. Set CONFIG_FILE (path to YAML config) "
        "or CONFIG_JSON (JSON configuration string)."
    )
