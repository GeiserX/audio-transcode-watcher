"""Tests for ReplayGain track tags on sources and outputs."""

import filecmp
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import mutagen
import pytest
from mutagen.id3 import ID3, TXXX
from mutagen.mp4 import MP4, MP4FreeForm

import audio_transcode_watcher.sync as sync_mod
from audio_transcode_watcher import manifest, replaygain
from audio_transcode_watcher.config import Config, OutputConfig
from audio_transcode_watcher.sync import initial_sync, process_source_file
from audio_transcode_watcher.watcher import AudioSyncHandler

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)

# A mono 1 kHz sine with a -20 dBFS peak measures -23.01 LUFS, so its
# ReplayGain 2.0 track gain is -18 - (-23.01) = +5.01 dB and its peak 0.1.
EXPECTED_GAIN = 5.01
EXPECTED_PEAK = 0.1


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.setattr(sync_mod, "ORPHAN_MIN_AGE", 0.0)
    monkeypatch.setattr(sync_mod, "TEMP_MIN_AGE", 0.0)
    for cache in (
        sync_mod._failed_sources,
        replaygain._checked,
        replaygain._own_writes,
        manifest._rows,
        manifest._dirty,
        manifest._last_write,
    ):
        cache.clear()
    yield
    for cache in (
        sync_mod._failed_sources,
        replaygain._checked,
        replaygain._own_writes,
        manifest._rows,
        manifest._dirty,
        manifest._last_write,
    ):
        cache.clear()


def _tone(path: Path, *codec_args: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "aevalsrc=0.1*sin(2*PI*1000*t):s=44100:d=3",
            *codec_args,
            str(path),
        ],
        check=True,
    )
    return path


def _gain(path) -> float:
    return float(replaygain.read_track_tags(str(path))[replaygain.GAIN_TAG].split()[0])


def _peak(path) -> float:
    return float(replaygain.read_track_tags(str(path))[replaygain.PEAK_TAG])


def _config(src: Path, outputs: dict[str, str], tmp: Path, rg=True) -> Config:
    return Config(
        source_path=str(src),
        outputs=[
            OutputConfig(name=codec, codec=codec, path=str(tmp / codec))
            for codec in outputs
        ],
        fetch_lyrics=False,
        replaygain=rg,
    )


class TestTrackValues:
    def test_gain_is_reference_minus_loudness(self):
        values = replaygain.track_values(-11.68, -0.1)
        assert values == {
            "REPLAYGAIN_TRACK_GAIN": "-6.32 dB",
            "REPLAYGAIN_TRACK_PEAK": "0.988553",
        }

    def test_quiet_track_gets_positive_gain(self):
        values = replaygain.track_values(-23.0, -20.0)
        assert values["REPLAYGAIN_TRACK_GAIN"] == "5.00 dB"
        assert values["REPLAYGAIN_TRACK_PEAK"] == "0.100000"

    def test_silence_gets_no_tag(self):
        assert replaygain.track_values(-70.0, float("-inf")) is None
        assert replaygain.track_values(float("-inf"), float("-inf")) is None

    def test_peak_of_minus_inf_is_zero(self):
        values = replaygain.track_values(-30.0, float("-inf"))
        assert values["REPLAYGAIN_TRACK_PEAK"] == "0.000000"


class TestConfig:
    def _data(self, **extra):
        data = {
            "source": {"path": "/s"},
            "outputs": [{"name": "a", "codec": "mp3", "path": "/o"}],
        }
        data.update(extra)
        return data

    def test_off_by_default(self):
        assert Config.from_dict(self._data()).replaygain is False

    def test_top_level_key(self):
        assert Config.from_dict(self._data(replaygain=True)).replaygain is True

    def test_also_read_under_settings(self):
        cfg = Config.from_dict(self._data(settings={"replaygain": True}))
        assert cfg.replaygain is True

    def test_rejects_non_boolean(self):
        with pytest.raises(ValueError, match="replaygain"):
            Config.from_dict(self._data(replaygain="yes"))


