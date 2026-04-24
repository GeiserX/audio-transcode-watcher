"""Tests for sync module."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from audio_transcode_watcher.config import Config, OutputConfig
from audio_transcode_watcher.sync import (
    _cleanup_orphans,
    _has_lossless_source,
    cleanup_stale_temp_files,
    delete_outputs,
    delete_sidecars,
    initial_sync,
    process_source_file,
    purge_all_outputs,
    safety_guard_active,
    sync_sidecars,
)
from audio_transcode_watcher.utils import get_rel_stem, nfc


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

        _cleanup_orphans(config, set())

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

        source_rel_stems = {nfc("Valid - Song")}
        _cleanup_orphans(config, source_rel_stems)

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

        source_rel_stems = {nfc("Song")}
        _cleanup_orphans(config, source_rel_stems)

        # MP3 copy should be kept
        assert mp3_copy.exists()

    def test_recursive_removes_orphan_in_subdir(self, temp_dir):
        """Test that orphaned files in subdirectories are removed."""
        source = Path(temp_dir) / "source"
        source.mkdir()

        output = Path(temp_dir) / "output"
        (output / "album1").mkdir(parents=True)
        orphan = output / "album1" / "Orphan.m4a"
        orphan.touch()

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(output))],
        )

        _cleanup_orphans(config, set())

        assert not orphan.exists()
        # Empty subdir should also be cleaned up
        assert not (output / "album1").exists()

    def test_recursive_keeps_non_orphan_in_subdir(self, temp_dir):
        """Test that non-orphaned files in subdirectories are kept."""
        source = Path(temp_dir) / "source"
        (source / "album1").mkdir(parents=True)
        (source / "album1" / "Song.flac").touch()

        output = Path(temp_dir) / "output"
        (output / "album1").mkdir(parents=True)
        valid = output / "album1" / "Song.m4a"
        valid.touch()

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(output))],
        )

        source_rel_stems = {get_rel_stem(
            str(source / "album1" / "Song.flac"), str(source),
        )}
        _cleanup_orphans(config, source_rel_stems)

        assert valid.exists()


class TestSyncSidecars:
    """Tests for sync_sidecars function."""

    def test_copies_lrc_to_outputs(self, temp_dir):
        """Test that .lrc files are copied to all output directories."""
        source = Path(temp_dir) / "source"
        source.mkdir()
        (source / "Artist - Song.flac").touch()
        lrc = source / "Artist - Song.lrc"
        lrc.write_text("[00:01.00] Hello")

        out1 = Path(temp_dir) / "aac"
        out1.mkdir()
        out2 = Path(temp_dir) / "mp3"
        out2.mkdir()

        config = Config(
            source_path=str(source),
            outputs=[
                OutputConfig(name="aac", codec="aac", path=str(out1)),
                OutputConfig(name="mp3", codec="mp3", path=str(out2)),
            ],
        )

        sync_sidecars(str(source / "Artist - Song.flac"), config)

        assert (out1 / "Artist - Song.lrc").exists()
        assert (out2 / "Artist - Song.lrc").exists()
        assert (out1 / "Artist - Song.lrc").read_text() == "[00:01.00] Hello"

    def test_skips_when_no_lrc_exists(self, temp_dir):
        """Test that nothing happens when source has no .lrc file."""
        source = Path(temp_dir) / "source"
        source.mkdir()
        (source / "Artist - Song.flac").touch()

        output = Path(temp_dir) / "output"
        output.mkdir()

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="out", codec="aac", path=str(output))],
        )

        sync_sidecars(str(source / "Artist - Song.flac"), config)

        assert not (output / "Artist - Song.lrc").exists()

    def test_does_not_overwrite_identical(self, temp_dir):
        """Test that an up-to-date sidecar is not re-copied."""
        import time

        source = Path(temp_dir) / "source"
        source.mkdir()
        lrc = source / "Song.lrc"
        lrc.write_text("lyrics")

        output = Path(temp_dir) / "output"
        output.mkdir()
        dst = output / "Song.lrc"
        dst.write_text("lyrics")
        # Make destination newer than source
        import os
        os.utime(str(dst), (time.time() + 10, time.time() + 10))

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="out", codec="aac", path=str(output))],
        )

        sync_sidecars(str(source / "Song.flac"), config)

        # Should not have been overwritten (still same content)
        assert dst.read_text() == "lyrics"


class TestDeleteSidecars:
    """Tests for delete_sidecars function."""

    def test_deletes_lrc_from_outputs(self, temp_dir):
        """Test that .lrc files are removed from outputs."""
        source = Path(temp_dir) / "source"
        source.mkdir()

        out1 = Path(temp_dir) / "aac"
        out1.mkdir()
        lrc1 = out1 / "Artist - Song.lrc"
        lrc1.touch()

        out2 = Path(temp_dir) / "mp3"
        out2.mkdir()
        lrc2 = out2 / "Artist - Song.lrc"
        lrc2.touch()

        config = Config(
            source_path=str(source),
            outputs=[
                OutputConfig(name="aac", codec="aac", path=str(out1)),
                OutputConfig(name="mp3", codec="mp3", path=str(out2)),
            ],
        )

        delete_sidecars(str(source / "Artist - Song.flac"), config)

        assert not lrc1.exists()
        assert not lrc2.exists()

    def test_no_error_when_lrc_missing(self, temp_dir):
        """Test that no error when .lrc doesn't exist in output."""
        source = Path(temp_dir) / "source"
        source.mkdir()

        output = Path(temp_dir) / "output"
        output.mkdir()

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="out", codec="aac", path=str(output))],
        )

        # Should not raise
        delete_sidecars(str(source / "Song.flac"), config)


