"""Tests for configuration loading."""

import json
import os
from pathlib import Path

import pytest

from audio_transcode_watcher.config import (
    CODEC_EXTENSIONS,
    Config,
    OutputConfig,
    load_config,
)


class TestOutputConfig:
    """Tests for OutputConfig."""
    
    def test_create_with_required_fields(self):
        """Test creating OutputConfig with required fields only."""
        config = OutputConfig(
            name="test",
            codec="mp3",
            path="/output",
        )
        assert config.name == "test"
        assert config.codec == "mp3"
        assert config.path == "/output"
        assert config.bitrate == "256k"  # Default for mp3
        assert config.include_artwork is True
    
    def test_codec_normalized_to_lowercase(self):
        """Test that codec is normalized to lowercase."""
        config = OutputConfig(name="test", codec="ALAC", path="/output")
        assert config.codec == "alac"
    
    def test_invalid_codec_raises_error(self):
        """Test that invalid codec raises ValueError."""
        with pytest.raises(ValueError, match="Unknown codec"):
            OutputConfig(name="test", codec="invalid", path="/output")
    
    def test_extension_property(self):
        """Test the extension property for each codec."""
        for codec, expected_ext in CODEC_EXTENSIONS.items():
            config = OutputConfig(name="test", codec=codec, path="/output")
            assert config.extension == expected_ext
    
    def test_is_lossless_property(self):
        """Test the is_lossless property."""
        lossless_codecs = {"alac", "flac", "wav"}
        lossy_codecs = {"mp3", "aac", "opus"}
        
        for codec in lossless_codecs:
            config = OutputConfig(name="test", codec=codec, path="/output")
            assert config.is_lossless is True
        
        for codec in lossy_codecs:
            config = OutputConfig(name="test", codec=codec, path="/output")
            assert config.is_lossless is False
    
    def test_artwork_disabled_for_unsupported_codecs(self):
        """Test that artwork is disabled for codecs that don't support it."""
        # Opus doesn't support artwork
        config = OutputConfig(
            name="test",
            codec="opus",
            path="/output",
            include_artwork=True,
        )
        assert config.include_artwork is False
    
    def test_from_dict(self):
        """Test creating OutputConfig from a dictionary."""
        data = {
            "name": "test-aac",
            "codec": "aac",
            "path": "/music/aac",
            "bitrate": "192k",
            "include_artwork": False,
        }
        config = OutputConfig.from_dict(data)
        assert config.name == "test-aac"
        assert config.codec == "aac"
        assert config.path == "/music/aac"
        assert config.bitrate == "192k"
        assert config.include_artwork is False


