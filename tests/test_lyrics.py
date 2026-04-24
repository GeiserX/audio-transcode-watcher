"""Tests for the lyrics module."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from audio_transcode_watcher.lyrics import (
    _get_whisper_model,
    _segments_to_lrc,
    _transcribe_with_whisper,
    _write_lrc,
    extract_metadata,
    fetch_lyrics_for_file,
)


class TestExtractMetadata:
    """Tests for extract_metadata()."""

    def test_from_filename_artist_title(self, tmp_path):
        """Parse 'Artist - Title.flac' filename."""
        f = tmp_path / "Queen - Bohemian Rhapsody.flac"
        f.touch()
        result = extract_metadata(str(f))
        assert result == ("Queen", "Bohemian Rhapsody")

    def test_from_filename_with_track_number(self, tmp_path):
        """Strip leading track number."""
        f = tmp_path / "03 - Radiohead - Karma Police.flac"
        f.touch()
        result = extract_metadata(str(f))
        assert result == ("Radiohead", "Karma Police")

    def test_from_filename_no_separator(self, tmp_path):
        """Return None when filename has no ' - ' separator."""
        f = tmp_path / "just_a_filename.flac"
        f.touch()
        result = extract_metadata(str(f))
        assert result is None

    @patch("audio_transcode_watcher.lyrics.mutagen.File")
    def test_from_embedded_metadata(self, mock_mutagen, tmp_path):
        """Prefer embedded metadata over filename."""
        f = tmp_path / "unknown.flac"
        f.touch()
        mock_audio = MagicMock()
        mock_audio.tags = {"artist": ["Pink Floyd"], "title": ["Comfortably Numb"]}
        mock_mutagen.return_value = mock_audio
        result = extract_metadata(str(f))
        assert result == ("Pink Floyd", "Comfortably Numb")

    @patch("audio_transcode_watcher.lyrics.mutagen.File")
    def test_falls_back_to_filename_on_empty_tags(self, mock_mutagen, tmp_path):
        """Fall back to filename when tags are empty."""
        f = tmp_path / "AC DC - Thunderstruck.flac"
        f.touch()
        mock_audio = MagicMock()
        mock_audio.tags = {}
        mock_mutagen.return_value = mock_audio
        result = extract_metadata(str(f))
        assert result == ("AC DC", "Thunderstruck")


class TestSegmentsToLrc:
    """Tests for _segments_to_lrc()."""

    def test_converts_segments(self):
        segments = [
            {"start": 0.0, "text": "Hello world"},
            {"start": 65.5, "text": "Second line"},
        ]
        result = _segments_to_lrc(segments)
        assert "[00:00.00] Hello world" in result
        assert "[01:05.50] Second line" in result

    def test_skips_empty_text(self):
        segments = [
            {"start": 0.0, "text": "  "},
            {"start": 5.0, "text": "Real line"},
        ]
        result = _segments_to_lrc(segments)
        assert "Real line" in result
        assert result.count("[") == 1


class TestFetchLyricsForFile:
    """Tests for fetch_lyrics_for_file()."""

    def test_skips_when_lrc_exists(self, tmp_path):
        """Skip fetching when .lrc file already exists."""
        audio = tmp_path / "Artist - Song.flac"
        audio.touch()
        lrc = tmp_path / "Artist - Song.lrc"
        lrc.write_text("[00:01.00] Existing lyrics")
        result = fetch_lyrics_for_file(str(audio))
        assert result is None

    @patch("audio_transcode_watcher.lyrics.syncedlyrics")
    @patch("audio_transcode_watcher.lyrics.extract_metadata")
    def test_fetches_and_writes_lyrics(self, mock_meta, mock_syncedlyrics, tmp_path):
        """Fetch lyrics and write .lrc file."""
        audio = tmp_path / "Queen - Radio Gaga.flac"
        audio.touch()
        mock_meta.return_value = ("Queen", "Radio Gaga")
        mock_syncedlyrics.search.return_value = "[00:01.00] All we hear is\n[00:03.00] Radio gaga"

        result = fetch_lyrics_for_file(str(audio), whisper_fallback=False)
        assert result is not None
        assert result.endswith(".lrc")
        assert os.path.isfile(result)
        content = open(result).read()
        assert "Radio gaga" in content

    @patch("audio_transcode_watcher.lyrics._transcribe_with_whisper")
    @patch("audio_transcode_watcher.lyrics.syncedlyrics")
    @patch("audio_transcode_watcher.lyrics.extract_metadata")
    def test_falls_back_to_whisper(self, mock_meta, mock_syncedlyrics, mock_whisper, tmp_path):
        """Fall back to Whisper when syncedlyrics returns nothing."""
        audio = tmp_path / "Niche Band - Rare Song.flac"
        audio.touch()
        mock_meta.return_value = ("Niche Band", "Rare Song")
        mock_syncedlyrics.search.return_value = None
        mock_whisper.return_value = "[00:00.00] Transcribed by whisper"

        result = fetch_lyrics_for_file(str(audio), whisper_fallback=True)
        assert result is not None
        assert os.path.isfile(result)
        content = open(result).read()
        assert "Transcribed by whisper" in content
        mock_whisper.assert_called_once()

    @patch("audio_transcode_watcher.lyrics._transcribe_with_whisper")
    @patch("audio_transcode_watcher.lyrics.syncedlyrics")
    @patch("audio_transcode_watcher.lyrics.extract_metadata")
    def test_whisper_disabled(self, mock_meta, mock_syncedlyrics, mock_whisper, tmp_path):
        """Don't use Whisper when disabled."""
        audio = tmp_path / "Artist - Song.flac"
        audio.touch()
        mock_meta.return_value = ("Artist", "Song")
        mock_syncedlyrics.search.return_value = None

        result = fetch_lyrics_for_file(str(audio), whisper_fallback=False)
        assert result is None
        mock_whisper.assert_not_called()

    @patch("audio_transcode_watcher.lyrics.syncedlyrics")
    @patch("audio_transcode_watcher.lyrics.extract_metadata")
    def test_returns_none_when_no_lyrics(self, mock_meta, mock_syncedlyrics, tmp_path):
        """Return None when nothing is found."""
        audio = tmp_path / "Obscure Band - Niche Song.flac"
        audio.touch()
        mock_meta.return_value = ("Obscure Band", "Niche Song")
        mock_syncedlyrics.search.return_value = None

        result = fetch_lyrics_for_file(str(audio), whisper_fallback=False)
        assert result is None

    def test_returns_none_when_no_metadata(self, tmp_path):
        """Return None when metadata can't be extracted."""
        audio = tmp_path / "noinfo.flac"
        audio.touch()
        result = fetch_lyrics_for_file(str(audio), whisper_fallback=False)
        assert result is None

    @patch("audio_transcode_watcher.lyrics.syncedlyrics")
    @patch("audio_transcode_watcher.lyrics.extract_metadata")
    def test_handles_syncedlyrics_exception(self, mock_meta, mock_syncedlyrics, tmp_path):
        """Handle exception from syncedlyrics.search gracefully."""
        audio = tmp_path / "Artist - Song.flac"
        audio.touch()
        mock_meta.return_value = ("Artist", "Song")
        mock_syncedlyrics.search.side_effect = Exception("network error")

        result = fetch_lyrics_for_file(str(audio), whisper_fallback=False)
        assert result is None

    @patch("audio_transcode_watcher.lyrics._transcribe_with_whisper")
    @patch("audio_transcode_watcher.lyrics.syncedlyrics")
    @patch("audio_transcode_watcher.lyrics.extract_metadata")
    def test_whisper_fallback_with_no_metadata(self, mock_meta, mock_syncedlyrics, mock_whisper, tmp_path):
        """Use filename stem as label when whisper fallback has no metadata."""
        audio = tmp_path / "instrumental.flac"
        audio.touch()
        mock_meta.return_value = None
        mock_whisper.return_value = "[00:00.00] Instrumental"

        result = fetch_lyrics_for_file(str(audio), whisper_fallback=True)
        assert result is not None
        assert result.endswith(".lrc")
        content = open(result).read()
        assert "Instrumental" in content