class TestCleanupOrphanSidecars:
    """Tests for orphan sidecar cleanup in _cleanup_orphans."""

    def test_removes_orphan_lrc(self, temp_dir):
        """Test that orphaned .lrc files are removed from outputs."""
        source = Path(temp_dir) / "source"
        source.mkdir()

        output = Path(temp_dir) / "output"
        output.mkdir()
        orphan_lrc = output / "Deleted - Song.lrc"
        orphan_lrc.touch()

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="out", codec="aac", path=str(output))],
        )

        _cleanup_orphans(config, set())

        assert not orphan_lrc.exists()

    def test_keeps_valid_lrc(self, temp_dir):
        """Test that .lrc files with matching sources are kept."""
        source = Path(temp_dir) / "source"
        source.mkdir()
        (source / "Valid - Song.flac").touch()

        output = Path(temp_dir) / "output"
        output.mkdir()
        valid_lrc = output / "Valid - Song.lrc"
        valid_lrc.touch()

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="out", codec="aac", path=str(output))],
        )

        _cleanup_orphans(config, {nfc("Valid - Song")})

        assert valid_lrc.exists()

    def test_recursive_removes_orphan_lrc_in_subdir(self, temp_dir):
        """Test that orphaned .lrc in subdirectories are removed."""
        source = Path(temp_dir) / "source"
        source.mkdir()

        output = Path(temp_dir) / "output"
        (output / "album").mkdir(parents=True)
        orphan_lrc = output / "album" / "Gone.lrc"
        orphan_lrc.touch()

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="out", codec="aac", path=str(output))],
        )

        _cleanup_orphans(config, set())

        assert not orphan_lrc.exists()


class TestHasLosslessSource:
    """Tests for _has_lossless_source function."""

    def test_returns_true_when_lossless_exists(self, temp_dir):
        """Return True when a lossless file with same stem exists."""
        source = Path(temp_dir) / "source"
        source.mkdir()
        (source / "Song.flac").touch()
        mp3 = source / "Song.mp3"
        mp3.touch()

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(Path(temp_dir) / "out"))],
        )
        (Path(temp_dir) / "out").mkdir()

        assert _has_lossless_source(str(mp3), config) is True

    def test_returns_false_when_no_lossless_exists(self, temp_dir):
        """Return False when no lossless file with same stem exists."""
        source = Path(temp_dir) / "source"
        source.mkdir()
        mp3 = source / "Song.mp3"
        mp3.touch()

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(Path(temp_dir) / "out"))],
        )
        (Path(temp_dir) / "out").mkdir()

        assert _has_lossless_source(str(mp3), config) is False