@needs_ffmpeg
class TestSourceTags:
    def test_flac_source(self, tmp_path):
        flac = _tone(tmp_path / "a.flac")
        assert replaygain.tag_source(str(flac)) is not None
        assert abs(_gain(flac) - EXPECTED_GAIN) < 0.5
        assert abs(_peak(flac) - EXPECTED_PEAK) < 0.01
        assert "replaygain_track_gain" in mutagen.File(flac).tags

    def test_mp3_source_keeps_id3v23(self, tmp_path):
        mp3 = _tone(tmp_path / "a.mp3", "-c:a", "libmp3lame", "-id3v2_version", "3")
        assert replaygain.tag_source(str(mp3)) is not None
        assert abs(_gain(mp3) - EXPECTED_GAIN) < 0.5
        tags = ID3(mp3)
        assert "TXXX:REPLAYGAIN_TRACK_GAIN" in tags
        assert "TXXX:REPLAYGAIN_TRACK_PEAK" in tags
        assert tags.version[:2] == (2, 3)

    def test_already_tagged_source_is_not_measured(self, tmp_path):
        flac = _tone(tmp_path / "a.flac")
        audio = mutagen.File(flac)
        audio["REPLAYGAIN_TRACK_GAIN"] = "-1.00 dB"
        audio.save()
        with patch.object(replaygain, "measure") as measure:
            assert replaygain.tag_source(str(flac)) is None
        measure.assert_not_called()
        assert _gain(flac) == -1.0

    def test_unmeasurable_source_is_skipped(self, tmp_path, caplog):
        bad = tmp_path / "bad.flac"
        bad.write_bytes(b"fLaC" + os.urandom(4000))
        before = bad.read_bytes()
        assert replaygain.tag_source(str(bad)) is None
        assert bad.read_bytes() == before

    def test_bad_source_does_not_stop_the_scan(self, tmp_path):
        src = tmp_path / "src"
        _tone(src / "good.flac")
        (src / "bad.flac").write_bytes(b"fLaC" + os.urandom(4000))
        config = _config(src, {"mp3": ""}, tmp_path)
        initial_sync(config)
        assert abs(_gain(tmp_path / "mp3" / "good.mp3") - EXPECTED_GAIN) < 0.5
        assert not (tmp_path / "mp3" / "bad.mp3").exists()


CODECS = {"alac": ".m4a", "aac": ".m4a", "mp3": ".mp3", "opus": ".opus", "wav": ".wav"}


@needs_ffmpeg
class TestOutputs:
    def test_new_encodes_carry_the_tags(self, tmp_path):
        src = tmp_path / "src"
        _tone(src / "Artist" / "a.flac")
        config = _config(src, CODECS, tmp_path)
        process_source_file(str(src / "Artist" / "a.flac"), config, check_stable=False)
        for codec, ext in CODECS.items():
            out = tmp_path / codec / "Artist" / f"a{ext}"
            assert abs(_gain(out) - EXPECTED_GAIN) < 0.5, codec
            assert abs(_peak(out) - EXPECTED_PEAK) < 0.01, codec
        m4a = MP4(tmp_path / "alac" / "Artist" / "a.m4a")
        assert "----:com.apple.iTunes:replaygain_track_gain" in m4a.tags

    def test_existing_outputs_are_tagged_without_reencoding(self, tmp_path):
        src = tmp_path / "src"
        _tone(src / "a.flac")
        config = _config(src, CODECS, tmp_path, rg=False)
        initial_sync(config)
        alac = tmp_path / "alac" / "a.m4a"
        assert replaygain.read_track_tags(str(alac)) == {}

        config.replaygain = True
        real = sync_mod.atomic_ffmpeg_encode
        with patch.object(sync_mod, "atomic_ffmpeg_encode", wraps=real) as encode:
            initial_sync(config)
        encode.assert_not_called()
        for codec, ext in CODECS.items():
            out = tmp_path / codec / f"a{ext}"
            assert abs(_gain(out) - EXPECTED_GAIN) < 0.5, codec
            row = manifest.lookup(str(tmp_path / codec), str(out))
            assert manifest.output_matches(row, str(out)), codec

    def test_lossy_copy_is_recopied_from_the_tagged_source(self, tmp_path):
        src = tmp_path / "src"
        mp3 = _tone(src / "a.mp3", "-c:a", "libmp3lame")
        config = _config(src, {"alac": ""}, tmp_path, rg=False)
        initial_sync(config)
        copy = tmp_path / "alac" / "a.mp3"
        assert filecmp.cmp(mp3, copy, shallow=False)

        config.replaygain = True
        initial_sync(config)
        assert abs(_gain(mp3) - EXPECTED_GAIN) < 0.5
        assert filecmp.cmp(mp3, copy, shallow=False)
        # A real copy (shutil.copy2) carries the source's mtime.
        assert copy.stat().st_mtime == mp3.stat().st_mtime


