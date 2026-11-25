"""Tests for the watcher module."""

import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from audio_transcode_watcher.config import Config, OutputConfig
from audio_transcode_watcher.watcher import AudioSyncHandler, start_watcher


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

    @patch("audio_transcode_watcher.watcher.process_source_file")
    @patch("audio_transcode_watcher.watcher.is_audio_file", return_value=False)
    @patch("time.sleep")
    def test_skips_non_audio_files(self, _sleep, _is_audio, mock_proc, handler):
        """Non-audio files are ignored by _process_later."""
        handler._process_later("/music/source/readme.txt")
        mock_proc.assert_not_called()

    @patch("audio_transcode_watcher.watcher.process_source_file")
    @patch("audio_transcode_watcher.watcher.safety_guard_active", return_value=True)
    @patch("audio_transcode_watcher.watcher.is_audio_file", return_value=True)
    @patch("time.sleep")
    def test_skips_when_safety_guard_active(
        self, _sleep, _is_audio, _safety, mock_proc, handler
    ):
        """Skip processing when safety guard is active."""
        handler._process_later("/music/source/song.flac")
        mock_proc.assert_not_called()


class TestOnCreatedAudio:
    """Tests for on_created with audio files."""

    @patch("audio_transcode_watcher.watcher.process_source_file")
    @patch("audio_transcode_watcher.watcher.safety_guard_active", return_value=False)
    @patch("audio_transcode_watcher.watcher.is_audio_file", return_value=True)
    @patch("time.sleep")
    def test_processes_audio_on_create(
        self, _sleep, _is_audio, _safety, mock_proc, handler
    ):
        """Audio files trigger _process_later on create."""
        handler.on_created(_event("/music/source/new_song.flac"))
        mock_proc.assert_called_once()


class TestOnModifiedSafetyGuard:
    """Tests for on_modified when safety guard is active."""

    @patch("audio_transcode_watcher.watcher.sync_sidecars")
    @patch("audio_transcode_watcher.watcher.safety_guard_active", return_value=True)
    def test_skips_all_when_safety_guard_active(self, _safety, mock_sync, handler):
        """Skip all processing when safety guard is active."""
        handler.on_modified(_event("/music/source/song.flac"))
        mock_sync.assert_not_called()


