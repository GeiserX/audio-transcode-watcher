"""Tests for utility functions."""

from pathlib import Path

import pytest

from audio_transcode_watcher.utils import (
    appears_empty_dir,
    get_output_filename,
    has_audio_extension,
    is_audio_file,
    is_lossless,
    is_mp3,
    nfc,
    nfc_path,
)


class TestNfc:
    """Tests for NFC normalization."""
    
    def test_normalizes_to_nfc(self):
        """Test that strings are normalized to NFC."""
        # NFD representation of "é" (e + combining acute accent)
        nfd_string = "e\u0301"
        # NFC representation of "é" (single character)
        nfc_string = "é"
        
        assert nfc(nfd_string) == nfc_string
    
    def test_already_nfc_unchanged(self):
        """Test that NFC strings are unchanged."""
        nfc_string = "Sigur Rós"
        assert nfc(nfc_string) == nfc_string
    
    def test_ascii_unchanged(self):
        """Test that ASCII strings are unchanged."""
        ascii_string = "Artist - Song"
        assert nfc(ascii_string) == ascii_string


class TestNfcPath:
    """Tests for NFC path normalization."""
    
    def test_normalizes_path_components(self):
        """Test that path components are normalized."""
        # Path with NFD filename
        nfd_path = "/music/Sigur Ro\u0301s/song.flac"
        result = nfc_path(nfd_path)
        
        # Should be NFC normalized
        assert "Sigur Rós" in result
    
    def test_preserves_absolute_path(self):
        """Test that absolute paths remain absolute."""
        path = "/music/artist/song.flac"
        result = nfc_path(path)
        assert result.startswith("/")
    
    def test_handles_empty_path(self):
        """Test handling of empty path."""
        assert nfc_path("") == ""


class TestHasAudioExtension:
    """Tests for has_audio_extension function (no file existence check)."""
    
    def test_flac_extension(self):
        """Test that .flac extension is recognized."""
        assert has_audio_extension("/music/Artist - Song.flac") is True
    
    def test_mp3_extension(self):
        """Test that .mp3 extension is recognized."""
        assert has_audio_extension("/music/Artist - Song.mp3") is True
    
    def test_m4a_extension(self):
        """Test that .m4a extension is recognized."""
        assert has_audio_extension("/music/Artist - Song.m4a") is True
    
    def test_nonexistent_file_still_recognized(self):
        """Test that a nonexistent path with audio extension returns True."""
        assert has_audio_extension("/nonexistent/path/Song.flac") is True
    
    def test_txt_extension_not_audio(self):
        """Test that .txt extension is not recognized."""
        assert has_audio_extension("/music/readme.txt") is False
    
    def test_case_insensitive(self):
        """Test that extension matching is case-insensitive."""
        assert has_audio_extension("/music/Song.FLAC") is True
    
    def test_no_extension(self):
        """Test that path without extension is not recognized."""
        assert has_audio_extension("/music/noext") is False


class TestIsAudioFile:
    """Tests for is_audio_file function."""
    
    def test_flac_is_audio(self, temp_dir):
        """Test that FLAC files are recognized."""
        flac = Path(temp_dir) / "test.flac"
        flac.touch()
        assert is_audio_file(str(flac)) is True
    
    def test_mp3_is_audio(self, temp_dir):
        """Test that MP3 files are recognized."""
        mp3 = Path(temp_dir) / "test.mp3"
        mp3.touch()
        assert is_audio_file(str(mp3)) is True
    
    def test_m4a_is_audio(self, temp_dir):
        """Test that M4A files are recognized."""
        m4a = Path(temp_dir) / "test.m4a"
        m4a.touch()
        assert is_audio_file(str(m4a)) is True
    
    def test_txt_is_not_audio(self, temp_dir):
        """Test that TXT files are not recognized."""
        txt = Path(temp_dir) / "test.txt"
        txt.touch()
        assert is_audio_file(str(txt)) is False
    
    def test_nonexistent_is_not_audio(self, temp_dir):
        """Test that nonexistent files are not recognized."""
        assert is_audio_file(str(Path(temp_dir) / "nonexistent.flac")) is False
    
    def test_case_insensitive(self, temp_dir):
        """Test that extension matching is case-insensitive."""
        flac = Path(temp_dir) / "test.FLAC"
        flac.touch()
        assert is_audio_file(str(flac)) is True


class TestIsLossless:
    """Tests for is_lossless function."""
    
    def test_flac_is_lossless(self):
        """Test that FLAC is recognized as lossless."""
        assert is_lossless("song.flac") is True
    
    def test_wav_is_lossless(self):
        """Test that WAV is recognized as lossless."""
        assert is_lossless("song.wav") is True
    
    def test_ape_is_lossless(self):
        """Test that APE is recognized as lossless."""
        assert is_lossless("song.ape") is True
    
    def test_mp3_is_not_lossless(self):
        """Test that MP3 is not recognized as lossless."""
        assert is_lossless("song.mp3") is False


class TestIsMp3:
    """Tests for is_mp3 function."""
    
    def test_mp3_is_mp3(self):
        """Test that .mp3 is recognized."""
        assert is_mp3("song.mp3") is True
    
    def test_mp3_case_insensitive(self):
        """Test that extension is case-insensitive."""
        assert is_mp3("song.MP3") is True
    
    def test_flac_is_not_mp3(self):
        """Test that FLAC is not recognized as MP3."""
        assert is_mp3("song.flac") is False


class TestAppearsEmptyDir:
    """Tests for appears_empty_dir function."""
    
    def test_empty_dir_is_empty(self, temp_dir):
        """Test that empty directory appears empty."""
        empty = Path(temp_dir) / "empty"
        empty.mkdir()
        assert appears_empty_dir(str(empty)) is True
    
    def test_dir_with_files_not_empty(self, temp_dir):
        """Test that directory with files doesn't appear empty."""
        with_files = Path(temp_dir) / "with_files"
        with_files.mkdir()
        (with_files / "file.txt").touch()
        assert appears_empty_dir(str(with_files)) is False
    
    def test_dir_with_hidden_only_is_empty(self, temp_dir):
        """Test that directory with only hidden files appears empty."""
        hidden_only = Path(temp_dir) / "hidden_only"
        hidden_only.mkdir()
        (hidden_only / ".hidden").touch()
        assert appears_empty_dir(str(hidden_only)) is True
    
    def test_nonexistent_dir_is_empty(self, temp_dir):
        """Test that nonexistent directory appears empty."""
        assert appears_empty_dir(str(Path(temp_dir) / "nonexistent")) is True


class TestGetOutputFilename:
    """Tests for get_output_filename function."""
    
    def test_replaces_extension(self):
        """Test that extension is replaced."""
        result = get_output_filename("Artist - Song.flac", ".m4a")
        assert result == "Artist - Song.m4a"
    
    def test_handles_unicode(self):
        """Test handling of Unicode filenames."""
        result = get_output_filename("Sigur Rós - Hoppípolla.flac", ".mp3")
        assert "Sigur Rós - Hoppípolla" in result
        assert result.endswith(".mp3")
    
    def test_normalizes_to_nfc(self):
        """Test that output filename is NFC normalized."""
        # NFD input
        nfd_name = "Cafe\u0301 Song.flac"
        result = get_output_filename(nfd_name, ".m4a")
        # Should be NFC
        assert result == "Café Song.m4a"