@needs_ffmpeg
class TestNoReprocessing:
    def _tagged_library(self, tmp_path):
        src = tmp_path / "src"
        flac = _tone(src / "a.flac")
        config = _config(src, {"alac": "", "mp3": ""}, tmp_path)
        initial_sync(config)
        assert abs(_gain(flac) - EXPECTED_GAIN) < 0.5
        return src, flac, config

    def test_rescan_does_no_work(self, tmp_path):
        _src, flac, config = self._tagged_library(tmp_path)
        stat = flac.stat()
        files = [tmp_path / "alac" / "a.m4a", tmp_path / "mp3" / "a.mp3"]
        outputs = {p: p.stat().st_mtime_ns for p in files}

        for restart in (False, True):
            if restart:  # a new process: nothing remembered in memory
                replaygain._checked.clear()
                manifest._rows.clear()
            with (
                patch.object(sync_mod, "atomic_ffmpeg_encode") as encode,
                patch.object(replaygain, "measure") as measure,
                patch.object(replaygain, "write_track_tags") as write,
            ):
                initial_sync(config, periodic=True)
            encode.assert_not_called()
            measure.assert_not_called()
            write.assert_not_called()
        assert flac.stat().st_mtime_ns == stat.st_mtime_ns
        assert {p: p.stat().st_mtime_ns for p in outputs} == outputs

    def test_watcher_ignores_its_own_tag_write(self, tmp_path):
        _src, flac, config = self._tagged_library(tmp_path)
        handler = AudioSyncHandler(config)
        event = SimpleNamespace(src_path=str(flac), is_directory=False)
        with (
            patch("audio_transcode_watcher.watcher.delete_outputs") as delete,
            patch("audio_transcode_watcher.watcher.process_source_file") as process,
            patch("time.sleep"),
        ):
            handler.on_modified(event)
            delete.assert_not_called()
            process.assert_not_called()

            # A real edit afterwards is still a change.
            audio = mutagen.File(flac)
            audio["TITLE"] = "edited"
            audio.save()
            handler.on_modified(event)
            delete.assert_called_once()
            process.assert_called_once()

    def test_tolerant_copy_is_not_rebuilt_after_tagging(self, tmp_path):
        src = tmp_path / "src"
        flac = _tone(src / "a.flac")
        config = _config(src, {"mp3": ""}, tmp_path, rg=False)
        initial_sync(config)
        out = tmp_path / "mp3" / "a.mp3"
        manifest.lookup(str(tmp_path / "mp3"), str(out))["kind"] = "tolerant"

        config.replaygain = True
        for _scan in range(2):  # the scan that tags, then the next one
            with patch.object(sync_mod, "atomic_ffmpeg_encode") as encode:
                initial_sync(config)
            encode.assert_not_called()
        assert abs(_gain(flac) - EXPECTED_GAIN) < 0.5
        assert abs(_gain(out) - EXPECTED_GAIN) < 0.5
        row = manifest.lookup(str(tmp_path / "mp3"), str(out))
        assert row["kind"] == "tolerant"
        output = config.outputs[0]
        assert not sync_mod._tolerant_source_changed(output, str(out), str(flac))

    def test_replaced_damaged_source_still_rebuilds_its_tolerant_copy(self, tmp_path):
        src = tmp_path / "src"
        flac = _tone(src / "a.flac")
        config = _config(src, {"mp3": ""}, tmp_path, rg=False)
        initial_sync(config)
        out = tmp_path / "mp3" / "a.mp3"
        row = manifest.lookup(str(tmp_path / "mp3"), str(out))
        row["kind"] = "tolerant"
        row["size"] = row["size"] + 1  # the row describes an older source

        sync_mod._tag_source(str(flac), config)
        assert abs(_gain(flac) - EXPECTED_GAIN) < 0.5
        output = config.outputs[0]
        assert sync_mod._tolerant_source_changed(output, str(out), str(flac))


