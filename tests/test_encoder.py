"""Tests for FFmpeg encoder module."""

import logging
import os
import shutil
import stat
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from audio_transcode_watcher.config import OutputConfig
from audio_transcode_watcher.encoder import (
    MP4_TAG_ATOMS,
    _cleanup_temp,
    _remove_artwork_from_command,
    atomic_ffmpeg_encode,
    build_ffmpeg_command,
    copy_mp4_tags,
)


class TestBuildFFmpegCommand:
    """Tests for build_ffmpeg_command function."""
    
    def test_alac_command_with_artwork(self):
        """Test ALAC encoding command with artwork."""
        config = OutputConfig(
            name="alac",
            codec="alac",
            path="/output",
            include_artwork=True,
        )
        cmd = build_ffmpeg_command("/input/test.flac", "/output/test.m4a", config)
        
        assert cmd[0] == "ffmpeg"
        assert "-i" in cmd
        assert "/input/test.flac" in cmd
        assert "-c:a" in cmd
        assert "alac" in cmd
        assert "-map" in cmd
        assert "0:v:0?" in cmd  # Artwork mapping
        assert "-c:v" in cmd
        assert "copy" in cmd
        assert cmd[-1] == "/output/test.m4a"
    
    def test_alac_command_without_artwork(self):
        """Test ALAC encoding command without artwork."""
        config = OutputConfig(
            name="alac",
            codec="alac",
            path="/output",
            include_artwork=False,
        )
        cmd = build_ffmpeg_command("/input/test.flac", "/output/test.m4a", config)
        
        assert "0:v:0?" not in cmd
        assert "-c:v" not in cmd
    
    def test_aac_command(self):
        """Test AAC encoding command."""
        config = OutputConfig(
            name="aac",
            codec="aac",
            path="/output",
            bitrate="192k",
        )
        cmd = build_ffmpeg_command("/input/test.flac", "/output/test.m4a", config)
        
        assert "-c:a" in cmd
        assert "aac" in cmd
        assert "-b:a" in cmd
        assert "192k" in cmd
        assert "-movflags" in cmd
        assert "+faststart" in cmd
    
    def test_mp3_command(self):
        """Test MP3 encoding command."""
        config = OutputConfig(
            name="mp3",
            codec="mp3",
            path="/output",
            bitrate="320k",
            include_artwork=True,
        )
        cmd = build_ffmpeg_command("/input/test.flac", "/output/test.mp3", config)
        
        assert "-c:a" in cmd
        assert "libmp3lame" in cmd
        assert "-b:a" in cmd
        assert "320k" in cmd
        assert "-c:v" in cmd
        assert "mjpeg" in cmd  # MP3 needs mjpeg for ID3 APIC
        assert "-id3v2_version" in cmd
    
    def test_opus_command(self):
        """Test Opus encoding command."""
        config = OutputConfig(
            name="opus",
            codec="opus",
            path="/output",
            bitrate="128k",
        )
        cmd = build_ffmpeg_command("/input/test.flac", "/output/test.opus", config)
        
        assert "-c:a" in cmd
        assert "libopus" in cmd
        assert "-b:a" in cmd
        assert "128k" in cmd
        # Opus doesn't support artwork
        assert "0:v:0?" not in cmd
    
    def test_flac_command(self):
        """Test FLAC encoding command (re-encoding)."""
        config = OutputConfig(
            name="flac",
            codec="flac",
            path="/output",
            include_artwork=True,
        )
        cmd = build_ffmpeg_command("/input/test.wav", "/output/test.flac", config)
        
        assert "-c:a" in cmd
        assert "flac" in cmd
        assert "-f" in cmd
    
    def test_wav_command(self):
        """Test WAV encoding command."""
        config = OutputConfig(
            name="wav",
            codec="wav",
            path="/output",
        )
        cmd = build_ffmpeg_command("/input/test.flac", "/output/test.wav", config)
        
        assert "-c:a" in cmd
        assert "pcm_s16le" in cmd
        assert "-f" in cmd
        assert "wav" in cmd
    
    def test_command_has_common_options(self):
        """Test that all commands have common options."""
        config = OutputConfig(name="test", codec="mp3", path="/output")
        cmd = build_ffmpeg_command("/input/test.flac", "/output/test.mp3", config)
        
        assert "-loglevel" in cmd
        assert "error" in cmd
        assert "-y" in cmd  # Overwrite
        assert "-map" in cmd
        assert "0:a:0" in cmd  # First audio stream
        assert "-map_metadata" in cmd
        assert "0" in cmd


