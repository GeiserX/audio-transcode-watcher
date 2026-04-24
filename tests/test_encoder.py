"""Tests for FFmpeg encoder module."""

import os
from unittest.mock import MagicMock, patch

import pytest

from audio_transcode_watcher.config import OutputConfig
from audio_transcode_watcher.encoder import (
    _cleanup_temp,
    _remove_artwork_from_command,
    atomic_ffmpeg_encode,
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


class TestBuildFFmpegCommandUnsupportedCodec:
    """Test error handling for unsupported codecs."""

    def test_unsupported_codec_raises_value_error(self):
        """Raise ValueError for unknown codec."""
        config = OutputConfig.__new__(OutputConfig)
        config.codec = "unknown"
        config.bitrate = "128k"
        config.include_artwork = False
        with pytest.raises(ValueError, match="Unsupported codec"):
            build_ffmpeg_command("/input/test.flac", "/output/test.xyz", config)


class TestAtomicFfmpegEncode:
    """Tests for atomic_ffmpeg_encode function."""

    @patch("subprocess.run")
    def test_successful_encode_renames_temp_to_final(self, mock_run, tmp_path):
        """On success, temp file is renamed to final destination."""
        final = str(tmp_path / "output.m4a")
        cmd = ["ffmpeg", "-i", "input.flac", "-c:a", "alac", final]

        proc = MagicMock()
        proc.returncode = 0
        mock_run.return_value = proc

        # atomic_ffmpeg_encode writes to .tmp__ff then renames;
        # simulate the file being created by ffmpeg
        def side_effect(cmd_arg, **kwargs):
            tmp_dest = cmd_arg[-1]
            with open(tmp_dest, "w") as f:
                f.write("data")
            return proc

        mock_run.side_effect = side_effect

        rc = atomic_ffmpeg_encode(cmd, final)
        assert rc == 0
        assert os.path.isfile(final)

    @patch("subprocess.run")
    def test_failed_encode_returns_nonzero(self, mock_run, tmp_path):
        """Return nonzero rc when ffmpeg fails."""
        final = str(tmp_path / "output.m4a")
        cmd = ["ffmpeg", "-i", "input.flac", final]

        proc = MagicMock()
        proc.returncode = 1
        proc.stderr = b"some error"
        mock_run.return_value = proc

        rc = atomic_ffmpeg_encode(cmd, final, retry_without_artwork=False)
        assert rc == 1
        assert not os.path.isfile(final)

    @patch("subprocess.run")
    def test_retries_without_artwork_on_artwork_error(self, mock_run, tmp_path):
        """Retry without artwork when stderr hints at artwork failure."""
        final = str(tmp_path / "output.m4a")
        cmd = [
            "ffmpeg", "-i", "input.flac",
            "-map", "0:a:0", "-map", "0:v:0?",
            "-c:a", "alac", "-c:v", "copy",
            final,
        ]

        # First call fails with artwork error, second succeeds
        fail_proc = MagicMock()
        fail_proc.returncode = 1
        fail_proc.stderr = b"Error: mjpeg decode failed"

        ok_proc = MagicMock()
        ok_proc.returncode = 0

        call_count = [0]

        def side_effect(cmd_arg, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return fail_proc
            # Create the temp file to simulate ffmpeg success
            tmp_dest = cmd_arg[-1]
            with open(tmp_dest, "w") as f:
                f.write("data")
            return ok_proc

        mock_run.side_effect = side_effect

        rc = atomic_ffmpeg_encode(cmd, final, retry_without_artwork=True)
        assert rc == 0
        assert mock_run.call_count == 2

    @patch("subprocess.run")
    def test_retry_also_fails_returns_nonzero(self, mock_run, tmp_path):
        """Return nonzero when both initial and retry encode fail."""
        final = str(tmp_path / "output.m4a")
        cmd = ["ffmpeg", "-i", "input.flac", "-map", "0:v:0?", "-c:v", "copy", final]

        proc = MagicMock()
        proc.returncode = 1
        proc.stderr = b"png decode error"
        mock_run.return_value = proc

        rc = atomic_ffmpeg_encode(cmd, final, retry_without_artwork=True)
        assert rc == 1
        assert mock_run.call_count == 2

    @patch("subprocess.run")
    def test_cleans_up_stale_temp_before_encoding(self, mock_run, tmp_path):
        """Remove leftover .tmp__ff before encoding starts."""
        final = str(tmp_path / "output.m4a")
        stale = final + ".tmp__ff"
        with open(stale, "w") as f:
            f.write("stale")

        proc = MagicMock()
        proc.returncode = 0
        mock_run.return_value = proc

        def side_effect(cmd_arg, **kwargs):
            tmp_dest = cmd_arg[-1]
            with open(tmp_dest, "w") as f:
                f.write("data")
            return proc

        mock_run.side_effect = side_effect

        rc = atomic_ffmpeg_encode(["ffmpeg", "-i", "in.flac", final], final)
        assert rc == 0

    @patch("os.replace", side_effect=OSError("disk full"))
    @patch("subprocess.run")
    def test_returns_1_when_atomic_replace_fails(self, mock_run, _replace, tmp_path):
        """Return 1 when os.replace fails after successful encode."""
        final = str(tmp_path / "output.m4a")
        cmd = ["ffmpeg", "-i", "input.flac", final]

        proc = MagicMock()
        proc.returncode = 0
        mock_run.return_value = proc

        def side_effect(cmd_arg, **kwargs):
            tmp_dest = cmd_arg[-1]
            with open(tmp_dest, "w") as f:
                f.write("data")
            return proc

        mock_run.side_effect = side_effect

        rc = atomic_ffmpeg_encode(cmd, final, retry_without_artwork=False)
        assert rc == 1

    @patch("subprocess.run")
    def test_retry_replace_failure_returns_1(self, mock_run, tmp_path):
        """Return 1 when retry succeeds but os.replace fails."""
        final = str(tmp_path / "output.m4a")
        cmd = ["ffmpeg", "-i", "in.flac", "-map", "0:v:0?", "-c:v", "copy", final]

        fail_proc = MagicMock()
        fail_proc.returncode = 1
        fail_proc.stderr = b"mjpeg error"

        ok_proc = MagicMock()
        ok_proc.returncode = 0

        call_count = [0]

        def side_effect(cmd_arg, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return fail_proc
            tmp_dest = cmd_arg[-1]
            with open(tmp_dest, "w") as f:
                f.write("data")
            return ok_proc

        mock_run.side_effect = side_effect

        with patch("os.replace", side_effect=OSError("nfs error")):
            rc = atomic_ffmpeg_encode(cmd, final, retry_without_artwork=True)

        assert rc == 1


class TestCleanupTemp:
    """Tests for _cleanup_temp function."""

    def test_removes_existing_file(self, tmp_path):
        """Remove a temporary file that exists."""
        f = tmp_path / "test.tmp__ff"
        f.write_text("data")
        _cleanup_temp(str(f))
        assert not f.exists()

    def test_no_error_when_file_missing(self, tmp_path):
        """No error when the file does not exist."""
        _cleanup_temp(str(tmp_path / "nonexistent.tmp__ff"))

    def test_no_error_on_permission_failure(self, tmp_path):
        """Gracefully handle permission errors."""
        with patch("os.path.exists", return_value=True), \
             patch("os.remove", side_effect=PermissionError("denied")):
            _cleanup_temp(str(tmp_path / "locked.tmp__ff"))