@needs_ffmpeg
class TestFormats:
    def test_wavpack_source_gets_apev2_tags(self, tmp_path):
        wv = _tone(tmp_path / "a.wv", "-c:a", "wavpack")
        assert replaygain.tag_source(str(wv)) is not None
        assert abs(_gain(wv) - EXPECTED_GAIN) < 0.5
        assert "REPLAYGAIN_TRACK_GAIN" in mutagen.File(wv).tags

    def test_m4a_source_gets_lowercase_freeform_atoms(self, tmp_path):
        m4a = _tone(tmp_path / "a.m4a", "-c:a", "aac")
        audio = MP4(m4a)
        audio["----:com.apple.iTunes:REPLAYGAIN_TRACK_PEAK"] = [MP4FreeForm(b"0.5")]
        audio.save()
        assert replaygain.tag_source(str(m4a)) is not None
        keys = [k for k in MP4(m4a).tags if "replaygain" in k.lower()]
        assert sorted(keys) == [
            "----:com.apple.iTunes:replaygain_track_gain",
            "----:com.apple.iTunes:replaygain_track_peak",
        ]
        assert abs(_gain(m4a) - EXPECTED_GAIN) < 0.5
        # The AAC encoder overshoots at the tone's abrupt start, so the true
        # peak is real but higher than the sine's; it only has to be fresh.
        assert _peak(m4a) != 0.5

    def test_id3_case_variant_is_replaced_not_duplicated(self, tmp_path):
        mp3 = _tone(tmp_path / "a.mp3", "-c:a", "libmp3lame")
        tags = ID3(mp3)
        tags.add(TXXX(encoding=0, desc="replaygain_track_peak", text=["0.5"]))
        tags.save()
        assert replaygain.tag_source(str(mp3)) is not None
        descs = [f.desc for f in ID3(mp3).getall("TXXX")]
        assert sorted(d for d in descs if "eplay" in d.lower()) == [
            "REPLAYGAIN_TRACK_GAIN",
            "REPLAYGAIN_TRACK_PEAK",
        ]

    @pytest.mark.parametrize(
        ("name", "args"),
        [("a.aac", ("-c:a", "aac", "-f", "adts")), ("a.wma", ("-c:a", "wmav2"))],
    )
    def test_formats_without_supported_tags_are_not_measured(
        self, tmp_path, name, args
    ):
        path = _tone(tmp_path / name, *args)
        with patch.object(replaygain, "measure") as measure:
            assert replaygain.tag_source(str(path)) is None
        measure.assert_not_called()
        assert replaygain.write_track_tags(str(path), {"X": "1"}) is False

    def test_silent_source_gets_no_tag(self, tmp_path, caplog):
        silent = tmp_path / "s.flac"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=44100:cl=mono",
                "-t",
                "2",
                str(silent),
            ],
            check=True,
        )
        with caplog.at_level("INFO"):
            assert replaygain.tag_source(str(silent)) is None
        assert "silent" in caplog.text
        assert replaygain.read_track_tags(str(silent)) == {}