class TestRemoveArtworkFromCommand:
    """Tests for _remove_artwork_from_command function."""
    
    def test_removes_video_mapping(self):
        """Test that video mapping is removed."""
        cmd = [
            "ffmpeg", "-i", "input.flac",
            "-map", "0:a:0",
            "-map", "0:v:0?",
            "-c:a", "alac",
            "-c:v", "copy",
            "output.m4a"
        ]
        filtered = _remove_artwork_from_command(cmd)
        
        assert "0:v:0?" not in filtered
        assert "-c:v" not in filtered
        assert "copy" not in filtered
        # Audio mapping should remain
        assert "0:a:0" in filtered
    
    def test_preserves_audio_mapping(self):
        """Test that audio mapping is preserved."""
        cmd = [
            "ffmpeg", "-i", "input.flac",
            "-map", "0:a:0",
            "-map", "0:v:0?",
            "-c:a", "libmp3lame",
            "-c:v", "mjpeg",
            "output.mp3"
        ]
        filtered = _remove_artwork_from_command(cmd)
        
        # Audio should be preserved
        assert "-map" in filtered
        assert "0:a:0" in filtered
        assert "-c:a" in filtered
        assert "libmp3lame" in filtered
    
    def test_removes_vf_options(self):
        """Test that -vf options are removed."""
        cmd = [
            "ffmpeg", "-i", "input.flac",
            "-vf", "scale=300:300",
            "-c:a", "aac",
            "output.m4a"
        ]
        filtered = _remove_artwork_from_command(cmd)
        
        assert "-vf" not in filtered
        assert "scale=300:300" not in filtered
    
    def test_handles_command_without_video(self):
        """Test handling command that already has no video options."""
        cmd = [
            "ffmpeg", "-i", "input.flac",
            "-map", "0:a:0",
            "-c:a", "opus",
            "output.opus"
        ]
        filtered = _remove_artwork_from_command(cmd)

        assert filtered == cmd


class TestBuildFFmpegCommandUnsupportedCodec:
    """Test error handling for unsupported codecs."""

    def test_unsupported_codec_raises_value_error(self):
        """Raise ValueError for unknown codec."""
        config = OutputConfig.__new__(OutputConfig)
        config.codec = "unknown"
        config.bitrate = "128k"
        config.include_artwork = False
        with pytest.raises(ValueError, match="Unsupported codec"):
            build_ffmpeg_command("/input/test.flac", "/output/test.xyz", config)


