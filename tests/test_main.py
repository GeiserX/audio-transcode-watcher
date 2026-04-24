"""Tests for main entry point."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from audio_transcode_watcher.main import main, setup_logging


class TestSetupLogging:
    """Tests for setup_logging function."""

    def test_configures_logging_handlers(self):
        """Verify logging basicConfig is invoked (adds handlers)."""
        # Remove existing handlers so basicConfig can take effect
        root = logging.getLogger()
        original_handlers = root.handlers[:]
        original_level = root.level
        root.handlers.clear()
        root.setLevel(logging.WARNING)
        try:
            setup_logging()
            assert root.level == logging.INFO
        finally:
            root.handlers = original_handlers
            root.setLevel(original_level)

    @patch("sys.stdout")
    def test_reconfigures_stdout_encoding(self, mock_stdout):
        """Reconfigure stdout to utf-8 when reconfigure is available."""
        mock_stdout.reconfigure = MagicMock()
        setup_logging()
        mock_stdout.reconfigure.assert_called_with(encoding="utf-8")

    @patch("sys.stderr")
    def test_reconfigures_stderr_encoding(self, mock_stderr):
        """Reconfigure stderr to utf-8 when reconfigure is available."""
        mock_stderr.reconfigure = MagicMock()
        setup_logging()
        mock_stderr.reconfigure.assert_called_with(encoding="utf-8")

    @patch("sys.stdout", spec=[])
    def test_handles_missing_reconfigure(self, _mock_stdout):
        """No error when stdout lacks reconfigure method."""
        # spec=[] means no attributes, so hasattr(sys.stdout, 'reconfigure') is False
        setup_logging()  # Should not raise

    @patch("sys.stdout")
    def test_handles_reconfigure_exception(self, mock_stdout):
        """Gracefully handle exception during reconfigure."""
        mock_stdout.reconfigure = MagicMock(side_effect=Exception("broken"))
        setup_logging()  # Should not raise


class TestMain:
    """Tests for main function."""

    @patch("audio_transcode_watcher.main.load_config")
    def test_returns_1_on_config_error(self, mock_load):
        """Return 1 when configuration loading fails."""
        mock_load.side_effect = ValueError("bad config")
        assert main() == 1

    @patch("audio_transcode_watcher.main.start_watcher")
    @patch("audio_transcode_watcher.main.initial_sync")
    @patch("audio_transcode_watcher.main.load_config")
    @patch("os.path.isdir", return_value=False)
    def test_returns_1_when_source_dir_missing(
        self, _isdir, mock_load, _sync, _watcher
    ):
        """Return 1 when source directory does not exist."""
        from audio_transcode_watcher.config import Config, OutputConfig

        mock_load.return_value = Config(
            source_path="/nonexistent/path",
            outputs=[OutputConfig(name="mp3", codec="mp3", path="/tmp/out")],
        )
        assert main() == 1

    @patch("time.sleep", side_effect=KeyboardInterrupt)
    @patch("audio_transcode_watcher.main.start_watcher")
    @patch("audio_transcode_watcher.main.initial_sync")
    @patch("audio_transcode_watcher.main.load_config")
    @patch("os.path.isdir", return_value=True)
    def test_returns_0_on_keyboard_interrupt(
        self, _isdir, mock_load, _sync, mock_watcher, _sleep
    ):
        """Return 0 after graceful KeyboardInterrupt shutdown."""
        from audio_transcode_watcher.config import Config, OutputConfig

        mock_load.return_value = Config(
            source_path="/music/flac",
            outputs=[OutputConfig(name="mp3", codec="mp3", path="/tmp/out")],
        )
        mock_observer = MagicMock()
        mock_watcher.return_value = mock_observer

        assert main() == 0
        mock_observer.stop.assert_called_once()
        mock_observer.join.assert_called_once()

    @patch("gc.collect")
    @patch("audio_transcode_watcher.main.start_watcher")
    @patch("audio_transcode_watcher.main.initial_sync")
    @patch("audio_transcode_watcher.main.load_config")
    @patch("os.path.isdir", return_value=True)
    def test_periodic_sync_triggers_after_interval(
        self, _isdir, mock_load, mock_sync, mock_watcher, mock_gc
    ):
        """Trigger periodic sync after 5-minute interval."""
        from audio_transcode_watcher.config import Config, OutputConfig

        mock_load.return_value = Config(
            source_path="/music/flac",
            outputs=[OutputConfig(name="mp3", codec="mp3", path="/tmp/out")],
        )
        mock_watcher.return_value = MagicMock()

        # Simulate time progressing past the sync interval.
        # time.time() is called: once for last_sync init, then in each loop
        # iteration for the comparison + possibly for updating last_sync.
        # We use a counter so every call returns an incrementing value that
        # eventually crosses the 300-second threshold.
        counter = [0]
        def fake_time():
            counter[0] += 1
            # First call (last_sync = time.time()) -> 0
            # Subsequent calls return 400 so the condition triggers immediately
            return 0 if counter[0] == 1 else 400

        sleep_calls = [0]
        def fake_sleep(secs):
            sleep_calls[0] += 1
            if sleep_calls[0] >= 2:
                raise KeyboardInterrupt

        with patch("time.time", side_effect=fake_time), \
             patch("time.sleep", side_effect=fake_sleep):
            main()

        # initial_sync called once at startup + once in periodic loop
        assert mock_sync.call_count == 2
        mock_gc.assert_called_once()

    @patch("time.sleep", side_effect=KeyboardInterrupt)
    @patch("audio_transcode_watcher.main.start_watcher")
    @patch("audio_transcode_watcher.main.initial_sync")
    @patch("audio_transcode_watcher.main.load_config")
    @patch("os.path.isdir", return_value=True)
    def test_logs_output_with_bitrate(self, _isdir, mock_load, _sync, mock_watcher, _sleep):
        """Log output configuration including bitrate when present."""
        from audio_transcode_watcher.config import Config, OutputConfig

        mock_load.return_value = Config(
            source_path="/music/flac",
            outputs=[
                OutputConfig(name="mp3", codec="mp3", path="/tmp/mp3", bitrate="320k"),
                OutputConfig(name="alac", codec="alac", path="/tmp/alac"),
            ],
        )
        mock_watcher.return_value = MagicMock()

        # Should not raise -- exercises the bitrate logging branch
        assert main() == 0