class TestProcessSourceFileMp3Copy:
    """Tests for MP3 copy behavior in process_source_file."""

    @patch("audio_transcode_watcher.sync.atomic_ffmpeg_encode")
    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_mp3_copied_to_alac_folder_unchanged(self, _guard, mock_encode, temp_dir):
        """MP3 files are copied (not transcoded) to ALAC output folder."""
        source = Path(temp_dir) / "source"
        source.mkdir()
        mp3 = source / "Song.mp3"
        mp3.write_text("fake mp3 data")

        out_alac = Path(temp_dir) / "alac"
        out_alac.mkdir()

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(out_alac))],
        )

        process_source_file(str(mp3), config, check_stable=False)

        # MP3 should be copied, not encoded
        mock_encode.assert_not_called()
        assert (out_alac / "Song.mp3").exists()
        assert (out_alac / "Song.mp3").read_text() == "fake mp3 data"

    @patch("audio_transcode_watcher.sync.atomic_ffmpeg_encode")
    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_mp3_skipped_when_lossless_source_exists(self, _guard, mock_encode, temp_dir):
        """Skip MP3 for ALAC output when a lossless source with same stem exists."""
        source = Path(temp_dir) / "source"
        source.mkdir()
        (source / "Song.flac").touch()
        mp3 = source / "Song.mp3"
        mp3.write_text("fake mp3")

        out_alac = Path(temp_dir) / "alac"
        out_alac.mkdir()

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(out_alac))],
        )

        process_source_file(str(mp3), config, check_stable=False)

        mock_encode.assert_not_called()
        assert not (out_alac / "Song.mp3").exists()


class TestProcessSourceFileStability:
    """Tests for stability check in process_source_file."""

    @patch("audio_transcode_watcher.sync.wait_for_stable", return_value=False)
    @patch("audio_transcode_watcher.sync.atomic_ffmpeg_encode")
    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_skips_when_file_not_stable(self, _guard, mock_encode, _stable, source_dir, sample_config):
        """Skip processing when file is not stable."""
        audio_file = Path(source_dir) / "Artist - Song 1.flac"
        process_source_file(str(audio_file), sample_config, check_stable=True)
        mock_encode.assert_not_called()


class TestProcessSourceFileDuplicate:
    """Tests for duplicate prevention in process_source_file."""

    @patch("audio_transcode_watcher.sync.atomic_ffmpeg_encode")
    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_skips_already_existing_outputs(self, _guard, mock_encode, temp_dir):
        """Skip encoding when output already exists and force=False."""
        source = Path(temp_dir) / "source"
        source.mkdir()
        flac = source / "Song.flac"
        flac.touch()

        out = Path(temp_dir) / "output"
        out.mkdir()
        existing = out / "Song.m4a"
        existing.touch()

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(out))],
        )

        process_source_file(str(flac), config, force=False, check_stable=False)

        mock_encode.assert_not_called()


class TestProcessSourceFileEncodeFail:
    """Tests for encode failure logging in process_source_file."""

    @patch("audio_transcode_watcher.sync.atomic_ffmpeg_encode", return_value=1)
    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_logs_error_on_encode_failure(self, _guard, mock_encode, temp_dir):
        """Log error when encoding fails (nonzero return)."""
        source = Path(temp_dir) / "source"
        source.mkdir()
        flac = source / "Song.flac"
        flac.touch()

        out = Path(temp_dir) / "output"
        out.mkdir()

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="mp3", codec="mp3", path=str(out), bitrate="256k")],
        )

        # Should not raise; just logs the error
        process_source_file(str(flac), config, force=True, check_stable=False)
        mock_encode.assert_called_once()


class TestProcessSourceFileLyrics:
    """Tests for lyrics fetching in process_source_file."""

    @patch("audio_transcode_watcher.sync.sync_sidecars")
    @patch("audio_transcode_watcher.sync.fetch_lyrics_for_file", side_effect=Exception("network"))
    @patch("audio_transcode_watcher.sync.atomic_ffmpeg_encode", return_value=0)
    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_lyrics_failure_does_not_stop_processing(self, _guard, _encode, _lyrics, _sync, temp_dir):
        """Lyrics fetch failure does not prevent sync_sidecars from running."""
        source = Path(temp_dir) / "source"
        source.mkdir()
        flac = source / "Song.flac"
        flac.touch()

        out = Path(temp_dir) / "output"
        out.mkdir()

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="mp3", codec="mp3", path=str(out), bitrate="256k")],
            fetch_lyrics=True,
        )

        process_source_file(str(flac), config, force=True, check_stable=False)
        _sync.assert_called_once()