class TestAtomicFfmpegEncode:
    """Tests for atomic_ffmpeg_encode function."""

    @patch("subprocess.run")
    def test_successful_encode_renames_temp_to_final(self, mock_run, tmp_path):
        """On success, temp file is renamed to final destination."""
        final = str(tmp_path / "output.m4a")
        cmd = ["ffmpeg", "-i", "input.flac", "-c:a", "alac", final]

        proc = MagicMock()
        proc.returncode = 0
        mock_run.return_value = proc

        # atomic_ffmpeg_encode writes to .tmp__ff then renames;
        # simulate the file being created by ffmpeg
        def side_effect(cmd_arg, **kwargs):
            tmp_dest = cmd_arg[-1]
            with open(tmp_dest, "w") as f:
                f.write("data")
            return proc

        mock_run.side_effect = side_effect

        rc = atomic_ffmpeg_encode(cmd, final)
        assert rc == 0
        assert os.path.isfile(final)

    @patch("subprocess.run")
    def test_failed_encode_returns_nonzero(self, mock_run, tmp_path):
        """Return nonzero rc when ffmpeg fails."""
        final = str(tmp_path / "output.m4a")
        cmd = ["ffmpeg", "-i", "input.flac", final]

        proc = MagicMock()
        proc.returncode = 1
        proc.stderr = b"some error"
        mock_run.return_value = proc

        rc = atomic_ffmpeg_encode(cmd, final, retry_without_artwork=False)
        assert rc == 1
        assert not os.path.isfile(final)

    @patch("subprocess.run")
    def test_retries_without_artwork_on_artwork_error(self, mock_run, tmp_path):
        """Retry without artwork when stderr hints at artwork failure."""
        final = str(tmp_path / "output.m4a")
        cmd = [
            "ffmpeg", "-i", "input.flac",
            "-map", "0:a:0", "-map", "0:v:0?",
            "-c:a", "alac", "-c:v", "copy",
            final,
        ]

        # First call fails with artwork error, second succeeds
        fail_proc = MagicMock()
        fail_proc.returncode = 1
        fail_proc.stderr = b"Error: mjpeg decode failed"

        ok_proc = MagicMock()
        ok_proc.returncode = 0

        call_count = [0]

        def side_effect(cmd_arg, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return fail_proc
            # Create the temp file to simulate ffmpeg success
            tmp_dest = cmd_arg[-1]
            with open(tmp_dest, "w") as f:
                f.write("data")
            return ok_proc

        mock_run.side_effect = side_effect

        rc = atomic_ffmpeg_encode(cmd, final, retry_without_artwork=True)
        assert rc == 0
        assert mock_run.call_count == 2

    @patch("subprocess.run")
    def test_retry_also_fails_returns_nonzero(self, mock_run, tmp_path):
        """Return nonzero when both initial and retry encode fail."""
        final = str(tmp_path / "output.m4a")
        cmd = ["ffmpeg", "-i", "input.flac", "-map", "0:v:0?", "-c:v", "copy", final]

        proc = MagicMock()
        proc.returncode = 1
        proc.stderr = b"png decode error"
        mock_run.return_value = proc

        rc = atomic_ffmpeg_encode(cmd, final, retry_without_artwork=True)
        assert rc == 1
        assert mock_run.call_count == 2

    @patch("subprocess.run")
    def test_cleans_up_stale_temp_before_encoding(self, mock_run, tmp_path):
        """Remove leftover .tmp__ff before encoding starts."""
        final = str(tmp_path / "output.m4a")
        stale = final + ".tmp__ff"
        with open(stale, "w") as f:
            f.write("stale")

        proc = MagicMock()
        proc.returncode = 0
        mock_run.return_value = proc

        def side_effect(cmd_arg, **kwargs):
            tmp_dest = cmd_arg[-1]
            with open(tmp_dest, "w") as f:
                f.write("data")
            return proc

        mock_run.side_effect = side_effect

        rc = atomic_ffmpeg_encode(["ffmpeg", "-i", "in.flac", final], final)
        assert rc == 0

    @patch("os.replace", side_effect=OSError("disk full"))
    @patch("subprocess.run")
    def test_returns_1_when_atomic_replace_fails(self, mock_run, _replace, tmp_path):
        """Return 1 when os.replace fails after successful encode."""
        final = str(tmp_path / "output.m4a")
        cmd = ["ffmpeg", "-i", "input.flac", final]

        proc = MagicMock()
        proc.returncode = 0
        mock_run.return_value = proc

        def side_effect(cmd_arg, **kwargs):
            tmp_dest = cmd_arg[-1]
            with open(tmp_dest, "w") as f:
                f.write("data")
            return proc

        mock_run.side_effect = side_effect

        rc = atomic_ffmpeg_encode(cmd, final, retry_without_artwork=False)
        assert rc == 1

    @patch("subprocess.run")
    def test_retry_replace_failure_returns_1(self, mock_run, tmp_path):
        """Return 1 when retry succeeds but os.replace fails."""
        final = str(tmp_path / "output.m4a")
        cmd = ["ffmpeg", "-i", "in.flac", "-map", "0:v:0?", "-c:v", "copy", final]

        fail_proc = MagicMock()
        fail_proc.returncode = 1
        fail_proc.stderr = b"mjpeg error"

        ok_proc = MagicMock()
        ok_proc.returncode = 0

        call_count = [0]

        def side_effect(cmd_arg, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return fail_proc
            tmp_dest = cmd_arg[-1]
            with open(tmp_dest, "w") as f:
                f.write("data")
            return ok_proc

        mock_run.side_effect = side_effect

        with patch("os.replace", side_effect=OSError("nfs error")):
            rc = atomic_ffmpeg_encode(cmd, final, retry_without_artwork=True)

        assert rc == 1


class TestCleanupTemp:
    """Tests for _cleanup_temp function."""

    def test_removes_existing_file(self, tmp_path):
        """Remove a temporary file that exists."""
        f = tmp_path / "test.tmp__ff"
        f.write_text("data")
        _cleanup_temp(str(f))
        assert not f.exists()

    def test_no_error_when_file_missing(self, tmp_path):
        """No error when the file does not exist."""
        _cleanup_temp(str(tmp_path / "nonexistent.tmp__ff"))

    def test_no_error_on_permission_failure(self, tmp_path):
        """Gracefully handle permission errors."""
        with patch("os.path.exists", return_value=True), \
             patch("os.remove", side_effect=PermissionError("denied")):
            _cleanup_temp(str(tmp_path / "locked.tmp__ff"))


# A stand-in for ffmpeg: logs its arguments, writes the output file, prints
# FAKE_STDERR and exits FAKE_RC. When FAKE_ART_STDERR is set, a command that
# still maps the cover art stream fails with that stderr instead.
FAKE_FFMPEG = """#!/bin/sh
for last; do :; done
echo "$*" >> "$FAKE_LOG"
case " $* " in *" 0:v:0? "*) art=1;; *) art=0;; esac
if [ "$art" = 1 ] && [ -n "$FAKE_ART_STDERR" ]; then
  printf '%b\\n' "$FAKE_ART_STDERR" >&2
  exit 1
fi
printf data > "$last"
if [ -n "$FAKE_STDERR" ]; then printf '%b\\n' "$FAKE_STDERR" >&2; fi
exit ${FAKE_RC:-0}
"""

CORRUPT_FLAC_STDERR = (
    "[flac @ 0x7f] invalid residual\\n"
    "[flac @ 0x7f] decode_frame() failed"
)


@pytest.fixture
def fake_ffmpeg(tmp_path, monkeypatch):
    """Put a fake ffmpeg first on PATH; return the file it logs calls to."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "ffmpeg"
    script.write_text(FAKE_FFMPEG)
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    log = tmp_path / "ffmpeg-calls.log"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LOG", str(log))
    for var in ("FAKE_RC", "FAKE_STDERR", "FAKE_ART_STDERR"):
        monkeypatch.delenv(var, raising=False)
    return log


def _calls(log: Path) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


def _alac_cmd(tmp_path, name="Etta James - I'd Rather Go Blind"):
    out = OutputConfig(name="alac", codec="alac", path=str(tmp_path / "alac"))
    src = str(tmp_path / f"{name}.flac")
    dest = str(tmp_path / "alac" / f"{name}.m4a")
    return build_ffmpeg_command(src, dest, out), dest


class TestFailLoudly:
    """A source that does not decode cleanly fails, even when ffmpeg exits 0."""

    @pytest.mark.parametrize("codec", ["alac", "aac", "mp3", "opus", "flac", "wav"])
    def test_every_command_uses_xerror(self, codec):
        out = OutputConfig(name=codec, codec=codec, path="/out")
        assert "-xerror" in build_ffmpeg_command("/in/a.flac", "/out/a.x", out)

    def test_clean_run_succeeds(self, fake_ffmpeg, tmp_path):
        cmd, dest = _alac_cmd(tmp_path)
        assert atomic_ffmpeg_encode(cmd, dest) == 0
        assert os.path.isfile(dest)
        assert len(_calls(fake_ffmpeg)) == 1

    @pytest.mark.parametrize(
        "stderr",
        [
            CORRUPT_FLAC_STDERR,
            "[aist#0:0/flac @ 0x1] Decoding error: Invalid data found when processing input",
            "Error while decoding stream #0:0: Invalid data found when processing input",
        ],
    )
    def test_decode_error_with_exit_0_fails(self, fake_ffmpeg, tmp_path, monkeypatch, caplog, stderr):
        monkeypatch.setenv("FAKE_RC", "0")
        monkeypatch.setenv("FAKE_STDERR", stderr)
        cmd, dest = _alac_cmd(tmp_path)

        with caplog.at_level(logging.ERROR, logger="audio_transcode_watcher.encoder"):
            rc = atomic_ffmpeg_encode(cmd, dest)

        assert rc != 0
        assert not os.path.exists(dest)
        assert not os.path.exists(dest + ".tmp__ff")
        errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
        assert any("I'd Rather Go Blind.flac" in m for m in errors)

    def test_nonzero_exit_fails(self, fake_ffmpeg, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_RC", "183")
        cmd, dest = _alac_cmd(tmp_path)
        assert atomic_ffmpeg_encode(cmd, dest) == 183
        assert not os.path.exists(dest)

    def test_decode_error_does_not_trigger_artwork_retry(self, fake_ffmpeg, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_RC", "1")
        monkeypatch.setenv("FAKE_STDERR", CORRUPT_FLAC_STDERR)
        cmd, dest = _alac_cmd(tmp_path)
        assert "0:v:0?" in cmd

        assert atomic_ffmpeg_encode(cmd, dest) != 0
        assert len(_calls(fake_ffmpeg)) == 1

    @pytest.mark.parametrize(
        "art_stderr",
        [
            "[mp4 @ 0x1] Could not find tag for codec png in stream #1, codec not currently supported in container",
            "[vist#0:1/mjpeg @ 0x1] Error while decoding attached picture",
            "Error initializing output stream 0:1 -- video stream",
        ],
    )
    def test_artwork_error_retries_without_cover(self, fake_ffmpeg, tmp_path, monkeypatch, art_stderr):
        monkeypatch.setenv("FAKE_ART_STDERR", art_stderr)
        cmd, dest = _alac_cmd(tmp_path)

        assert atomic_ffmpeg_encode(cmd, dest) == 0
        calls = _calls(fake_ffmpeg)
        assert len(calls) == 2
        assert "0:v:0?" in calls[0]
        assert "0:v:0?" not in calls[1]
        assert os.path.isfile(dest)


def _make_flac(path: Path, seconds: int = 3) -> None:
    subprocess.run(
        [
            "ffmpeg", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-c:a", "flac", str(path),
        ],
        check=True,
    )


REAL_TAG_VALUES = {
    "REPLAYGAIN_TRACK_GAIN": "-6.50 dB",
    "REPLAYGAIN_TRACK_PEAK": "0.988525",
    "REPLAYGAIN_ALBUM_GAIN": "-7.10 dB",
    "REPLAYGAIN_ALBUM_PEAK": "1.000000",
    "MUSICBRAINZ_TRACKID": "b8b7a3c3-9a4e-4c7b-9a43-5c3b2d0e4f11",
    "MUSICBRAINZ_ALBUMID": "1f9a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8",
    "MUSICBRAINZ_ARTISTID": "6f2a7c11-0d6e-4b2f-9b8c-1e2d3c4b5a69",
    "MUSICBRAINZ_ALBUMARTISTID": "6f2a7c11-0d6e-4b2f-9b8c-1e2d3c4b5a69",
    "MUSICBRAINZ_RELEASEGROUPID": "0a1b2c3d-4e5f-6071-8293-a4b5c6d7e8f9",
    "ISRC": "USMC16046323",
    "LABEL": "Argo",
    "CATALOGNUMBER": "LP-4003",
    "ARTISTSORT": "James, Etta",
    "ALBUMARTISTSORT": "James, Etta",
    "ALBUMSORT": "At Last!",
}

# The atoms MusicBrainz Picard writes; every common reader knows them.
EXPECTED_ATOMS = {
    "REPLAYGAIN_TRACK_GAIN": "----:com.apple.iTunes:replaygain_track_gain",
    "REPLAYGAIN_TRACK_PEAK": "----:com.apple.iTunes:replaygain_track_peak",
    "REPLAYGAIN_ALBUM_GAIN": "----:com.apple.iTunes:replaygain_album_gain",
    "REPLAYGAIN_ALBUM_PEAK": "----:com.apple.iTunes:replaygain_album_peak",
    "MUSICBRAINZ_TRACKID": "----:com.apple.iTunes:MusicBrainz Track Id",
    "MUSICBRAINZ_ALBUMID": "----:com.apple.iTunes:MusicBrainz Album Id",
    "MUSICBRAINZ_ARTISTID": "----:com.apple.iTunes:MusicBrainz Artist Id",
    "MUSICBRAINZ_ALBUMARTISTID": "----:com.apple.iTunes:MusicBrainz Album Artist Id",
    "MUSICBRAINZ_RELEASEGROUPID": "----:com.apple.iTunes:MusicBrainz Release Group Id",
    "ISRC": "----:com.apple.iTunes:ISRC",
    "LABEL": "----:com.apple.iTunes:LABEL",
    "CATALOGNUMBER": "----:com.apple.iTunes:CATALOGNUMBER",
    "ARTISTSORT": "soar",
    "ALBUMARTISTSORT": "soaa",
    "ALBUMSORT": "soal",
}


def _atom_text(tags, atom):
    value = tags[atom][0]
    return value if isinstance(value, str) else bytes(value).decode()


class TestMp4TagAtoms:
    """The atom names, checked without ffmpeg."""

    def test_atom_names_match_picard(self):
        assert MP4_TAG_ATOMS == EXPECTED_ATOMS
        assert set(REAL_TAG_VALUES) == set(MP4_TAG_ATOMS)


needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg is not installed; the real-file tag checks need it",
)


@needs_ffmpeg
class TestFreeformTagsRealFiles:
    """Tags the MP4 atoms cannot hold survive an ALAC or AAC encode."""

    @pytest.mark.parametrize("codec", ["alac", "aac"])
    def test_flac_tags_land_in_picard_atoms(self, tmp_path, codec):
        from mutagen.flac import FLAC
        from mutagen.mp4 import MP4

        src = tmp_path / "Etta James - At Last.flac"
        _make_flac(src)
        flac = FLAC(str(src))
        flac["TITLE"] = "At Last"
        flac["ARTIST"] = "Etta James"
        for k, v in REAL_TAG_VALUES.items():
            flac[k] = v
        flac.save()

        out = OutputConfig(name=codec, codec=codec, path=str(tmp_path / codec))
        dest = str(tmp_path / codec / "Etta James - At Last.m4a")
        cmd = build_ffmpeg_command(str(src), dest, out)
        rc = atomic_ffmpeg_encode(
            cmd, dest, finalize=lambda tmp: copy_mp4_tags(str(src), tmp)
        )
        assert rc == 0

        tags = MP4(dest).tags
        for k, v in REAL_TAG_VALUES.items():
            atom = EXPECTED_ATOMS[k]
            assert atom in tags, atom
            assert _atom_text(tags, atom) == v
        # Sort names are plain text in the standard sort atoms.
        assert tags["soar"] == ["James, Etta"]
        assert not any(a.startswith("----:com.apple.iTunes:ARTISTSORT") for a in tags)
        # Standard atoms ffmpeg writes are still there.
        assert tags["\xa9nam"] == ["At Last"]
        assert tags["\xa9ART"] == ["Etta James"]

    def test_id3_source_tags_are_read(self, tmp_path):
        from mutagen.id3 import TPUB, TSOP, TSRC, TXXX, UFID
        from mutagen.mp4 import MP4
        from mutagen.wave import WAVE

        src = tmp_path / "song.wav"
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi",
             "-i", "sine=duration=1", str(src)],
            check=True,
        )
        wav = WAVE(str(src))
        wav.add_tags()
        wav.tags.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_GAIN", text=["-3.00 dB"]))
        wav.tags.add(TXXX(encoding=3, desc="MusicBrainz Album Id", text=["album-id"]))
        wav.tags.add(UFID(owner="http://musicbrainz.org", data=b"track-id"))
        wav.tags.add(TSRC(encoding=3, text=["GBAYE0601498"]))
        wav.tags.add(TPUB(encoding=3, text=["Parlophone"]))
        wav.tags.add(TSOP(encoding=3, text=["Beatles, The"]))
        wav.save()

        dest = tmp_path / "song.m4a"
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-i", str(src),
             "-c:a", "alac", "-f", "mp4", str(dest)],
            check=True,
        )
        assert copy_mp4_tags(str(src), str(dest)) == 6

        tags = MP4(str(dest)).tags
        expect = {
            "REPLAYGAIN_TRACK_GAIN": "-3.00 dB",
            "MUSICBRAINZ_ALBUMID": "album-id",
            "MUSICBRAINZ_TRACKID": "track-id",
            "ISRC": "GBAYE0601498",
            "LABEL": "Parlophone",
            "ARTISTSORT": "Beatles, The",
        }
        for k, v in expect.items():
            assert _atom_text(tags, EXPECTED_ATOMS[k]) == v

    def test_source_without_tags_writes_nothing(self, tmp_path):
        src = tmp_path / "bare.flac"
        _make_flac(src, seconds=1)
        assert copy_mp4_tags(str(src), str(tmp_path / "unused.m4a")) == 0


class TestEncoderEdges:
    """Small branches of the new encoder code."""

    def test_source_name_without_input_flag(self, fake_ffmpeg, tmp_path, monkeypatch, caplog):
        monkeypatch.setenv("FAKE_RC", "2")
        dest = str(tmp_path / "out.m4a")
        assert atomic_ffmpeg_encode(["ffmpeg", dest], dest, retry_without_artwork=False) == 2
        assert "FFmpeg failed (rc=2) for ? →" in caplog.text

    def test_finalize_failure_keeps_the_output(self, fake_ffmpeg, tmp_path, caplog):
        cmd, dest = _alac_cmd(tmp_path)

        def broken(_tmp):
            raise ValueError("bad tags")

        assert atomic_ffmpeg_encode(cmd, dest, finalize=broken) == 0
        assert os.path.isfile(dest)
        assert "Post-encode step failed" in caplog.text

    def test_finalize_runs_on_the_temp_file(self, fake_ffmpeg, tmp_path):
        cmd, dest = _alac_cmd(tmp_path)
        seen = []
        assert atomic_ffmpeg_encode(cmd, dest, finalize=seen.append) == 0
        assert seen == [dest + ".tmp__ff"]

    @patch("audio_transcode_watcher.encoder.mutagen.File", return_value=None)
    def test_unreadable_source_copies_no_tags(self, _file, tmp_path):
        assert copy_mp4_tags(str(tmp_path / "x.flac"), str(tmp_path / "x.m4a")) == 0