class TestConfig:
    """Tests for Config."""
    
    def test_create_with_required_fields(self, sample_output_config):
        """Test creating Config with required fields."""
        config = Config(
            source_path="/music/source",
            outputs=[sample_output_config],
        )
        assert config.source_path == "/music/source"
        assert len(config.outputs) == 1
        assert config.force_reencode is False
    
    def test_empty_source_path_raises_error(self, sample_output_config):
        """Test that empty source_path raises ValueError."""
        with pytest.raises(ValueError, match="source_path is required"):
            Config(source_path="", outputs=[sample_output_config])
    
    def test_empty_outputs_raises_error(self):
        """Test that empty outputs list raises ValueError."""
        with pytest.raises(ValueError, match="At least one output is required"):
            Config(source_path="/music", outputs=[])
    
    def test_duplicate_output_names_raises_error(self):
        """Test that duplicate output names raise ValueError."""
        outputs = [
            OutputConfig(name="same", codec="mp3", path="/path1"),
            OutputConfig(name="same", codec="aac", path="/path2"),
        ]
        with pytest.raises(ValueError, match="Duplicate output names"):
            Config(source_path="/music", outputs=outputs)
    
    def test_duplicate_output_paths_raises_error(self):
        """Test that duplicate output paths raise ValueError."""
        outputs = [
            OutputConfig(name="out1", codec="mp3", path="/same/path"),
            OutputConfig(name="out2", codec="aac", path="/same/path"),
        ]
        with pytest.raises(ValueError, match="Duplicate output paths"):
            Config(source_path="/music", outputs=outputs)
    
    def test_output_paths_property(self, sample_config):
        """Test the output_paths property."""
        paths = sample_config.output_paths
        assert len(paths) == 3
        assert all(isinstance(p, str) for p in paths)
    
    def test_get_output_by_name(self, sample_config):
        """Test getting output by name."""
        output = sample_config.get_output_by_name("mp3-256")
        assert output is not None
        assert output.codec == "mp3"
        
        # Non-existent name
        assert sample_config.get_output_by_name("nonexistent") is None
    
    def test_from_dict(self):
        """Test creating Config from a dictionary."""
        data = {
            "source": {"path": "/music/flac"},
            "outputs": [
                {"name": "out1", "codec": "mp3", "path": "/music/mp3"},
            ],
            "settings": {
                "force_reencode": True,
                "stability_timeout": 30.0,
            },
        }
        config = Config.from_dict(data)
        assert config.source_path == "/music/flac"
        assert len(config.outputs) == 1
        assert config.force_reencode is True
        assert config.stability_timeout == 30.0
    
    def test_from_yaml_file(self, yaml_config_file):
        """Test loading Config from a YAML file."""
        config = Config.from_yaml_file(yaml_config_file)
        assert config.source_path == "/music/flac"
        assert len(config.outputs) == 2
        assert config.stability_timeout == 30
        assert config.min_stable_seconds == 0.5
    
    def test_from_json_string(self):
        """Test loading Config from a JSON string."""
        json_str = json.dumps({
            "source": {"path": "/music/src"},
            "outputs": [
                {"name": "aac", "codec": "aac", "path": "/music/aac"},
            ],
        })
        config = Config.from_json_string(json_str)
        assert config.source_path == "/music/src"
        assert config.outputs[0].codec == "aac"


class TestLoadConfig:
    """Tests for load_config function."""
    
    def test_load_from_config_file(self, yaml_config_file, monkeypatch):
        """Test loading config from CONFIG_FILE env var."""
        monkeypatch.setenv("CONFIG_FILE", yaml_config_file)
        config = load_config()
        assert config.source_path == "/music/flac"
    
    def test_load_from_config_json(self, monkeypatch):
        """Test loading config from CONFIG_JSON env var."""
        json_config = json.dumps({
            "source": {"path": "/json/source"},
            "outputs": [
                {"name": "test", "codec": "flac", "path": "/json/output"},
            ],
        })
        monkeypatch.setenv("CONFIG_JSON", json_config)
        config = load_config()
        assert config.source_path == "/json/source"
    
    def test_config_file_takes_precedence(self, yaml_config_file, monkeypatch):
        """Test that CONFIG_FILE takes precedence over CONFIG_JSON."""
        monkeypatch.setenv("CONFIG_FILE", yaml_config_file)
        monkeypatch.setenv("CONFIG_JSON", '{"source":{"path":"/json"},"outputs":[{"name":"x","codec":"mp3","path":"/x"}]}')
        config = load_config()
        assert config.source_path == "/music/flac"  # From YAML, not JSON
    
    def test_missing_config_raises_error(self, monkeypatch):
        """Test that missing config raises ValueError."""
        monkeypatch.delenv("CONFIG_FILE", raising=False)
        monkeypatch.delenv("CONFIG_JSON", raising=False)
        with pytest.raises(ValueError, match="No configuration found"):
            load_config()
    
    def test_nonexistent_config_file_raises_error(self, monkeypatch):
        """Test that nonexistent config file raises ValueError."""
        monkeypatch.setenv("CONFIG_FILE", "/nonexistent/config.yaml")
        with pytest.raises(ValueError, match="CONFIG_FILE not found"):
            load_config()
