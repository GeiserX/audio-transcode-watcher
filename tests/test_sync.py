"""Tests for sync module."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

import audio_transcode_watcher.sync as sync_mod
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


@pytest.fixture(autouse=True)
def _no_age_guards(monkeypatch):
    """Let orphan and temp cleanup act on files created by the test itself.

    Tests of the age guards set the real thresholds back explicitly.
    """
    monkeypatch.setattr(sync_mod, "ORPHAN_MIN_AGE", 0.0)
    monkeypatch.setattr(sync_mod, "TEMP_MIN_AGE", 0.0)
    sync_mod._failed_sources.clear()
    yield
    sync_mod._failed_sources.clear()


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

    def test_active_when_output_empty_and_bulk_encode_disabled(
        self, source_dir, temp_dir
    ):
        """Test safety guard is active when output empty and bulk encode disabled."""
        empty_output = Path(temp_dir) / "empty_output"
        empty_output.mkdir()

        config = Config(
            source_path=source_dir,
            outputs=[OutputConfig(name="out", codec="alac", path=str(empty_output))],
            allow_initial_bulk_encode=False,  # Disable bulk encoding
        )

        assert safety_guard_active(config) is True

    def test_inactive_when_output_empty_and_bulk_encode_enabled(
        self, source_dir, temp_dir
    ):
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
    def test_skips_non_audio_files(
        self, mock_guard, mock_encode, sample_config, temp_dir
    ):
        """Test that non-audio files are skipped."""
        mock_guard.return_value = False

        # Create a non-audio file
        non_audio = Path(temp_dir) / "readme.txt"
        non_audio.write_text("test")

        process_source_file(str(non_audio), sample_config, check_stable=False)

        mock_encode.assert_not_called()

    @patch("audio_transcode_watcher.sync.atomic_ffmpeg_encode")
    @patch("audio_transcode_watcher.sync.safety_guard_active")
    def test_skips_when_safety_guard_active(
        self, mock_guard, mock_encode, sample_config, source_dir
    ):
        """Test that processing is skipped when safety guard is active."""
        mock_guard.return_value = True

        audio_file = Path(source_dir) / "Artist - Song 1.flac"
        process_source_file(str(audio_file), sample_config, check_stable=False)

        mock_encode.assert_not_called()

    @patch("audio_transcode_watcher.sync.atomic_ffmpeg_encode")
    @patch("audio_transcode_watcher.sync.safety_guard_active")
    def test_processes_audio_files(
        self, mock_guard, mock_encode, sample_config, source_dir
    ):
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
        # The orphan pass never runs against an empty source.
        (source / "Other - Song.flac").touch()

        output = Path(temp_dir) / "output"
        output.mkdir()
        orphan = output / "Orphan - Song.m4a"
        orphan.touch()

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(output))],
        )

        _cleanup_orphans(config)

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

        _cleanup_orphans(config)

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

        _cleanup_orphans(config)

        # MP3 copy should be kept
        assert mp3_copy.exists()

    def test_recursive_removes_orphan_in_subdir(self, temp_dir):
        """Test that orphaned files in subdirectories are removed."""
        source = Path(temp_dir) / "source"
        source.mkdir()
        # The orphan pass never runs against an empty source.
        (source / "Other - Song.flac").touch()

        output = Path(temp_dir) / "output"
        (output / "album1").mkdir(parents=True)
        orphan = output / "album1" / "Orphan.m4a"
        orphan.touch()

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(output))],
        )

        _cleanup_orphans(config)

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

        _cleanup_orphans(config)

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
        # The orphan pass never runs against an empty source.
        (source / "Other - Song.flac").touch()

        output = Path(temp_dir) / "output"
        output.mkdir()
        orphan_lrc = output / "Deleted - Song.lrc"
        orphan_lrc.touch()

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="out", codec="aac", path=str(output))],
        )

        _cleanup_orphans(config)

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

        _cleanup_orphans(config)

        assert valid_lrc.exists()

    def test_recursive_removes_orphan_lrc_in_subdir(self, temp_dir):
        """Test that orphaned .lrc in subdirectories are removed."""
        source = Path(temp_dir) / "source"
        source.mkdir()
        # The orphan pass never runs against an empty source.
        (source / "Other - Song.flac").touch()

        output = Path(temp_dir) / "output"
        (output / "album").mkdir(parents=True)
        orphan_lrc = output / "album" / "Gone.lrc"
        orphan_lrc.touch()

        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="out", codec="aac", path=str(output))],
        )

        _cleanup_orphans(config)

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
            outputs=[
                OutputConfig(
                    name="alac", codec="alac", path=str(Path(temp_dir) / "out")
                )
            ],
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
            outputs=[
                OutputConfig(
                    name="alac", codec="alac", path=str(Path(temp_dir) / "out")
                )
            ],
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
    def test_mp3_skipped_when_lossless_source_exists(
        self, _guard, mock_encode, temp_dir
    ):
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
    def test_skips_when_file_not_stable(
        self, _guard, mock_encode, _stable, source_dir, sample_config
    ):
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
            outputs=[
                OutputConfig(name="mp3", codec="mp3", path=str(out), bitrate="256k")
            ],
        )

        # Should not raise; just logs the error
        process_source_file(str(flac), config, force=True, check_stable=False)
        mock_encode.assert_called_once()


class TestProcessSourceFileLyrics:
    """Tests for lyrics fetching in process_source_file."""

    @patch("audio_transcode_watcher.sync.sync_sidecars")
    @patch(
        "audio_transcode_watcher.sync.fetch_lyrics_for_file",
        side_effect=Exception("network"),
    )
    @patch("audio_transcode_watcher.sync.atomic_ffmpeg_encode", return_value=0)
    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_lyrics_failure_does_not_stop_processing(
        self, _guard, _encode, _lyrics, _sync, temp_dir
    ):
        """Lyrics fetch failure does not prevent sync_sidecars from running."""
        source = Path(temp_dir) / "source"
        source.mkdir()
        flac = source / "Song.flac"
        flac.touch()

        out = Path(temp_dir) / "output"
        out.mkdir()

        config = Config(
            source_path=str(source),
            outputs=[
                OutputConfig(name="mp3", codec="mp3", path=str(out), bitrate="256k")
            ],
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
            outputs=[
                OutputConfig(name="mp3", codec="mp3", path=str(out), bitrate="256k")
            ],
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
            outputs=[
                OutputConfig(
                    name="out", codec="alac", path=str(Path(temp_dir) / "missing")
                )
            ],
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
            outputs=[
                OutputConfig(name="mp3", codec="mp3", path=str(out_mp3), bitrate="256k")
            ],
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
    def test_processes_source_files_and_cleans_orphans(
        self, _guard, mock_proc, mock_orphan, temp_dir
    ):
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
    def test_skips_orphan_cleanup_when_no_source_files(
        self, _guard, _proc, mock_orphan, temp_dir
    ):
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
    def test_purges_when_force_reencode_enabled(
        self, _guard, _proc, mock_purge, temp_dir
    ):
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

    @patch(
        "audio_transcode_watcher.sync.walk_audio_files",
        side_effect=Exception("disk error"),
    )
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

        _cleanup_orphans(config)

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


LOSSY = [".mp3", ".aac", ".m4a", ".ogg", ".opus", ".wma"]


def _dirs(temp_dir, *names):
    paths = []
    for name in names:
        p = Path(temp_dir) / name
        p.mkdir(parents=True, exist_ok=True)
        paths.append(p)
    return paths


class TestLossySources:
    """Lossy sources are copied, never inflated into a lossless output."""

    @pytest.mark.parametrize("ext", LOSSY)
    @patch("audio_transcode_watcher.sync.atomic_ffmpeg_encode")
    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_lossy_source_copied_unchanged_into_alac(
        self, _guard, mock_encode, temp_dir, ext
    ):
        source, out = _dirs(temp_dir, "source", "alac")
        src = source / f"Artist - Title{ext}"
        src.write_bytes(b"lossy bytes with tags " + ext.encode())
        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(out))],
            fetch_lyrics=False,
        )

        process_source_file(str(src), config, check_stable=False)

        mock_encode.assert_not_called()
        assert [p.name for p in out.iterdir()] == [f"Artist - Title{ext}"]
        assert (out / f"Artist - Title{ext}").read_bytes() == src.read_bytes()

    @pytest.mark.parametrize(
        "ext,codec",
        [(".mp3", "mp3"), (".m4a", "aac"), (".aac", "aac"), (".opus", "opus")],
    )
    @patch("audio_transcode_watcher.sync.atomic_ffmpeg_encode")
    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_lossy_source_copied_into_lossy_output_of_same_codec(
        self, _guard, mock_encode, temp_dir, ext, codec
    ):
        source, out = _dirs(temp_dir, "source", "lossy")
        src = source / f"Song{ext}"
        src.write_bytes(b"original")
        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="lossy", codec=codec, path=str(out))],
            fetch_lyrics=False,
        )

        process_source_file(str(src), config, check_stable=False)

        mock_encode.assert_not_called()
        assert (out / f"Song{ext}").read_bytes() == b"original"

    @pytest.mark.parametrize(
        "ext,codec",
        [
            (".ogg", "mp3"),
            (".mp3", "aac"),
            (".m4a", "mp3"),
            (".wma", "opus"),
            (".opus", "aac"),
        ],
    )
    @patch("audio_transcode_watcher.sync.atomic_ffmpeg_encode", return_value=0)
    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_lossy_source_transcoded_into_lossy_output_of_other_codec(
        self, _guard, mock_encode, temp_dir, ext, codec
    ):
        source, out = _dirs(temp_dir, "source", "lossy")
        src = source / f"Song{ext}"
        src.write_bytes(b"original")
        output = OutputConfig(name="lossy", codec=codec, path=str(out))
        config = Config(source_path=str(source), outputs=[output], fetch_lyrics=False)

        process_source_file(str(src), config, check_stable=False)

        mock_encode.assert_called_once()
        assert mock_encode.call_args.args[1] == str(out / f"Song{output.extension}")

    @pytest.mark.parametrize("ext", [".tak", ".aif", ".flac"])
    @patch("audio_transcode_watcher.sync.atomic_ffmpeg_encode", return_value=0)
    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_lossless_source_encoded_with_tag_copy_for_mp4(
        self, _guard, mock_encode, temp_dir, ext
    ):
        source, alac, mp3 = _dirs(temp_dir, "source", "alac", "mp3")
        src = source / f"Song{ext}"
        src.touch()
        config = Config(
            source_path=str(source),
            outputs=[
                OutputConfig(name="alac", codec="alac", path=str(alac)),
                OutputConfig(name="mp3", codec="mp3", path=str(mp3)),
            ],
            fetch_lyrics=False,
        )

        process_source_file(str(src), config, check_stable=False)

        assert mock_encode.call_count == 2
        by_dest = {
            c.args[1]: c.kwargs.get("finalize") for c in mock_encode.call_args_list
        }
        assert by_dest[str(alac / "Song.m4a")] is not None
        assert by_dest[str(mp3 / "Song.mp3")] is None

    @patch("audio_transcode_watcher.sync.atomic_ffmpeg_encode")
    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_lossy_source_skipped_when_lossless_sibling_exists(
        self, _guard, mock_encode, temp_dir
    ):
        source, alac, mp3 = _dirs(temp_dir, "source", "alac", "mp3")
        (source / "Song.flac").touch()
        ogg = source / "Song.ogg"
        ogg.write_bytes(b"ogg")
        config = Config(
            source_path=str(source),
            outputs=[
                OutputConfig(name="alac", codec="alac", path=str(alac)),
                OutputConfig(name="mp3", codec="mp3", path=str(mp3)),
            ],
            fetch_lyrics=False,
        )

        process_source_file(str(ogg), config, check_stable=False)

        mock_encode.assert_not_called()
        assert list(alac.iterdir()) == [] and list(mp3.iterdir()) == []


class TestMixedExtensionOrphans:
    """An output can hold several extensions for one stem."""

    def test_alac_output_keeps_encodes_and_lossy_copies(self, temp_dir):
        source, alac = _dirs(temp_dir, "source", "alac")
        # A.mp3 sits beside A.flac in the source: the lossless file wins,
        # so the copy of A.mp3 made before A.flac arrived is an orphan.
        for name in ["A.flac", "A.mp3", "B.mp3", "C.ogg", "D.m4a", "E.wma"]:
            (source / name).touch()
        keep = ["A.m4a", "B.mp3", "C.ogg", "D.m4a", "E.wma"]
        gone = ["A.mp3", "B.m4a", "C.m4a", "Gone.ogg", "Gone.m4a"]
        for name in keep + gone:
            (alac / name).touch()
        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(alac))],
        )

        _cleanup_orphans(config)

        assert sorted(p.name for p in alac.iterdir()) == sorted(keep)

    def test_lossy_output_keeps_copies_and_transcodes(self, temp_dir):
        source, mp3 = _dirs(temp_dir, "source", "mp3")
        for name in ["A.flac", "B.mp3", "C.ogg"]:
            (source / name).touch()
        keep = ["A.mp3", "B.mp3", "C.mp3"]
        gone = ["C.ogg", "Gone.mp3"]
        for name in keep + gone:
            (mp3 / name).touch()
        config = Config(
            source_path=str(source),
            outputs=[
                OutputConfig(name="mp3", codec="mp3", path=str(mp3), bitrate="256k")
            ],
        )

        _cleanup_orphans(config)

        assert sorted(p.name for p in mp3.iterdir()) == sorted(keep)

    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_deleting_lossy_source_keeps_lossless_output_and_lrc(
        self, _guard, temp_dir
    ):
        source, alac = _dirs(temp_dir, "source", "alac")
        (source / "Song.flac").touch()
        (source / "Song.lrc").write_text("lyrics")
        (alac / "Song.m4a").touch()
        (alac / "Song.mp3").touch()
        (alac / "Song.lrc").write_text("lyrics")
        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(alac))],
        )

        # Song.mp3 was in the source and has just been deleted.
        delete_outputs(str(source / "Song.mp3"), config)

        assert sorted(p.name for p in alac.iterdir()) == ["Song.lrc", "Song.m4a"]

    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_deleting_last_source_of_stem_removes_lrc(self, _guard, temp_dir):
        source, alac = _dirs(temp_dir, "source", "alac")
        (alac / "Song.ogg").touch()
        (alac / "Song.lrc").write_text("lyrics")
        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(alac))],
        )

        delete_outputs(str(source / "Song.ogg"), config)

        assert list(alac.iterdir()) == []


class TestOrphanRaceGuards:
    """The periodic sync never removes an output the watcher just made."""

    def test_young_output_is_kept_until_old(self, temp_dir, monkeypatch):
        monkeypatch.setattr(sync_mod, "ORPHAN_MIN_AGE", 120.0)
        source, out = _dirs(temp_dir, "source", "alac")
        (source / "Other.flac").touch()
        fresh = out / "Fresh.m4a"
        fresh.touch()
        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(out))],
        )

        _cleanup_orphans(config)
        assert fresh.exists()

        real_time = sync_mod.time.time
        monkeypatch.setattr(sync_mod.time, "time", lambda: real_time() + 121)
        _cleanup_orphans(config)
        assert not fresh.exists()

    def test_young_source_keeps_its_stem(self, temp_dir, monkeypatch):
        monkeypatch.setattr(sync_mod, "ORPHAN_MIN_AGE", 120.0)
        source, out = _dirs(temp_dir, "source", "alac")
        (source / "Song.flac").touch()
        copy = out / "Song.mp3"  # orphan: the lossless source of the stem wins
        copy.touch()
        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(out))],
        )
        src_root = str(source)

        def age(path, now):
            return 0.0 if path.startswith(src_root) else 1000.0

        monkeypatch.setattr(sync_mod, "_age_seconds", age)
        _cleanup_orphans(config)
        assert copy.exists()

        monkeypatch.setattr(sync_mod, "_age_seconds", lambda path, now: 1000.0)
        _cleanup_orphans(config)
        assert not copy.exists()

    def test_in_progress_stem_is_kept(self, temp_dir, monkeypatch):
        monkeypatch.setattr(sync_mod, "_age_seconds", lambda path, now: 1000.0)
        source, out = _dirs(temp_dir, "source", "alac")
        flac = source / "Song.flac"
        flac.touch()
        copy = out / "Song.mp3"
        copy.touch()
        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(out))],
        )

        with sync_mod._in_progress_lock:
            sync_mod._in_progress.add(str(flac))
        try:
            _cleanup_orphans(config)
            assert copy.exists()
        finally:
            with sync_mod._in_progress_lock:
                sync_mod._in_progress.discard(str(flac))
        _cleanup_orphans(config)
        assert not copy.exists()

    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_file_arriving_during_sync_keeps_its_output(
        self, _guard, temp_dir, monkeypatch
    ):
        """The 2026-08-06 race: the list was built, a file arrived, the
        watcher encoded it, and the orphan pass deleted the fresh output."""
        monkeypatch.setattr(sync_mod, "_age_seconds", lambda path, now: 1000.0)
        source, out = _dirs(temp_dir, "source", "alac")
        (source / "Old.flac").touch()
        (out / "Old.m4a").touch()
        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(out))],
            parallel_workers=1,
        )

        def watcher_meanwhile(src_file, *_a, **_k):
            (source / "New.flac").touch()
            (out / "New.m4a").touch()

        with patch(
            "audio_transcode_watcher.sync.process_source_file",
            side_effect=watcher_meanwhile,
        ):
            initial_sync(config, periodic=True)

        assert (out / "New.m4a").exists()
        assert (out / "Old.m4a").exists()


class TestStaleTempAge:
    """A temp file younger than 10 minutes may be an encode in progress."""

    def test_young_temp_kept_old_temp_removed(self, temp_dir, monkeypatch):
        monkeypatch.setattr(sync_mod, "TEMP_MIN_AGE", 600.0)
        source, out = _dirs(temp_dir, "source", "alac")
        young = out / "young.m4a.tmp__ff"
        old = out / "old.m4a.tmp__ff"
        young.write_text("encoding")
        old.write_text("stale")
        past = old.stat().st_mtime - 601
        os.utime(old, (past, past))
        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(out))],
        )

        assert cleanup_stale_temp_files(config) == 1
        assert young.exists()
        assert not old.exists()


class TestFailedSourceMemory:
    """A source that failed is not retried on every scan, until it changes."""

    @patch("audio_transcode_watcher.sync.atomic_ffmpeg_encode", return_value=69)
    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_not_retried_until_mtime_changes(self, _guard, mock_encode, temp_dir):
        source, out = _dirs(temp_dir, "source", "alac")
        flac = source / "Etta James - I'd Rather Go Blind.flac"
        flac.touch()
        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(out))],
            fetch_lyrics=False,
        )

        process_source_file(str(flac), config, check_stable=False)
        process_source_file(str(flac), config, check_stable=False)
        assert mock_encode.call_count == 1

        later = flac.stat().st_mtime + 10
        os.utime(flac, (later, later))
        process_source_file(str(flac), config, check_stable=False)
        assert mock_encode.call_count == 2

    @patch("audio_transcode_watcher.sync.atomic_ffmpeg_encode", return_value=0)
    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_success_is_not_remembered(self, _guard, mock_encode, temp_dir):
        source, out = _dirs(temp_dir, "source", "alac")
        flac = source / "Song.flac"
        flac.touch()
        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(out))],
            fetch_lyrics=False,
        )

        process_source_file(str(flac), config, force=True, check_stable=False)
        process_source_file(str(flac), config, force=True, check_stable=False)
        assert mock_encode.call_count == 2


class TestSyncLogLabel:
    """The periodic pass no longer logs as if the service restarted."""

    @pytest.mark.parametrize(
        "periodic,label,other",
        [
            (True, "Periodic sync", "Initial sync"),
            (False, "Initial sync", "Periodic sync"),
        ],
    )
    @patch("audio_transcode_watcher.sync.process_source_file")
    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_label(self, _guard, _proc, temp_dir, caplog, periodic, label, other):
        source, out = _dirs(temp_dir, "source", "alac")
        (source / "Song.flac").touch()
        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(out))],
        )
        with caplog.at_level("INFO", logger="audio_transcode_watcher.sync"):
            initial_sync(config, periodic=periodic)
        text = caplog.text
        assert f"{label} …" in text and f"{label} complete." in text
        assert other not in text


needs_ffmpeg = pytest.mark.skipif(
    __import__("shutil").which("ffmpeg") is None,
    reason="ffmpeg is not installed; the real-file checks need it",
)


@needs_ffmpeg
class TestRealCorruptSource:
    """A truncated-on-decode FLAC never reaches the output folder."""

    def test_corrupt_flac_writes_no_output_and_is_remembered(self, temp_dir):
        import subprocess

        source, out = _dirs(temp_dir, "source", "alac")
        good = source / "good.tmp.flac"
        subprocess.run(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=5",
                "-c:a",
                "flac",
                str(good),
            ],
            check=True,
        )
        data = bytearray(good.read_bytes())
        good.unlink()
        for i in range(len(data) // 3, len(data) // 3 + 400):
            data[i] ^= 0x5A
        bad = source / "Etta James - I'd Rather Go Blind.flac"
        bad.write_bytes(bytes(data))
        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(out))],
            fetch_lyrics=False,
        )

        process_source_file(str(bad), config, check_stable=False)

        assert list(out.iterdir()) == []
        assert str(bad) in sync_mod._failed_sources

    def test_one_flipped_bit_is_caught(self, temp_dir):
        """ffmpeg 7.1 decodes this silently unless frame CRCs are checked."""
        import subprocess

        source, out = _dirs(temp_dir, "source", "alac")
        good = source / "good.tmp.flac"
        subprocess.run(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=5",
                "-c:a",
                "flac",
                str(good),
            ],
            check=True,
        )
        data = bytearray(good.read_bytes())
        good.unlink()
        data[len(data) // 2] ^= 0x01
        bad = source / "One Bit - Flipped.flac"
        bad.write_bytes(bytes(data))
        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(out))],
            fetch_lyrics=False,
        )

        process_source_file(str(bad), config, check_stable=False)

        assert list(out.iterdir()) == []


class TestLosslessWinsAtProcessTime:
    """A lossless source that arrives after its lossy twin replaces the copy."""

    @staticmethod
    def _fake_encode(cmd, dest, **_kwargs):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"ALAC encode of the FLAC")
        return 0

    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_m4a_copied_then_flac_arrives(self, _guard, temp_dir):
        source, alac = _dirs(temp_dir, "source", "alac")
        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(alac))],
            fetch_lyrics=False,
        )
        lossy = source / "X.m4a"
        lossy.write_bytes(b"lossy AAC from the bot")

        with patch("audio_transcode_watcher.sync.atomic_ffmpeg_encode") as enc:
            process_source_file(str(lossy), config, check_stable=False)
            enc.assert_not_called()
        assert (alac / "X.m4a").read_bytes() == b"lossy AAC from the bot"

        flac = source / "X.flac"
        flac.write_bytes(b"flac")
        with patch(
            "audio_transcode_watcher.sync.atomic_ffmpeg_encode",
            side_effect=self._fake_encode,
        ) as enc:
            process_source_file(str(flac), config, force=False, check_stable=False)

        enc.assert_called_once()
        assert enc.call_args.args[1] == str(alac / "X.m4a")
        assert (alac / "X.m4a").read_bytes() == b"ALAC encode of the FLAC"

    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_mp3_copy_removed_when_flac_arrives(self, _guard, temp_dir):
        source, alac = _dirs(temp_dir, "source", "alac")
        (source / "X.mp3").write_bytes(b"mp3")
        (alac / "X.mp3").write_bytes(b"mp3")
        (source / "X.flac").write_bytes(b"flac")
        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(alac))],
            fetch_lyrics=False,
        )

        with patch(
            "audio_transcode_watcher.sync.atomic_ffmpeg_encode",
            side_effect=self._fake_encode,
        ):
            process_source_file(str(source / "X.flac"), config, check_stable=False)

        assert sorted(p.name for p in alac.iterdir()) == ["X.m4a"]

    @patch("audio_transcode_watcher.sync.atomic_ffmpeg_encode")
    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_finished_encode_is_not_redone_on_every_scan(
        self, _guard, mock_encode, temp_dir
    ):
        source, alac = _dirs(temp_dir, "source", "alac")
        (source / "X.m4a").write_bytes(b"lossy AAC from the bot")
        (source / "X.flac").write_bytes(b"flac")
        (alac / "X.m4a").write_bytes(b"ALAC encode of the FLAC")
        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(alac))],
            fetch_lyrics=False,
        )

        process_source_file(str(source / "X.flac"), config, check_stable=False)

        mock_encode.assert_not_called()
        assert (alac / "X.m4a").read_bytes() == b"ALAC encode of the FLAC"

    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_remove_failure_is_logged(self, _guard, temp_dir, caplog):
        source, alac = _dirs(temp_dir, "source", "alac")
        (source / "X.mp3").write_bytes(b"mp3")
        (alac / "X.mp3").write_bytes(b"mp3")
        (source / "X.flac").write_bytes(b"flac")
        config = Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(alac))],
            fetch_lyrics=False,
        )

        with (
            patch("audio_transcode_watcher.sync.atomic_ffmpeg_encode", return_value=0),
            patch(
                "audio_transcode_watcher.sync.os.remove",
                side_effect=PermissionError("ro"),
            ),
        ):
            process_source_file(str(source / "X.flac"), config, check_stable=False)

        assert "Failed to remove lossy copy" in caplog.text


class TestErrorPaths:
    """Failure branches of the new sync code: logged, never fatal."""

    def _config(self, source, out, **kw):
        return Config(
            source_path=str(source),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(out))],
            fetch_lyrics=False,
            **kw,
        )

    def test_failure_memory_ignores_missing_files(self, temp_dir):
        missing = str(Path(temp_dir) / "gone.flac")
        sync_mod._remember_failure(missing)
        assert missing not in sync_mod._failed_sources
        sync_mod._failed_sources[missing] = 1.0
        assert sync_mod._is_known_failure(missing) is False

    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_failed_copy_leaves_no_temp(self, _guard, temp_dir, caplog):
        source, out = _dirs(temp_dir, "source", "alac")
        mp3 = source / "Song.mp3"
        mp3.write_bytes(b"mp3")
        config = self._config(source, out)

        with patch(
            "audio_transcode_watcher.sync.os.replace", side_effect=OSError("disk full")
        ):
            process_source_file(str(mp3), config, check_stable=False)

        assert list(out.iterdir()) == []
        assert "Copy failed" in caplog.text

    @patch("audio_transcode_watcher.sync.atomic_ffmpeg_encode")
    def test_safety_guard_between_outputs_stops_the_loop(self, mock_encode, temp_dir):
        source, out = _dirs(temp_dir, "source", "alac")
        flac = source / "Song.flac"
        flac.touch()
        config = self._config(source, out)
        # False for process_source_file's own check, True inside the loop.
        with patch(
            "audio_transcode_watcher.sync.safety_guard_active",
            side_effect=[False, True],
        ):
            process_source_file(str(flac), config, check_stable=False)
        mock_encode.assert_not_called()

    @patch("audio_transcode_watcher.sync.safety_guard_active", return_value=False)
    def test_delete_outputs_logs_remove_failure(self, _guard, temp_dir, caplog):
        source, out = _dirs(temp_dir, "source", "alac")
        (out / "Song.m4a").touch()
        config = self._config(source, out)
        with patch(
            "audio_transcode_watcher.sync.os.remove", side_effect=PermissionError("ro")
        ):
            delete_outputs(str(source / "Song.flac"), config)
        assert "Failed to remove" in caplog.text

    def test_initial_sync_logs_worker_errors_and_cleaned_temps(self, temp_dir, caplog):
        source, out = _dirs(temp_dir, "source", "alac")
        (source / "Song.flac").touch()
        (out / "old.m4a.tmp__ff").touch()
        config = self._config(source, out, parallel_workers=1)
        with (
            patch(
                "audio_transcode_watcher.sync.safety_guard_active", return_value=False
            ),
            patch(
                "audio_transcode_watcher.sync.process_source_file",
                side_effect=RuntimeError("boom"),
            ),
            caplog.at_level("INFO", logger="audio_transcode_watcher.sync"),
        ):
            initial_sync(config)
        assert "Cleaned up 1 stale temp files" in caplog.text
        assert "Error processing" in caplog.text and "boom" in caplog.text

    @patch("audio_transcode_watcher.sync._cleanup_orphans")
    @patch("audio_transcode_watcher.sync.process_source_file")
    def test_initial_sync_skips_orphans_when_guard_trips_late(
        self, _proc, mock_orphans, temp_dir, caplog
    ):
        source, out = _dirs(temp_dir, "source", "alac")
        (source / "Song.flac").touch()
        config = self._config(source, out, parallel_workers=1)
        with (
            patch(
                "audio_transcode_watcher.sync.safety_guard_active",
                side_effect=[False, True],
            ),
            caplog.at_level("INFO", logger="audio_transcode_watcher.sync"),
        ):
            initial_sync(config, periodic=True)
        mock_orphans.assert_not_called()
        assert "Periodic sync complete (partial)." in caplog.text

    def test_orphans_skip_when_source_scan_fails(self, temp_dir, caplog):
        source, out = _dirs(temp_dir, "source", "alac")
        (out / "Orphan.m4a").touch()
        with patch(
            "audio_transcode_watcher.sync.walk_audio_files", side_effect=OSError("io")
        ):
            _cleanup_orphans(self._config(source, out))
        assert (out / "Orphan.m4a").exists()
        assert "Failed to scan source for orphan cleanup" in caplog.text

    def test_orphans_skip_when_source_is_empty(self, temp_dir):
        source, out = _dirs(temp_dir, "source", "alac")
        (out / "Orphan.m4a").touch()
        _cleanup_orphans(self._config(source, out))
        assert (out / "Orphan.m4a").exists()

    def test_unreadable_age_counts_as_young(self, temp_dir):
        source, out = _dirs(temp_dir, "source", "alac")
        (source / "Song.flac").touch()
        (out / "Song.mp3").touch()  # orphan once age is known
        (out / "Gone.m4a").touch()
        with patch(
            "audio_transcode_watcher.sync._age_seconds", side_effect=OSError("stat")
        ):
            _cleanup_orphans(self._config(source, out))
        assert (out / "Song.mp3").exists() and (out / "Gone.m4a").exists()

    def test_orphan_remove_failure_is_logged(self, temp_dir, caplog):
        source, out = _dirs(temp_dir, "source", "alac")
        (source / "Song.flac").touch()
        (out / "Gone.m4a").touch()
        with patch(
            "audio_transcode_watcher.sync.os.remove", side_effect=PermissionError("ro")
        ):
            _cleanup_orphans(self._config(source, out))
        assert "Failed to remove" in caplog.text

    def test_orphan_pass_leaves_other_files_alone(self, temp_dir):
        source, out = _dirs(temp_dir, "source", "alac")
        (source / "Song.flac").touch()
        (out / "cover.jpg").touch()
        (out / "Song.m4a.tmp__ff").touch()
        _cleanup_orphans(self._config(source, out))
        assert (out / "cover.jpg").exists() and (out / "Song.m4a.tmp__ff").exists()

    def test_orphan_output_scan_failure_is_logged(self, temp_dir, caplog):
        source, out = _dirs(temp_dir, "source", "alac")
        (source / "Song.flac").touch()
        real_walk = os.walk
        raised = []

        def walk(path, *a, **k):
            # os is shared, so this also sees remove_empty_dirs: fail once.
            if str(path) == str(out) and not raised:
                raised.append(path)
                raise OSError("output gone")
            return real_walk(path, *a, **k)

        with patch("audio_transcode_watcher.sync.os.walk", side_effect=walk):
            _cleanup_orphans(self._config(source, out))
        assert "Failed to scan" in caplog.text and "for orphans" in caplog.text
