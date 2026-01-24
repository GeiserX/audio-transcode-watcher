"""Tests for sync module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from audio_transcode_watcher.config import Config, OutputConfig
from audio_transcode_watcher.sync import (
    _cleanup_orphans,
    delete_outputs,
    initial_sync,
    process_source_file,
    purge_all_outputs,
    safety_guard_active,
)
from audio_transcode_watcher.utils import nfc


class TestSafetyGuard:
    """Tests for safety_guard_active function."""
    
    def test_active_when_source_empty(self, temp_dir):
        """Test safety guard is active when source is empty."""
        empty_source = Path(temp_dir) / "empty_source"
        empty_source.mkdir()
        
        output = Path(temp_dir) / "output"
        output.mkdir()
        (output / "file.m4a").touch()
        
        config = Config(
            source_path=str(empty_source),
            outputs=[OutputConfig(name="out", codec="alac", path=str(output))],
        )
        
        assert safety_guard_active(config) is True
    
    def test_active_when_output_empty_and_bulk_encode_disabled(self, source_dir, temp_dir):
        """Test safety guard is active when output empty and bulk encode disabled."""
        empty_output = Path(temp_dir) / "empty_output"
        empty_output.mkdir()
        
        config = Config(
            source_path=source_dir,
            outputs=[OutputConfig(name="out", codec="alac", path=str(empty_output))],
            allow_initial_bulk_encode=False,  # Disable bulk encoding
        )
        
        assert safety_guard_active(config) is True
    
    def test_inactive_when_output_empty_and_bulk_encode_enabled(self, source_dir, temp_dir):
        """Test safety guard allows empty outputs when bulk encode enabled (default)."""
        empty_output = Path(temp_dir) / "empty_output"
        empty_output.mkdir()
        
        config = Config(
            source_path=source_dir,
            outputs=[OutputConfig(name="out", codec="alac", path=str(empty_output))],
            allow_initial_bulk_encode=True,  # Default behavior
        )
        
        assert safety_guard_active(config) is False
    
    def test_inactive_when_all_have_files(self, source_dir, output_dirs):
        """Test safety guard is inactive when all dirs have files."""
        # Add files to output dirs
        for path in output_dirs.values():
            (Path(path) / "test.m4a").touch()
        
        config = Config(
            source_path=source_dir,
            outputs=[
                OutputConfig(name=name, codec="alac", path=path)
                for name, path in output_dirs.items()
            ],
        )
        
        assert safety_guard_active(config) is False
    
    def test_ignores_hidden_files(self, temp_dir):
        """Test that hidden files are ignored when checking empty."""
        source = Path(temp_dir) / "source"
        source.mkdir()
        (source / ".hidden").touch()  # Hidden file
        
        output = Path(temp_dir) / "output"
        output.mkdir()
        (output / "file.m4a").touch()
        
        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="out", codec="alac", path=str(output))],
        )
        
        # Source has only hidden file, should be considered empty
        assert safety_guard_active(config) is True


class TestProcessSourceFile:
    """Tests for process_source_file function."""
    
    @patch("audio_transcode_watcher.sync.atomic_ffmpeg_encode")
    @patch("audio_transcode_watcher.sync.safety_guard_active")
    def test_skips_non_audio_files(self, mock_guard, mock_encode, sample_config, temp_dir):
        """Test that non-audio files are skipped."""
        mock_guard.return_value = False
        
        # Create a non-audio file
        non_audio = Path(temp_dir) / "readme.txt"
        non_audio.write_text("test")
        
        process_source_file(str(non_audio), sample_config, check_stable=False)
        
        mock_encode.assert_not_called()
    
    @patch("audio_transcode_watcher.sync.atomic_ffmpeg_encode")
    @patch("audio_transcode_watcher.sync.safety_guard_active")
    def test_skips_when_safety_guard_active(self, mock_guard, mock_encode, sample_config, source_dir):
        """Test that processing is skipped when safety guard is active."""
        mock_guard.return_value = True
        
        audio_file = Path(source_dir) / "Artist - Song 1.flac"
        process_source_file(str(audio_file), sample_config, check_stable=False)
        
        mock_encode.assert_not_called()
    
    @patch("audio_transcode_watcher.sync.atomic_ffmpeg_encode")
    @patch("audio_transcode_watcher.sync.safety_guard_active")
    def test_processes_audio_files(self, mock_guard, mock_encode, sample_config, source_dir):
        """Test that audio files are processed."""
        mock_guard.return_value = False
        mock_encode.return_value = 0
        
        audio_file = Path(source_dir) / "Artist - Song 1.flac"
        process_source_file(str(audio_file), sample_config, check_stable=False)
        
        # Should be called for each output (3 outputs)
        assert mock_encode.call_count == 3


class TestDeleteOutputs:
    """Tests for delete_outputs function."""
    
    @patch("audio_transcode_watcher.sync.safety_guard_active")
    def test_deletes_matching_files(self, mock_guard, temp_dir):
        """Test that matching output files are deleted."""
        mock_guard.return_value = False
        
        source = Path(temp_dir) / "source"
        source.mkdir()
        (source / "Artist - Song.flac").touch()
        
        output = Path(temp_dir) / "output"
        output.mkdir()
        output_file = output / "Artist - Song.m4a"
        output_file.touch()
        
        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="out", codec="alac", path=str(output))],
        )
        
        delete_outputs(str(source / "Artist - Song.flac"), config)
        
        assert not output_file.exists()
    
    @patch("audio_transcode_watcher.sync.safety_guard_active")
    def test_handles_mp3_copies(self, mock_guard, temp_dir):
        """Test deletion of MP3 copies in ALAC folder."""
        mock_guard.return_value = False
        
        source = Path(temp_dir) / "source"
        source.mkdir()
        (source / "Song.mp3").touch()
        
        output = Path(temp_dir) / "output"
        output.mkdir()
        output_file = output / "Song.mp3"
        output_file.touch()
        
        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(output))],
        )
        
        delete_outputs(str(source / "Song.mp3"), config)
        
        assert not output_file.exists()


class TestPurgeAllOutputs:
    """Tests for purge_all_outputs function."""
    
    @patch("audio_transcode_watcher.sync.safety_guard_active")
    def test_purges_all_files(self, mock_guard, temp_dir):
        """Test that all output files are purged."""
        mock_guard.return_value = False
        
        source = Path(temp_dir) / "source"
        source.mkdir()
        (source / "file.flac").touch()
        
        output = Path(temp_dir) / "output"
        output.mkdir()
        (output / "file1.m4a").touch()
        (output / "file2.m4a").touch()
        
        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="out", codec="alac", path=str(output))],
        )
        
        purge_all_outputs(config)
        
        # All files should be deleted
        assert list(output.glob("*.m4a")) == []
    
    @patch("audio_transcode_watcher.sync.safety_guard_active")
    def test_skips_when_safety_guard_active(self, mock_guard, temp_dir):
        """Test that purge is skipped when safety guard is active."""
        mock_guard.return_value = True
        
        source = Path(temp_dir) / "source"
        source.mkdir()
        (source / "file.flac").touch()
        
        output = Path(temp_dir) / "output"
        output.mkdir()
        test_file = output / "file.m4a"
        test_file.touch()
        
        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="out", codec="alac", path=str(output))],
        )
        
        purge_all_outputs(config)
        
        # File should still exist
        assert test_file.exists()


class TestCleanupOrphans:
    """Tests for _cleanup_orphans function."""
    
    def test_removes_orphan_files(self, temp_dir):
        """Test that orphan files are removed."""
        source = Path(temp_dir) / "source"
        source.mkdir()
        
        output = Path(temp_dir) / "output"
        output.mkdir()
        orphan = output / "Orphan - Song.m4a"
        orphan.touch()
        
        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(output))],
        )
        
        # Source has no files, so "Orphan - Song" is an orphan
        source_stems = set()
        _cleanup_orphans(config, source_stems)
        
        assert not orphan.exists()
    
    def test_keeps_non_orphan_files(self, temp_dir):
        """Test that non-orphan files are kept."""
        source = Path(temp_dir) / "source"
        source.mkdir()
        (source / "Valid - Song.flac").touch()
        
        output = Path(temp_dir) / "output"
        output.mkdir()
        valid_output = output / "Valid - Song.m4a"
        valid_output.touch()
        
        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(output))],
        )
        
        source_stems = {nfc("Valid - Song")}
        _cleanup_orphans(config, source_stems)
        
        assert valid_output.exists()
    
    def test_handles_alac_with_mp3(self, temp_dir):
        """Test ALAC folder can have both .m4a and .mp3 files."""
        source = Path(temp_dir) / "source"
        source.mkdir()
        (source / "Song.mp3").touch()
        
        output = Path(temp_dir) / "output"
        output.mkdir()
        mp3_copy = output / "Song.mp3"
        mp3_copy.touch()
        
        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(output))],
        )
        
        source_stems = {nfc("Song")}
        _cleanup_orphans(config, source_stems)
        
        # MP3 copy should be kept
        assert mp3_copy.exists()