class TestProcessSourceFileConcurrency:
    """Tests for concurrent processing guard."""

    @patch("audio_transcode_watcher.sync.atomic_ffmpeg_encode", return_value=0)
    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_in_progress_set_cleared_after_processing(self, _guard, _encode, temp_dir):
        """Verify _in_progress set is cleared after processing completes."""
        from audio_transcode_watcher.sync import _in_progress, _in_progress_lock

        source = Path(temp_dir) / "source"
        source.mkdir()
        flac = source / "Song.flac"
        flac.touch()

        out = Path(temp_dir) / "output"
        out.mkdir()

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="mp3", codec="mp3", path=str(out), bitrate="256k")],
            fetch_lyrics=False,
        )

        process_source_file(str(flac), config, force=True, check_stable=False)

        with _in_progress_lock:
            assert str(flac) not in _in_progress


class TestCleanupStaleTempFiles:
    """Tests for cleanup_stale_temp_files function."""

    def test_removes_tmp_ff_files(self, temp_dir):
        """Remove files with .tmp__ff suffix from output directories."""
        source = Path(temp_dir) / "source"
        source.mkdir()
        (source / "file.flac").touch()

        output = Path(temp_dir) / "output"
        output.mkdir()
        stale = output / "song.m4a.tmp__ff"
        stale.write_text("stale")

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="out", codec="alac", path=str(output))],
        )

        cleaned = cleanup_stale_temp_files(config)
        assert cleaned == 1
        assert not stale.exists()

    def test_returns_zero_when_no_temp_files(self, temp_dir):
        """Return 0 when no stale temp files exist."""
        source = Path(temp_dir) / "source"
        source.mkdir()
        (source / "file.flac").touch()

        output = Path(temp_dir) / "output"
        output.mkdir()
        (output / "song.m4a").touch()

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="out", codec="alac", path=str(output))],
        )

        assert cleanup_stale_temp_files(config) == 0

    def test_skips_nonexistent_output_dirs(self, temp_dir):
        """Skip output directories that do not exist."""
        source = Path(temp_dir) / "source"
        source.mkdir()
        (source / "file.flac").touch()

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="out", codec="alac", path=str(Path(temp_dir) / "missing"))],
        )

        assert cleanup_stale_temp_files(config) == 0

    def test_handles_removal_error(self, temp_dir):
        """Handle errors when removing temp files."""
        source = Path(temp_dir) / "source"
        source.mkdir()
        (source / "file.flac").touch()

        output = Path(temp_dir) / "output"
        output.mkdir()
        stale = output / "song.m4a.tmp__ff"
        stale.write_text("stale")

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="out", codec="alac", path=str(output))],
        )

        with patch("os.remove", side_effect=PermissionError("denied")):
            cleaned = cleanup_stale_temp_files(config)

        assert cleaned == 0


class TestDeleteOutputsMp3WithLossless:
    """Tests for delete_outputs MP3 with lossless source."""

    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_mp3_delete_skips_non_alac_when_lossless_exists(self, _guard, temp_dir):
        """When deleting MP3 and lossless source exists, skip non-alac outputs."""
        source = Path(temp_dir) / "source"
        source.mkdir()
        (source / "Song.flac").touch()
        mp3 = source / "Song.mp3"
        mp3.touch()

        out_mp3 = Path(temp_dir) / "mp3out"
        out_mp3.mkdir()
        out_file = out_mp3 / "Song.mp3"
        out_file.touch()

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="mp3", codec="mp3", path=str(out_mp3), bitrate="256k")],
        )

        delete_outputs(str(mp3), config)

        # With lossless source existing, MP3 delete for non-alac codec should not remove
        assert out_file.exists()