class TestOnMoved:
    """Tests for on_moved event handling."""

    @patch("audio_transcode_watcher.watcher.process_source_file")
    @patch("audio_transcode_watcher.watcher.safety_guard_active", return_value=False)
    @patch("audio_transcode_watcher.watcher.is_audio_file", return_value=True)
    @patch("audio_transcode_watcher.watcher.delete_outputs")
    @patch("time.sleep")
    def test_moves_audio_file(
        self, _sleep, mock_del, _is_audio, _safety, mock_proc, handler
    ):
        """Delete old outputs and process new location on audio file move."""
        handler.on_moved(
            _event(
                "/music/source/old.flac",
                dest_path="/music/source/new.flac",
            )
        )
        mock_del.assert_called_once()
        mock_proc.assert_called_once()

    @patch("audio_transcode_watcher.watcher.sync_sidecars")
    @patch("audio_transcode_watcher.watcher.delete_sidecars")
    def test_moves_sidecar_file(self, mock_del_sc, mock_sync_sc, handler):
        """Delete old sidecars and sync new sidecars on sidecar move."""
        handler.on_moved(
            _event(
                "/music/source/song.lrc",
                dest_path="/music/source/renamed.lrc",
            )
        )
        mock_del_sc.assert_called_once()
        mock_sync_sc.assert_called_once()

    def test_sidecar_moved_into_ignored_folder_is_not_copied(self, tmp_path):
        """A .lrc that Syncthing moves into .stversions/ stays out of the outputs."""
        src = tmp_path / "source"
        out = tmp_path / "output"
        (src / "Album").mkdir(parents=True)
        (out / "Album").mkdir(parents=True)
        (out / "Album" / "song.lrc").write_text("[00:01.00] Old")
        versions = src / ".stversions" / "Album"
        versions.mkdir(parents=True)
        moved = versions / "song~20240101-120000.lrc"
        moved.write_text("[00:01.00] Old")
        config = Config(
            source_path=str(src),
            outputs=[OutputConfig(name="mp3", codec="mp3", path=str(out))],
            ignored_files=[".stversions/"],
        )
        handler = AudioSyncHandler(config)

        handler.on_moved(_event(str(src / "Album" / "song.lrc"), dest_path=str(moved)))
        handler.on_created(_event(str(moved)))
        handler.on_modified(_event(str(moved)))

        assert not (out / ".stversions").exists()
        assert not (out / "Album" / "song.lrc").exists()

    @patch("audio_transcode_watcher.watcher.sync_sidecars")
    @patch("audio_transcode_watcher.watcher.delete_sidecars")
    def test_move_from_sidecar_to_non_sidecar(self, mock_del_sc, mock_sync_sc, handler):
        """Delete old sidecar, no sync when dest is not a sidecar."""
        handler.on_moved(
            _event(
                "/music/source/song.lrc",
                dest_path="/music/source/song.txt",
            )
        )
        mock_del_sc.assert_called_once()
        mock_sync_sc.assert_not_called()

    @patch("audio_transcode_watcher.watcher.sync_sidecars")
    @patch("audio_transcode_watcher.watcher.delete_sidecars")
    def test_move_from_non_sidecar_to_sidecar(self, mock_del_sc, mock_sync_sc, handler):
        """Sync new sidecar when moving from non-sidecar to sidecar."""
        handler.on_moved(
            _event(
                "/music/source/song.txt",
                dest_path="/music/source/song.lrc",
            )
        )
        mock_del_sc.assert_not_called()
        mock_sync_sc.assert_called_once()

    @patch("audio_transcode_watcher.watcher.AudioSyncHandler._reprocess_directory")
    @patch("audio_transcode_watcher.watcher.AudioSyncHandler._handle_directory_delete")
    def test_moves_directory(self, mock_handle_del, mock_reprocess, handler):
        """Handle directory move: delete old + reprocess new."""
        handler.on_moved(
            _event(
                "/music/source/old_album",
                is_directory=True,
                dest_path="/music/source/new_album",
            )
        )
        mock_handle_del.assert_called_once_with("/music/source/old_album")
        # _reprocess_directory is called in a thread, but we patched it at class level

    @patch("audio_transcode_watcher.watcher.delete_outputs")
    @patch("audio_transcode_watcher.watcher.is_audio_file", return_value=False)
    def test_move_non_audio_src_to_non_audio_dest(self, _is_audio, mock_del, handler):
        """Moving non-audio to non-audio does nothing."""
        handler.on_moved(
            _event(
                "/music/source/readme.txt",
                dest_path="/music/source/readme2.txt",
            )
        )
        mock_del.assert_not_called()


class TestOnDeletedDirectory:
    """Tests for on_deleted with directories."""

    @patch("audio_transcode_watcher.watcher.AudioSyncHandler._handle_directory_delete")
    def test_handles_directory_deletion(self, mock_handle, handler):
        """Delegate to _handle_directory_delete for directory events."""
        handler.on_deleted(_event("/music/source/album", is_directory=True))
        mock_handle.assert_called_once_with("/music/source/album")