class TestGetWhisperModel:
    """Tests for _get_whisper_model function."""

    def test_returns_none_when_load_previously_failed(self):
        """Return None immediately if a prior load attempt failed."""
        import audio_transcode_watcher.lyrics as lmod

        original_failed = lmod._whisper_load_failed
        original_model = lmod._whisper_model
        try:
            lmod._whisper_load_failed = True
            lmod._whisper_model = None
            result = _get_whisper_model("base")
            assert result is None
        finally:
            lmod._whisper_load_failed = original_failed
            lmod._whisper_model = original_model

    def test_returns_cached_model(self):
        """Return cached model if already loaded."""
        import audio_transcode_watcher.lyrics as lmod

        original_failed = lmod._whisper_load_failed
        original_model = lmod._whisper_model
        try:
            lmod._whisper_load_failed = False
            sentinel = MagicMock()
            lmod._whisper_model = sentinel
            result = _get_whisper_model("base")
            assert result is sentinel
        finally:
            lmod._whisper_load_failed = original_failed
            lmod._whisper_model = original_model

    @patch("audio_transcode_watcher.lyrics.whisper", create=True)
    def test_loads_model_on_first_call(self, mock_whisper_module):
        """Load whisper model on first call and cache it."""
        import audio_transcode_watcher.lyrics as lmod

        original_failed = lmod._whisper_load_failed
        original_model = lmod._whisper_model
        try:
            lmod._whisper_load_failed = False
            lmod._whisper_model = None

            sentinel = MagicMock()

            # We need to patch the import inside _get_whisper_model
            with patch.dict("sys.modules", {"whisper": mock_whisper_module}):
                mock_whisper_module.load_model.return_value = sentinel
                result = _get_whisper_model("tiny")
                assert result is sentinel
                assert lmod._whisper_model is sentinel
        finally:
            lmod._whisper_load_failed = original_failed
            lmod._whisper_model = original_model

    def test_sets_failed_flag_on_import_error(self):
        """Set _whisper_load_failed when whisper import fails."""
        import audio_transcode_watcher.lyrics as lmod

        original_failed = lmod._whisper_load_failed
        original_model = lmod._whisper_model
        try:
            lmod._whisper_load_failed = False
            lmod._whisper_model = None

            with patch.dict("sys.modules", {"whisper": None}):
                result = _get_whisper_model("base")
                assert result is None
                assert lmod._whisper_load_failed is True
        finally:
            lmod._whisper_load_failed = original_failed
            lmod._whisper_model = original_model


