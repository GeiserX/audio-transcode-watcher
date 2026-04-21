"""Tests for the watcher module."""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from audio_transcode_watcher.config import Config, OutputConfig
from audio_transcode_watcher.watcher import AudioSyncHandler


@pytest.fixture
def handler(tmp_path):
    """Create an AudioSyncHandler with a temp config."""
    src = tmp_path / "source"
    src.mkdir()
    out = tmp_path / "output"
    out.mkdir()
    config = Config(
        source_path=str(src),
        outputs=[
            OutputConfig(name="mp3", codec="mp3", path=str(out), bitrate="192k"),
        ],
    )
    return AudioSyncHandler(config)


def _event(src_path, is_directory=False, dest_path=None):
    """Create a minimal event object."""
    ev = SimpleNamespace(src_path=src_path, is_directory=is_directory)
    if dest_path is not None:
        ev.dest_path = dest_path
    return ev


class TestCooldown:
    """Tests for the event cooldown mechanism."""

    def test_not_cooling_down_initially(self, handler):
        assert not handler._is_cooling_down("/music/song.flac")

    def test_cooling_down_after_mark(self, handler):
        handler._mark_processed("/music/song.flac")
        assert handler._is_cooling_down("/music/song.flac")

    def test_cooldown_expires(self, handler):
        handler._mark_processed("/music/song.flac")
        # Monkey-patch the timestamp to simulate expiry
        key = "/music/song.flac"
        with handler._processed_lock:
            handler._processed_at[key] = time.monotonic() - handler._EVENT_COOLDOWN - 1
        assert not handler._is_cooling_down("/music/song.flac")

    def test_cooldown_per_file(self, handler):
        handler._mark_processed("/music/song1.flac")
        assert handler._is_cooling_down("/music/song1.flac")
        assert not handler._is_cooling_down("/music/song2.flac")


class TestOnModified:
    """Tests for on_modified event handling."""

    def test_ignores_directory_events(self, handler):
        with patch("audio_transcode_watcher.watcher.delete_outputs") as mock_del:
            handler.on_modified(_event("/music/subdir", is_directory=True))
            mock_del.assert_not_called()

    @patch("audio_transcode_watcher.watcher.sync_sidecars")
    @patch("audio_transcode_watcher.watcher.safety_guard_active", return_value=False)
    def test_syncs_sidecar_on_modify(self, _safety, mock_sync, handler):
        handler.on_modified(_event("/music/source/song.lrc"))
        mock_sync.assert_called_once()

    @patch("audio_transcode_watcher.watcher.process_source_file")
    @patch("audio_transcode_watcher.watcher.delete_outputs")
    @patch("audio_transcode_watcher.watcher.safety_guard_active", return_value=False)
    @patch("audio_transcode_watcher.watcher.is_audio_file", return_value=True)
    @patch("time.sleep")
    def test_skips_modify_during_cooldown(
        self, _sleep, _is_audio, _safety, mock_del, mock_proc, handler
    ):
        """The core bug fix: on_modified must not delete+re-encode during cooldown."""
        handler._mark_processed("/music/source/song.flac")
        handler.on_modified(_event("/music/source/song.flac"))
        mock_del.assert_not_called()
        mock_proc.assert_not_called()

    @patch("audio_transcode_watcher.watcher.process_source_file")
    @patch("audio_transcode_watcher.watcher.delete_outputs")
    @patch("audio_transcode_watcher.watcher.safety_guard_active", return_value=False)
    @patch("audio_transcode_watcher.watcher.is_audio_file", return_value=True)
    @patch("time.sleep")
    def test_processes_modify_after_cooldown(
        self, _sleep, _is_audio, _safety, mock_del, mock_proc, handler
    ):
        """Real modifications (after cooldown) must still delete+re-encode."""
        # Mark processed long ago
        key = "/music/source/song.flac"
        with handler._processed_lock:
            handler._processed_at[key] = time.monotonic() - handler._EVENT_COOLDOWN - 1
        handler.on_modified(_event(key))
        mock_del.assert_called_once()
        mock_proc.assert_called_once()

    @patch("audio_transcode_watcher.watcher.delete_outputs")
    @patch("audio_transcode_watcher.watcher.safety_guard_active", return_value=False)
    def test_ignores_non_audio_non_sidecar(self, _safety, mock_del, handler):
        """Non-audio, non-sidecar files (e.g. .txt) must be ignored."""
        handler.on_modified(_event("/music/source/notes.txt"))
        mock_del.assert_not_called()


class TestOnCreated:
    """Tests for on_created event handling."""

    def test_ignores_directory_events(self, handler):
        with patch("audio_transcode_watcher.watcher.sync_sidecars") as mock_sync:
            handler.on_created(_event("/music/subdir", is_directory=True))
            mock_sync.assert_not_called()

    @patch("audio_transcode_watcher.watcher.sync_sidecars")
    def test_syncs_sidecar_on_create(self, mock_sync, handler):
        handler.on_created(_event("/music/source/song.lrc"))
        mock_sync.assert_called_once()


class TestOnDeleted:
    """Tests for on_deleted event handling."""

    @patch("audio_transcode_watcher.watcher.delete_outputs")
    def test_deletes_outputs_for_audio(self, mock_del, handler):
        handler.on_deleted(_event("/music/source/song.flac"))
        mock_del.assert_called_once()

    @patch("audio_transcode_watcher.watcher.delete_sidecars")
    def test_deletes_sidecars_for_lrc(self, mock_del, handler):
        handler.on_deleted(_event("/music/source/song.lrc"))
        mock_del.assert_called_once()

    @patch("audio_transcode_watcher.watcher.delete_outputs")
    def test_ignores_non_audio(self, mock_del, handler):
        handler.on_deleted(_event("/music/source/notes.txt"))
        mock_del.assert_not_called()


class TestProcessLater:
    """Tests for _process_later marking cooldown."""

    @patch("audio_transcode_watcher.watcher.process_source_file")
    @patch("audio_transcode_watcher.watcher.safety_guard_active", return_value=False)
    @patch("audio_transcode_watcher.watcher.is_audio_file", return_value=True)
    @patch("time.sleep")
    def test_marks_processed_after_encoding(
        self, _sleep, _is_audio, _safety, _proc, handler
    ):
        handler._process_later("/music/source/song.flac")
        assert handler._is_cooling_down("/music/source/song.flac")