class TestHandleDirectoryDelete:
    """Tests for _handle_directory_delete."""

    @patch("audio_transcode_watcher.watcher.safety_guard_active", return_value=True)
    def test_skips_when_safety_guard_active(self, _safety, handler):
        """Skip directory cleanup when safety guard is active."""
        handler._handle_directory_delete("/music/source/album")
        # No error, just returns

    @patch("audio_transcode_watcher.watcher.safety_guard_active", return_value=False)
    @patch("audio_transcode_watcher.watcher.remove_empty_dirs")
    def test_cleans_mirrored_output_subtree(self, mock_remove_empty, _safety, tmp_path):
        """Delete files in mirrored output subtree when source dir is removed."""
        src = tmp_path / "source"
        src.mkdir()
        out = tmp_path / "output"
        album_out = out / "album1"
        album_out.mkdir(parents=True)
        orphan = album_out / "song.m4a"
        orphan.write_text("data")

        config = Config(
            source_path=str(src),
            outputs=[OutputConfig(name="alac", codec="alac", path=str(out))],
        )
        h = AudioSyncHandler(config)
        h._handle_directory_delete(str(src / "album1"))

        assert not orphan.exists()

    @patch("audio_transcode_watcher.watcher.safety_guard_active", return_value=False)
    def test_ignores_relative_path_outside_source(self, _safety, handler):
        """Skip when deleted directory is not under source (rel starts with ..)."""
        handler._handle_directory_delete("/completely/different/path")
        # No error, no action

    @patch("audio_transcode_watcher.watcher.safety_guard_active", return_value=False)
    def test_ignores_source_root_itself(self, _safety, handler):
        """Skip when deleted directory is the source root itself."""
        handler._handle_directory_delete(handler.config.source_path)
        # No error, no action


class TestReprocessDirectory:
    """Tests for _reprocess_directory."""

    @patch("audio_transcode_watcher.watcher.process_source_file")
    @patch("audio_transcode_watcher.watcher.safety_guard_active", return_value=False)
    @patch("audio_transcode_watcher.watcher.is_audio_file", return_value=True)
    @patch("time.sleep")
    def test_reprocesses_all_audio_files(
        self, _sleep, _is_audio, _safety, mock_proc, tmp_path
    ):
        """Reprocess all audio files found in directory."""
        src = tmp_path / "source"
        src.mkdir()
        (src / "song1.flac").touch()
        (src / "song2.flac").touch()
        out = tmp_path / "output"
        out.mkdir()

        config = Config(
            source_path=str(src),
            outputs=[
                OutputConfig(name="mp3", codec="mp3", path=str(out), bitrate="192k")
            ],
        )
        h = AudioSyncHandler(config)
        h._reprocess_directory(str(src))

        assert mock_proc.call_count == 2


class TestStartWatcher:
    """Tests for start_watcher function."""

    def test_returns_observer_instance(self, tmp_path):
        """Return a running Observer that can be stopped."""
        src = tmp_path / "source"
        src.mkdir()
        out = tmp_path / "output"
        out.mkdir()
        config = Config(
            source_path=str(src),
            outputs=[
                OutputConfig(name="mp3", codec="mp3", path=str(out), bitrate="192k")
            ],
        )
        observer = start_watcher(config)
        assert observer.is_alive()
        observer.stop()
        observer.join(timeout=5)


class TestDirectoryDeleteKeepsIgnoredInOutputs:
    """A source folder delete leaves protected output paths alone."""

    def _run(self, tmp_path, keep):
        src = tmp_path / "source"
        (src / "Other").mkdir(parents=True)
        (src / "Other" / "b.flac").write_text("x")
        out = tmp_path / "output"
        (out / "Artist" / ".stversions").mkdir(parents=True)
        for rel in ("Artist/a.m4a", "Artist/.stversions/old.m4a", "Artist/.nomedia"):
            (out / rel).write_text("data")
        config = Config(
            source_path=str(src),
            outputs=[OutputConfig(name="aac", codec="aac", path=str(out))],
            ignored_files=["**/.stversions", "**/.nomedia"],
        )
        config.ignored_files_in_outputs = keep
        AudioSyncHandler(config)._handle_directory_delete(str(src / "Artist"))
        return out

    def test_protected_paths_survive(self, tmp_path):
        out = self._run(tmp_path, keep=True)
        assert not (out / "Artist" / "a.m4a").exists()
        assert (out / "Artist" / ".stversions" / "old.m4a").is_file()
        assert (out / "Artist" / ".nomedia").is_file()

    def test_without_the_key_everything_goes(self, tmp_path):
        out = self._run(tmp_path, keep=False)
        assert not (out / "Artist").exists()