class TestTranscribeWithWhisper:
    """Tests for _transcribe_with_whisper function."""

    @patch("audio_transcode_watcher.lyrics._get_whisper_model")
    def test_returns_none_when_model_unavailable(self, mock_get):
        """Return None when whisper model cannot be loaded."""
        mock_get.return_value = None
        result = _transcribe_with_whisper("/music/song.flac")
        assert result is None

    @patch("audio_transcode_watcher.lyrics._get_whisper_model")
    def test_returns_lrc_on_successful_transcription(self, mock_get):
        """Return LRC content on successful Whisper transcription."""
        mock_model = MagicMock()
        mock_model.transcribe.return_value = {
            "segments": [
                {"start": 0.0, "text": "Hello world"},
                {"start": 5.5, "text": "Second line"},
            ]
        }
        mock_get.return_value = mock_model

        result = _transcribe_with_whisper("/music/song.flac", "base")
        assert result is not None
        assert "Hello world" in result
        assert "Second line" in result

    @patch("audio_transcode_watcher.lyrics._get_whisper_model")
    def test_returns_none_when_no_segments(self, mock_get):
        """Return None when Whisper produces no segments."""
        mock_model = MagicMock()
        mock_model.transcribe.return_value = {"segments": []}
        mock_get.return_value = mock_model

        result = _transcribe_with_whisper("/music/song.flac")
        assert result is None

    @patch("audio_transcode_watcher.lyrics._get_whisper_model")
    def test_returns_none_on_transcription_exception(self, mock_get):
        """Return None when Whisper transcription raises exception."""
        mock_model = MagicMock()
        mock_model.transcribe.side_effect = RuntimeError("CUDA error")
        mock_get.return_value = mock_model

        result = _transcribe_with_whisper("/music/song.flac")
        assert result is None


class TestWriteLrc:
    """Tests for _write_lrc function."""

    def test_writes_content_to_file(self, tmp_path):
        """Write LRC content and return path."""
        lrc_path = str(tmp_path / "song.lrc")
        result = _write_lrc(lrc_path, "[00:01.00] Hello", "Artist - Song", "syncedlyrics")
        assert result == lrc_path
        assert open(lrc_path).read() == "[00:01.00] Hello"

    def test_returns_none_on_write_error(self, tmp_path):
        """Return None when writing fails."""
        with patch("builtins.open", side_effect=PermissionError("denied")):
            result = _write_lrc("/impossible/path.lrc", "content", "label", "source")
        assert result is None
