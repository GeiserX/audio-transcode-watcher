"""Tests for the lyrics module."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from audio_transcode_watcher.lyrics import extract_metadata, fetch_lyrics_for_file


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

        result = fetch_lyrics_for_file(str(audio))
        assert result is not None
        assert result.endswith(".lrc")
        assert os.path.isfile(result)
        content = open(result).read()
        assert "Radio gaga" in content

    @patch("audio_transcode_watcher.lyrics.syncedlyrics")
    @patch("audio_transcode_watcher.lyrics.extract_metadata")
    def test_returns_none_when_no_lyrics(self, mock_meta, mock_syncedlyrics, tmp_path):
        """Return None when syncedlyrics finds nothing."""
        audio = tmp_path / "Obscure Band - Niche Song.flac"
        audio.touch()
        mock_meta.return_value = ("Obscure Band", "Niche Song")
        mock_syncedlyrics.search.return_value = None

        result = fetch_lyrics_for_file(str(audio))
        assert result is None

    def test_returns_none_when_no_metadata(self, tmp_path):
        """Return None when metadata can't be extracted."""
        audio = tmp_path / "noinfo.flac"
        audio.touch()
        result = fetch_lyrics_for_file(str(audio))
        assert result is None