class TestErrors:
    def test_measure_timeout(self, caplog):
        boom = subprocess.TimeoutExpired("ffmpeg", 1)
        with patch.object(replaygain.subprocess, "run", side_effect=boom):
            assert replaygain.measure("/x.flac") is None
        assert "timed out" in caplog.text

    def test_measure_without_ffmpeg(self, caplog):
        with patch.object(replaygain.subprocess, "run", side_effect=OSError("no")):
            assert replaygain.measure("/x.flac") is None
        assert "could not run ffmpeg" in caplog.text

    @pytest.mark.parametrize(
        ("rc", "stderr"),
        [(1, b"broken\n"), (0, b"no summary here"), (0, b"Summary:\n nothing")],
    )
    def test_measure_unparseable_output(self, rc, stderr):
        done = SimpleNamespace(returncode=rc, stderr=stderr)
        with patch.object(replaygain.subprocess, "run", return_value=done):
            assert replaygain.measure("/x.flac") is None

    def test_unknown_format_is_skipped(self, tmp_path):
        junk = tmp_path / "a.dat"
        junk.write_bytes(b"not audio at all")
        assert replaygain.tag_source(str(junk)) is None
        assert replaygain.write_track_tags(str(junk), {"X": "1"}) is False

    def test_corrupt_header_is_skipped(self, tmp_path, caplog):
        junk = tmp_path / "a.tta"
        junk.write_bytes(b"not audio at all")
        assert replaygain.tag_source(str(junk)) is None
        assert "cannot read tags" in caplog.text

    def test_unreadable_file_is_skipped(self, tmp_path, caplog):
        with patch.object(replaygain.mutagen, "File", side_effect=OSError("io")):
            assert replaygain.tag_source(str(tmp_path / "a.flac")) is None
        assert "cannot read tags" in caplog.text

    @needs_ffmpeg
    def test_failed_write_is_logged(self, tmp_path, caplog):
        flac = _tone(tmp_path / "a.flac")
        with patch.object(replaygain, "_write", side_effect=OSError("disk full")):
            assert replaygain.tag_source(str(flac)) is None
        assert "could not tag" in caplog.text
        assert str(flac) not in replaygain._own_writes

    def test_own_write_pending_and_stale(self, tmp_path):
        f = tmp_path / "a.flac"
        f.write_bytes(b"x")
        key = str(f)
        replaygain._own_writes[key] = None
        assert replaygain.is_own_write(key)
        replaygain._own_writes[key] = (999, 1)
        assert not replaygain.is_own_write(key)
        assert key not in replaygain._own_writes
        assert not replaygain.is_own_write(key)

    def test_checked_cache(self, tmp_path):
        f = tmp_path / "a.flac"
        f.write_bytes(b"x")
        replaygain.mark_checked(str(f))
        assert replaygain.is_checked(str(f))
        f.write_bytes(b"xy")
        assert not replaygain.is_checked(str(f))
        replaygain.forget(str(f))
        f.unlink()
        replaygain.mark_checked(str(f))
        assert str(f) not in replaygain._checked


@needs_ffmpeg
class TestCarryEdges:
    def test_lossy_source_with_lossless_sibling_leaves_outputs_alone(self, tmp_path):
        src = tmp_path / "src"
        _tone(src / "a.flac")
        _tone(src / "a.mp3", "-c:a", "libmp3lame")
        config = _config(src, {"mp3": ""}, tmp_path, rg=False)
        initial_sync(config)
        out = tmp_path / "mp3" / "a.mp3"
        before = out.read_bytes()
        config.replaygain = True
        process_source_file(str(src / "a.mp3"), config, check_stable=False)
        assert abs(_gain(src / "a.mp3") - EXPECTED_GAIN) < 0.5
        assert out.read_bytes() == before  # the FLAC's encode, untouched

    def test_one_bad_output_does_not_stop_the_others(self, tmp_path, caplog):
        src = tmp_path / "src"
        _tone(src / "a.flac")
        config = _config(src, {"alac": "", "mp3": ""}, tmp_path, rg=False)
        initial_sync(config)
        config.replaygain = True
        real = replaygain.write_track_tags

        def flaky(path, values):
            if path.endswith(".m4a"):
                raise OSError("read-only")
            return real(path, values)

        with patch.object(replaygain, "write_track_tags", side_effect=flaky):
            initial_sync(config)
        assert "could not tag" in caplog.text
        assert abs(_gain(tmp_path / "mp3" / "a.mp3") - EXPECTED_GAIN) < 0.5

    def test_tagging_error_never_blocks_the_encode(self, tmp_path, caplog):
        src = tmp_path / "src"
        _tone(src / "a.flac")
        config = _config(src, {"mp3": ""}, tmp_path)
        boom = RuntimeError("odd tags")
        with patch.object(replaygain, "tag_source", side_effect=boom):
            process_source_file(str(src / "a.flac"), config, check_stable=False)
        assert (tmp_path / "mp3" / "a.mp3").exists()
        assert "could not tag" in caplog.text

    def test_unreadable_source_tags_carry_nothing(self, tmp_path):
        src = tmp_path / "src"
        _tone(src / "a.flac")
        config = _config(src, {"mp3": ""}, tmp_path)
        with patch.object(replaygain, "read_track_tags", side_effect=OSError("io")):
            sync_mod._carry_replaygain(str(src / "a.flac"), config)