class TestInitialSync:
    """Tests for initial_sync function."""

    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=True)
    def test_skips_when_safety_guard_active(self, _guard, temp_dir):
        """Skip initial sync when safety guard is active."""
        source = Path(temp_dir) / "source"
        source.mkdir()

        output = Path(temp_dir) / "output"
        output.mkdir()

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="out", codec="alac", path=str(output))],
        )

        # Should not raise
        initial_sync(config)

    @patch("audio_transcode_watcher.sync._cleanup_orphans")
    @patch("audio_transcode_watcher.sync.process_source_file")
    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_processes_source_files_and_cleans_orphans(self, _guard, mock_proc, mock_orphan, temp_dir):
        """Process all source files and clean up orphans."""
        source = Path(temp_dir) / "source"
        source.mkdir()
        (source / "Song1.flac").touch()
        (source / "Song2.flac").touch()

        output = Path(temp_dir) / "output"
        output.mkdir()

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="out", codec="alac", path=str(output))],
            parallel_workers=1,
        )

        initial_sync(config)

        assert mock_proc.call_count == 2
        mock_orphan.assert_called_once()

    @patch("audio_transcode_watcher.sync._cleanup_orphans")
    @patch("audio_transcode_watcher.sync.process_source_file")
    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_skips_orphan_cleanup_when_no_source_files(self, _guard, _proc, mock_orphan, temp_dir):
        """Skip orphan cleanup when no source files found to avoid wiping outputs."""
        source = Path(temp_dir) / "source"
        source.mkdir()
        # No audio files in source

        output = Path(temp_dir) / "output"
        output.mkdir()

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="out", codec="alac", path=str(output))],
        )

        initial_sync(config)

        mock_orphan.assert_not_called()

    @patch("audio_transcode_watcher.sync.purge_all_outputs")
    @patch("audio_transcode_watcher.sync.process_source_file")
    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_purges_when_force_reencode_enabled(self, _guard, _proc, mock_purge, temp_dir):
        """Purge all outputs when force_reencode is enabled."""
        source = Path(temp_dir) / "source"
        source.mkdir()
        (source / "Song.flac").touch()

        output = Path(temp_dir) / "output"
        output.mkdir()

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="out", codec="alac", path=str(output))],
            force_reencode=True,
        )

        initial_sync(config)

        mock_purge.assert_called_once()

    @patch("audio_transcode_watcher.sync.walk_audio_files", side_effect=Exception("disk error"))
    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_handles_source_scan_failure(self, _guard, _walk, temp_dir):
        """Gracefully handle failure to scan source directory."""
        source = Path(temp_dir) / "source"
        source.mkdir()

        output = Path(temp_dir) / "output"
        output.mkdir()

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="out", codec="alac", path=str(output))],
        )

        # Should not raise
        initial_sync(config)


class TestCleanupOrphansAlacMp3Lossless:
    """Tests for orphan cleanup with ALAC MP3 and lossless sources."""

    def test_removes_mp3_from_alac_when_lossless_exists(self, temp_dir):
        """Remove MP3 from ALAC folder when lossless source with same stem exists."""
        source = Path(temp_dir) / "source"
        source.mkdir()
        (source / "Song.flac").touch()

        output = Path(temp_dir) / "output"
        output.mkdir()
        mp3_in_alac = output / "Song.mp3"
        mp3_in_alac.touch()

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(output))],
        )

        source_rel_stems = {nfc("Song")}
        _cleanup_orphans(config, source_rel_stems)

        # MP3 should be removed because lossless source exists
        assert not mp3_in_alac.exists()


class TestPurgeAllOutputsErrorHandling:
    """Tests for purge error handling paths."""

    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_handles_file_removal_error(self, _guard, temp_dir):
        """Continue purging when individual file removal fails."""
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

        with patch("os.remove", side_effect=PermissionError("denied")):
            purge_all_outputs(config)

        # Files still exist because removal failed, but no exception raised
        assert (output / "file1.m4a").exists()


class TestSyncSidecarsErrorHandling:
    """Tests for sync_sidecars error path."""

    def test_handles_copy_error(self, temp_dir):
        """Continue without error when sidecar copy fails."""
        source = Path(temp_dir) / "source"
        source.mkdir()
        lrc = source / "Song.lrc"
        lrc.write_text("lyrics")

        output = Path(temp_dir) / "output"
        output.mkdir()

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="out", codec="aac", path=str(output))],
        )

        with patch("shutil.copy2", side_effect=PermissionError("denied")):
            # Should not raise
            sync_sidecars(str(source / "Song.flac"), config)