class TestIgnoredEvents:
    """Events under ignored paths do nothing; moves across the line count."""

    @pytest.fixture
    def ign(self, tmp_path):
        src = tmp_path / "source"
        (src / "Album").mkdir(parents=True)
        out = tmp_path / "output"
        out.mkdir()
        config = Config(
            source_path=str(src),
            outputs=[OutputConfig(name="mp3", codec="mp3", path=str(out))],
            ignored_files=["**/.stversions"],
        )
        return AudioSyncHandler(config), src

    @patch("audio_transcode_watcher.watcher.AudioSyncHandler._process_later")
    @patch("audio_transcode_watcher.watcher.delete_outputs")
    def test_modified_and_deleted_do_nothing(self, mock_del, mock_proc, ign):
        handler, src = ign
        path = str(src / "Album" / ".stversions" / "song~1.flac")
        with patch(
            "audio_transcode_watcher.watcher.safety_guard_active", return_value=False
        ):
            handler.on_modified(_event(path))
            handler.on_deleted(_event(path))
            handler.on_deleted(_event(str(src / ".stversions"), is_directory=True))
        mock_del.assert_not_called()
        mock_proc.assert_not_called()

    @patch("audio_transcode_watcher.watcher.is_audio_file", return_value=True)
    @patch("audio_transcode_watcher.watcher.AudioSyncHandler._process_later")
    @patch("audio_transcode_watcher.watcher.delete_outputs")
    def test_move_into_ignored_folder_is_a_delete(
        self, mock_del, mock_proc, _audio, ign
    ):
        handler, src = ign
        old = str(src / "Album" / "song.flac")
        new = str(src / "Album" / ".stversions" / "song~1.flac")
        handler.on_moved(_event(old, dest_path=new))
        mock_del.assert_called_once_with(old, handler.config)
        mock_proc.assert_not_called()

    @patch("audio_transcode_watcher.watcher.is_audio_file", return_value=True)
    @patch("audio_transcode_watcher.watcher.AudioSyncHandler._process_later")
    @patch("audio_transcode_watcher.watcher.delete_outputs")
    def test_move_out_of_ignored_folder_is_a_create(
        self, mock_del, mock_proc, _audio, ign
    ):
        handler, src = ign
        old = str(src / "Album" / ".stversions" / "song~1.flac")
        new = str(src / "Album" / "song.flac")
        handler.on_moved(_event(old, dest_path=new))
        mock_del.assert_not_called()
        mock_proc.assert_called_once_with(new, force=True)

    @patch("audio_transcode_watcher.watcher.threading.Thread")
    @patch("audio_transcode_watcher.watcher.AudioSyncHandler._handle_directory_delete")
    def test_folder_moves_across_the_line(self, mock_dir_del, mock_thread, ign):
        handler, src = ign
        album = str(src / "Album")
        versions = str(src / ".stversions" / "Album")
        handler.on_moved(_event(album, is_directory=True, dest_path=versions))
        mock_dir_del.assert_called_once_with(album)
        mock_thread.assert_not_called()

        mock_dir_del.reset_mock()
        handler.on_moved(_event(versions, is_directory=True, dest_path=album))
        mock_dir_del.assert_not_called()
        mock_thread.assert_called_once()

    @patch("audio_transcode_watcher.watcher.AudioSyncHandler._process_later")
    def test_reprocess_directory_skips_ignored(self, mock_proc, ign):
        handler, src = ign
        for rel in ["Album/song.flac", "Album/.stversions/song~1.flac"]:
            (src / rel).parent.mkdir(parents=True, exist_ok=True)
            (src / rel).touch()
        handler._reprocess_directory(str(src / "Album"))
        mock_proc.assert_called_once_with(str(src / "Album" / "song.flac"), force=True)
