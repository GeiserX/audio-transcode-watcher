"""Tests for the lyrics module."""

from __future__ import annotations

import logging
import os
from unittest.mock import MagicMock, patch

import pytest

from audio_transcode_watcher.lyrics import (
    _write_lrc,
    extract_metadata,
    fetch_lyrics_for_file,
    lyrics_reject_reason,
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
        mock_syncedlyrics.search.return_value = (
            "[00:01.00] I'd sit alone and watch your light\n"
            "[00:04.00] My only friend through teenage nights\n"
            "[00:08.00] All we hear is\n"
            "[00:10.00] Radio gaga"
        )

        result = fetch_lyrics_for_file(str(audio))
        assert result is not None
        assert result.endswith(".lrc")
        assert os.path.isfile(result)
        content = open(result).read()
        assert "Radio gaga" in content


    @patch("audio_transcode_watcher.lyrics.syncedlyrics")
    @patch("audio_transcode_watcher.lyrics.extract_metadata")
    def test_returns_none_when_no_lyrics(self, mock_meta, mock_syncedlyrics, tmp_path):
        """Return None when nothing is found."""
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

    @patch("audio_transcode_watcher.lyrics.syncedlyrics")
    @patch("audio_transcode_watcher.lyrics.extract_metadata")
    def test_handles_syncedlyrics_exception(self, mock_meta, mock_syncedlyrics, tmp_path):
        """Handle exception from syncedlyrics.search gracefully."""
        audio = tmp_path / "Artist - Song.flac"
        audio.touch()
        mock_meta.return_value = ("Artist", "Song")
        mock_syncedlyrics.search.side_effect = Exception("network error")

        result = fetch_lyrics_for_file(str(audio))
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


GOOD_LRC = (
    "[00:12.10] I've been really tryin', baby\n"
    "[00:16.40] Tryin' to hold back this feeling for so long\n"
    "[00:21.00] And if you feel like I feel, baby\n"
    "[00:25.30] Then come on, oh, come on\n"
)


class TestLyricsRejectReason:
    """Junk results are rejected; real synced lyrics pass."""

    @pytest.mark.parametrize(
        "content,reason_part",
        [
            ("[00:01.00] ♪\n[00:05.00] ♪\n[00:09.00] ♪\n[00:13.00] ♪\n[00:17.00] ♪", "single repeated token"),
            ("[00:01.00] la la\n[00:02.00] La\n[00:03.00] la la la\n[00:04.00] la\n", "single repeated token"),
            ("[00:01.00] One line\n[00:02.00] Two lines\n[00:03.00] Three lines\n", "3 timed lines"),
            ("Plain line one\nPlain line two\nPlain line three\nPlain line four\nFive\n", "0 timed lines"),
            ("[00:01.00]\n[00:02.00]\n[00:03.00]\n[00:04.00]\n[00:05.00] Hello there\n", "1 timed lines"),
            ("[00:00.00] Lyrics by www.RentAnAdviser.com\n", "advertisement"),
            ("[00:00.00] https://example.com/lyrics\n[00:01.00]\n[00:02.00]\n[00:03.00]\n", "advertisement"),
            ("", "0 timed lines"),
        ],
    )
    def test_rejects(self, content, reason_part):
        reason = lyrics_reject_reason(content)
        assert reason is not None
        assert reason_part in reason

    def test_accepts_four_real_lines(self):
        assert lyrics_reject_reason(GOOD_LRC) is None

    def test_accepts_real_lyrics_with_one_ad_line(self):
        content = GOOD_LRC + "[00:30.00] Lyrics from www.example.com\n"
        assert lyrics_reject_reason(content) is None

    def test_accepts_lines_with_several_stamps(self):
        content = "[ti:Song]\n" + GOOD_LRC.replace("[00:25.30]", "[00:25.30][01:40.00]")
        assert lyrics_reject_reason(content) is None


class TestFetchWritesNothingForJunk:
    """When nothing usable comes back, no .lrc is written."""

    @pytest.mark.parametrize("returned", [None, ""])
    @patch("audio_transcode_watcher.lyrics.syncedlyrics")
    @patch("audio_transcode_watcher.lyrics.extract_metadata")
    def test_nothing_found_writes_nothing(self, mock_meta, mock_sl, returned, tmp_path):
        audio = tmp_path / "Obscure - Song.flac"
        audio.touch()
        mock_meta.return_value = ("Obscure", "Song")
        mock_sl.search.return_value = returned

        assert fetch_lyrics_for_file(str(audio)) is None
        assert list(tmp_path.glob("*.lrc")) == []

    @patch("audio_transcode_watcher.lyrics.syncedlyrics")
    @patch("audio_transcode_watcher.lyrics.extract_metadata")
    def test_rejected_result_writes_nothing_and_logs_why(self, mock_meta, mock_sl, tmp_path, caplog):
        audio = tmp_path / "Band - Instrumental.flac"
        audio.touch()
        mock_meta.return_value = ("Band", "Instrumental")
        mock_sl.search.return_value = "[00:00.00] ♪\n[00:10.00] ♪\n[00:20.00] ♪\n[00:30.00] ♪"

        with caplog.at_level(logging.INFO, logger="audio_transcode_watcher.lyrics"):
            assert fetch_lyrics_for_file(str(audio)) is None
        assert list(tmp_path.glob("*.lrc")) == []
        rejected = [r for r in caplog.records if "Rejected lyrics" in r.getMessage()]
        assert rejected and rejected[0].levelno == logging.INFO
        assert "single repeated token" in rejected[0].getMessage()

    @patch("audio_transcode_watcher.lyrics.syncedlyrics")
    def test_no_metadata_never_searches(self, mock_sl, tmp_path):
        audio = tmp_path / "noinfo.flac"
        audio.touch()
        assert fetch_lyrics_for_file(str(audio)) is None
        mock_sl.search.assert_not_called()


class TestLrcOwnership:
    """The .lrc in the source folder is owned like the source, mode 0664."""

    @patch("audio_transcode_watcher.lyrics.os.chown")
    @patch("audio_transcode_watcher.lyrics.syncedlyrics")
    @patch("audio_transcode_watcher.lyrics.extract_metadata")
    def test_chown_to_source_owner_and_mode_0664(self, mock_meta, mock_sl, mock_chown, tmp_path):
        audio = tmp_path / "Etta James - At Last.flac"
        audio.touch()
        mock_meta.return_value = ("Etta James", "At Last")
        mock_sl.search.return_value = GOOD_LRC

        lrc = fetch_lyrics_for_file(str(audio))
        assert lrc is not None
        st = os.stat(audio)
        mock_chown.assert_called_once_with(lrc, st.st_uid, st.st_gid)
        assert os.stat(lrc).st_mode & 0o777 == 0o664

    @patch("audio_transcode_watcher.lyrics.os.chown", side_effect=PermissionError("not root"))
    def test_chown_failure_is_logged_at_debug_and_file_kept(self, _chown, tmp_path, caplog):
        audio = tmp_path / "song.flac"
        audio.touch()
        lrc_path = str(tmp_path / "song.lrc")
        with caplog.at_level(logging.DEBUG, logger="audio_transcode_watcher.lyrics"):
            result = _write_lrc(lrc_path, GOOD_LRC, "label", "syncedlyrics", owner_of=str(audio))
        assert result == lrc_path
        assert os.path.isfile(lrc_path)
        chown_logs = [r for r in caplog.records if "Could not chown" in r.getMessage()]
        assert chown_logs and chown_logs[0].levelno == logging.DEBUG


class TestLrcModeFailure:
    @patch("audio_transcode_watcher.lyrics.os.chmod", side_effect=PermissionError("ro"))
    def test_chmod_failure_is_logged_at_debug(self, _chmod, tmp_path, caplog):
        audio = tmp_path / "song.flac"
        audio.touch()
        lrc_path = str(tmp_path / "song.lrc")
        with caplog.at_level(logging.DEBUG, logger="audio_transcode_watcher.lyrics"):
            assert _write_lrc(lrc_path, GOOD_LRC, "l", "s", owner_of=str(audio)) == lrc_path
        msgs = [r for r in caplog.records if "Could not chmod" in r.getMessage()]
        assert msgs and msgs[0].levelno == logging.DEBUG


class TestBareDomainAds:
    @patch("audio_transcode_watcher.lyrics.syncedlyrics")
    @patch("audio_transcode_watcher.lyrics.extract_metadata")
    def test_four_bare_domain_lines_are_rejected(self, mock_meta, mock_sl, tmp_path):
        audio = tmp_path / "Band - Song.flac"
        audio.touch()
        mock_meta.return_value = ("Band", "Song")
        mock_sl.search.return_value = (
            "[00:01.00] lyricsite.co\n"
            "[00:02.00] get-the-app.xyz\n"
            "[00:03.00] songs.example.club\n"
            "[00:04.00] more.lyrics.info\n"
        )
        assert fetch_lyrics_for_file(str(audio)) is None
        assert list(tmp_path.glob("*.lrc")) == []
