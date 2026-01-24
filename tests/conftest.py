"""Pytest fixtures for audio-transcode-watcher tests."""

import os
import tempfile
from pathlib import Path

import pytest

from audio_transcode_watcher.config import Config, OutputConfig


@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def source_dir(temp_dir):
    """Create a source directory with some test files."""
    src = Path(temp_dir) / "source"
    src.mkdir()
    
    # Create some fake audio files (just empty files for testing)
    (src / "Artist - Song 1.flac").touch()
    (src / "Artist - Song 2.flac").touch()
    (src / "Other - Track.mp3").touch()
    
    return str(src)


@pytest.fixture
def output_dirs(temp_dir):
    """Create output directories."""
    outputs = {}
    for name in ["alac", "mp3", "aac"]:
        path = Path(temp_dir) / name
        path.mkdir()
        outputs[name] = str(path)
    return outputs


@pytest.fixture
def sample_output_config():
    """Create a sample OutputConfig."""
    return OutputConfig(
        name="test-mp3",
        codec="mp3",
        path="/tmp/test-output",
        bitrate="256k",
        include_artwork=True,
    )


@pytest.fixture
def sample_config(source_dir, output_dirs):
    """Create a sample Config."""
    return Config(
        source_path=source_dir,
        outputs=[
            OutputConfig(
                name="alac",
                codec="alac",
                path=output_dirs["alac"],
            ),
            OutputConfig(
                name="mp3-256",
                codec="mp3",
                path=output_dirs["mp3"],
                bitrate="256k",
            ),
            OutputConfig(
                name="aac-256",
                codec="aac",
                path=output_dirs["aac"],
                bitrate="256k",
            ),
        ],
    )


@pytest.fixture
def yaml_config_content():
    """Return sample YAML config content."""
    return """
source:
  path: /music/flac

outputs:
  - name: alac
    codec: alac
    path: /music/alac
    include_artwork: true
  
  - name: mp3-256
    codec: mp3
    bitrate: 256k
    path: /music/mp3
    include_artwork: true

settings:
  force_reencode: false
  stability_timeout: 30
  min_stable_seconds: 0.5
"""


@pytest.fixture
def yaml_config_file(temp_dir, yaml_config_content):
    """Create a YAML config file."""
    config_path = Path(temp_dir) / "config.yaml"
    config_path.write_text(yaml_config_content)
    return str(config_path)
