"""Tests for FFmpeg encoder module."""

import pytest

from audio_transcode_watcher.config import OutputConfig
from audio_transcode_watcher.encoder import (
    _remove_artwork_from_command,
    build_ffmpeg_command,
)


class TestBuildFFmpegCommand:
    """Tests for build_ffmpeg_command function."""
    
    def test_alac_command_with_artwork(self):
        """Test ALAC encoding command with artwork."""
        config = OutputConfig(
            name="alac",
            codec="alac",
            path="/output",
            include_artwork=True,
        )
        cmd = build_ffmpeg_command("/input/test.flac", "/output/test.m4a", config)
        
        assert cmd[0] == "ffmpeg"
        assert "-i" in cmd
        assert "/input/test.flac" in cmd
        assert "-c:a" in cmd
        assert "alac" in cmd
        assert "-map" in cmd
        assert "0:v:0?" in cmd  # Artwork mapping
        assert "-c:v" in cmd
        assert "copy" in cmd
        assert cmd[-1] == "/output/test.m4a"
    
    def test_alac_command_without_artwork(self):
        """Test ALAC encoding command without artwork."""
        config = OutputConfig(
            name="alac",
            codec="alac",
            path="/output",
            include_artwork=False,
        )
        cmd = build_ffmpeg_command("/input/test.flac", "/output/test.m4a", config)
        
        assert "0:v:0?" not in cmd
        assert "-c:v" not in cmd
    
    def test_aac_command(self):
        """Test AAC encoding command."""
        config = OutputConfig(
            name="aac",
            codec="aac",
            path="/output",
            bitrate="192k",
        )
        cmd = build_ffmpeg_command("/input/test.flac", "/output/test.m4a", config)
        
        assert "-c:a" in cmd
        assert "aac" in cmd
        assert "-b:a" in cmd
        assert "192k" in cmd
        assert "-movflags" in cmd
        assert "+faststart" in cmd
    
    def test_mp3_command(self):
        """Test MP3 encoding command."""
        config = OutputConfig(
            name="mp3",
            codec="mp3",
            path="/output",
            bitrate="320k",
            include_artwork=True,
        )
        cmd = build_ffmpeg_command("/input/test.flac", "/output/test.mp3", config)
        
        assert "-c:a" in cmd
        assert "libmp3lame" in cmd
        assert "-b:a" in cmd
        assert "320k" in cmd
        assert "-c:v" in cmd
        assert "mjpeg" in cmd  # MP3 needs mjpeg for ID3 APIC
        assert "-id3v2_version" in cmd
    
    def test_opus_command(self):
        """Test Opus encoding command."""
        config = OutputConfig(
            name="opus",
            codec="opus",
            path="/output",
            bitrate="128k",
        )
        cmd = build_ffmpeg_command("/input/test.flac", "/output/test.opus", config)
        
        assert "-c:a" in cmd
        assert "libopus" in cmd
        assert "-b:a" in cmd
        assert "128k" in cmd
        # Opus doesn't support artwork
        assert "0:v:0?" not in cmd
    
    def test_flac_command(self):
        """Test FLAC encoding command (re-encoding)."""
        config = OutputConfig(
            name="flac",
            codec="flac",
            path="/output",
            include_artwork=True,
        )
        cmd = build_ffmpeg_command("/input/test.wav", "/output/test.flac", config)
        
        assert "-c:a" in cmd
        assert "flac" in cmd
        assert "-f" in cmd
    
    def test_wav_command(self):
        """Test WAV encoding command."""
        config = OutputConfig(
            name="wav",
            codec="wav",
            path="/output",
        )
        cmd = build_ffmpeg_command("/input/test.flac", "/output/test.wav", config)
        
        assert "-c:a" in cmd
        assert "pcm_s16le" in cmd
        assert "-f" in cmd
        assert "wav" in cmd
    
    def test_command_has_common_options(self):
        """Test that all commands have common options."""
        config = OutputConfig(name="test", codec="mp3", path="/output")
        cmd = build_ffmpeg_command("/input/test.flac", "/output/test.mp3", config)
        
        assert "-loglevel" in cmd
        assert "error" in cmd
        assert "-y" in cmd  # Overwrite
        assert "-map" in cmd
        assert "0:a:0" in cmd  # First audio stream
        assert "-map_metadata" in cmd
        assert "0" in cmd


class TestRemoveArtworkFromCommand:
    """Tests for _remove_artwork_from_command function."""
    
    def test_removes_video_mapping(self):
        """Test that video mapping is removed."""
        cmd = [
            "ffmpeg", "-i", "input.flac",
            "-map", "0:a:0",
            "-map", "0:v:0?",
            "-c:a", "alac",
            "-c:v", "copy",
            "output.m4a"
        ]
        filtered = _remove_artwork_from_command(cmd)
        
        assert "0:v:0?" not in filtered
        assert "-c:v" not in filtered
        assert "copy" not in filtered
        # Audio mapping should remain
        assert "0:a:0" in filtered
    
    def test_preserves_audio_mapping(self):
        """Test that audio mapping is preserved."""
        cmd = [
            "ffmpeg", "-i", "input.flac",
            "-map", "0:a:0",
            "-map", "0:v:0?",
            "-c:a", "libmp3lame",
            "-c:v", "mjpeg",
            "output.mp3"
        ]
        filtered = _remove_artwork_from_command(cmd)
        
        # Audio should be preserved
        assert "-map" in filtered
        assert "0:a:0" in filtered
        assert "-c:a" in filtered
        assert "libmp3lame" in filtered
    
    def test_removes_vf_options(self):
        """Test that -vf options are removed."""
        cmd = [
            "ffmpeg", "-i", "input.flac",
            "-vf", "scale=300:300",
            "-c:a", "aac",
            "output.m4a"
        ]
        filtered = _remove_artwork_from_command(cmd)
        
        assert "-vf" not in filtered
        assert "scale=300:300" not in filtered
    
    def test_handles_command_without_video(self):
        """Test handling command that already has no video options."""
        cmd = [
            "ffmpeg", "-i", "input.flac",
            "-map", "0:a:0",
            "-c:a", "opus",
            "output.opus"
        ]
        filtered = _remove_artwork_from_command(cmd)
        
        assert filtered == cmd
