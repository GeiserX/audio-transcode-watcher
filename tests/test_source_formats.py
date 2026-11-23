"""source.formats, and ALAC .m4a sources treated as lossless."""

import logging
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from mutagen.mp4 import MP4, MP4FreeForm

import audio_transcode_watcher.sync as sync_mod
from audio_transcode_watcher import manifest, utils
from audio_transcode_watcher.config import Config, OutputConfig
from audio_transcode_watcher.encoder import copy_mp4_tags
from audio_transcode_watcher.main import main
from audio_transcode_watcher.sync import (
    _cleanup_orphans,
    _yields_to_twin,
    delete_outputs,
    initial_sync,
    is_ignored_file,
    plan_output,
    process_source_file,
    safety_guard_active,
    walk_sources,
)
from audio_transcode_watcher.watcher import AudioSyncHandler

ALL_FORMATS = [
    "flac",
    "alac",
    "wav",
    "aiff",
    "ape",
    "wavpack",
    "tta",
    "tak",
    "mp3",
    "aac",
    "vorbis",
    "opus",
    "wma",
]

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are not installed; the real-file checks need them",
)


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.setattr(utils, "_probe_cache", {})
    monkeypatch.setattr(sync_mod, "ORPHAN_MIN_AGE", 0.0)
    monkeypatch.setattr(sync_mod, "_last_safety_log_ts", 0.0)
    monkeypatch.setattr(sync_mod, "_unignored_audio_seen", {})
    sync_mod._failed_sources.clear()
    manifest._rows.clear()
    manifest._dirty.clear()
    yield
    sync_mod._failed_sources.clear()
    manifest._rows.clear()
    manifest._dirty.clear()


@pytest.fixture
def probe(monkeypatch):
    """Answer ffprobe from a dict of file name -> codec, and count the runs."""
    answers: dict[str, str | None] = {}
    calls: list[str] = []

    def fake(path):
        calls.append(path)
        return answers.get(Path(path).name)

    monkeypatch.setattr(utils, "_run_ffprobe", fake)
    fake.answers = answers
    fake.calls = calls
    return fake


def _base(**source):
    return {
        "source": {"path": "/music/source", **source},
        "outputs": [{"name": "aac", "codec": "aac", "path": "/music/aac"}],
    }


def _config(temp_dir, formats=None, codecs=("alac",), **kwargs):
    source = Path(temp_dir) / "source"
    source.mkdir(exist_ok=True)
    outputs = []
    for codec in codecs:
        path = Path(temp_dir) / codec
        path.mkdir(exist_ok=True)
        outputs.append(OutputConfig(name=codec, codec=codec, path=str(path)))
    extra = {} if formats is None else {"source_formats": formats}
    config = Config(
        source_path=str(source),
        outputs=outputs,
        fetch_lyrics=False,
        parallel_workers=1,
        **extra,
        **kwargs,
    )
    return source, config


def _touch(root, *rels, content=b"x"):
    for rel in rels:
        p = Path(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)


def _files(root):
    return sorted(
        str(p.relative_to(root))
        for p in Path(root).rglob("*")
        if p.is_file() and p.name != manifest.MANIFEST_NAME
    )


def _fake_encode(content):
    def encode(cmd, dest, **_kwargs):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(content)
        return 0

    return encode


ENCODE = "audio_transcode_watcher.sync.atomic_ffmpeg_encode"


def _make(path, codec, seconds=1):
    """A short sine tone in *codec* at *path*, made by ffmpeg."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-metadata",
            "title=Tone",
            "-c:a",
            codec,
            str(path),
        ],
        check=True,
    )


def _codec_of(path):
    """The codec of *path*'s audio, asked of ffprobe directly."""
    return subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


class TestConfig:
    def test_default_is_every_format(self):
        config = Config.from_dict(_base())
        assert config.source_formats == frozenset(ALL_FORMATS)
        assert not config.formats_restricted
        assert not config.filters_sources

    def test_one_place_lists_every_name(self):
        assert list(utils.SOURCE_FORMATS) == ALL_FORMATS
        exts = {e for v in utils.SOURCE_FORMATS.values() for e in v}
        assert exts == utils.AUDIO_EXTENSIONS

    def test_listed_formats_load(self):
        config = Config.from_dict(_base(formats=["alac", "FLAC"]))
        assert config.source_formats == {"alac", "flac"}
        assert config.formats_restricted and config.filters_sources

    def test_every_name_listed_is_the_default(self):
        config = Config.from_dict(_base(formats=list(ALL_FORMATS)))
        assert not config.formats_restricted

    def test_yaml_list(self):
        data = yaml.safe_load(
            "source:\n  path: /m\n  formats: [alac, mp3]\n"
            "outputs:\n  - {name: a, codec: aac, path: /o}\n"
        )
        assert Config.from_dict(data).source_formats == {"alac", "mp3"}

    @pytest.mark.parametrize(
        "value",
        [[], "flac", None, ["flac", "ogg"], [1], {"flac": True}],
        ids=["empty", "string", "no-items", "unknown", "number", "mapping"],
    )
    def test_bad_values_stop_startup(self, value):
        with pytest.raises(ValueError) as err:
            Config.from_dict(_base(formats=value))
        assert "source.formats" in str(err.value)
        assert "Valid: " + ", ".join(ALL_FORMATS) in str(err.value)

    def test_unknown_name_is_named(self):
        with pytest.raises(ValueError, match="Unknown: ogg"):
            Config.from_dict(_base(formats=["flac", "ogg"]))

    @patch("time.sleep", side_effect=KeyboardInterrupt)
    @patch("audio_transcode_watcher.main.start_watcher", return_value=MagicMock())
    @patch("audio_transcode_watcher.main.initial_sync")
    @patch("audio_transcode_watcher.main.load_config")
    @patch("os.path.isdir", return_value=True)
    def test_startup_logs_a_restricted_list_only(
        self, _isdir, mock_load, _sync, _watcher, _sleep, caplog
    ):
        mock_load.return_value = Config.from_dict(_base(formats=["mp3", "alac"]))
        with caplog.at_level(logging.INFO):
            main()
        assert "Source formats: alac, mp3" in caplog.text
        caplog.clear()
        mock_load.return_value = Config.from_dict(_base())
        with caplog.at_level(logging.INFO):
            main()
        assert "Source formats" not in caplog.text


class TestProbe:
    def test_one_probe_per_file_version(self, temp_dir, probe):
        path = Path(temp_dir) / "a.m4a"
        path.write_bytes(b"x")
        probe.answers["a.m4a"] = "alac"
        assert utils.source_format(str(path)) == "alac"
        assert utils.is_lossless(str(path))
        assert utils.lossy_source_codec(str(path)) is None
        assert len(probe.calls) == 1
        path.write_bytes(b"xy")  # a new size is a new version
        probe.answers["a.m4a"] = "aac"
        assert utils.source_format(str(path)) == "aac"
        assert len(probe.calls) == 2

    def test_a_gone_file_answers_what_it_last_answered(self, temp_dir, probe):
        path = Path(temp_dir) / "a.m4a"
        path.write_bytes(b"x")
        probe.answers["a.m4a"] = "alac"
        assert utils.is_lossless(str(path))
        path.unlink()
        assert utils.is_lossless(str(path))
        assert len(probe.calls) == 1

    def test_other_extensions_are_never_probed(self, probe):
        assert utils.source_format("x.flac") == "flac"
        assert utils.source_format("x.ogg") == "vorbis"
        assert utils.source_format("x.wv") == "wavpack"
        assert utils.source_format("x.txt") is None
        assert probe.calls == []

    def test_unknown_codec_counts_as_aac(self, temp_dir, probe):
        path = Path(temp_dir) / "a.m4a"
        path.write_bytes(b"x")
        probe.answers["a.m4a"] = "mp3"
        assert utils.source_format(str(path)) == "aac"

    def test_failure_is_logged_once_and_counts_as_aac(self, temp_dir, caplog):
        path = Path(temp_dir) / "broken.m4a"
        path.write_bytes(b"not an mp4 file")
        failed = subprocess.CompletedProcess([], 1, "", "Invalid data found\n")
        with (
            patch("subprocess.run", return_value=failed) as run,
            caplog.at_level(logging.WARNING),
        ):
            assert utils.source_format(str(path)) == "aac"
            assert utils.source_format(str(path)) == "aac"
        assert run.call_count == 1
        warnings = [r.message for r in caplog.records]
        assert warnings == [
            (
                f"Could not read the codec of {path}, going by its extension: "
                "Invalid data found"
            )
        ]

    def test_missing_ffprobe_counts_as_aac(self, temp_dir, caplog):
        path = Path(temp_dir) / "a.m4a"
        path.write_bytes(b"x")
        with (
            patch("subprocess.run", side_effect=FileNotFoundError("ffprobe")),
            caplog.at_level(logging.WARNING),
        ):
            assert not utils.is_lossless(str(path))
        assert "Could not read the codec" in caplog.text

    @needs_ffmpeg
    def test_real_files(self, temp_dir):
        alac, aac = Path(temp_dir) / "l.m4a", Path(temp_dir) / "y.m4a"
        _make(alac, "alac")
        _make(aac, "aac")
        assert utils.source_format(str(alac)) == "alac"
        assert utils.source_format(str(aac)) == "aac"


class TestFormatFilter:
    """A source whose format is not listed is treated like an ignored file."""

    def test_unlisted_format_is_ignored(self, temp_dir, probe):
        source, config = _config(temp_dir, ["flac"])
        _touch(source, "A/x.flac", "A/x.mp3", "A/notes.txt")
        assert not is_ignored_file(str(source / "A/x.flac"), config)
        assert is_ignored_file(str(source / "A/x.mp3"), config)
        assert not is_ignored_file(str(source / "A/notes.txt"), config)
        assert not is_ignored_file(str(source / "A/x.lrc"), config)

    def test_a_folder_is_never_ignored_for_its_name(self, temp_dir):
        source, config = _config(temp_dir, ["flac"])
        _touch(source, "Live.mp3/x.flac")
        assert not is_ignored_file(str(source / "Live.mp3"), config)
        assert [Path(f).name for f in walk_sources(config)] == ["x.flac"]

    def test_alac_keeps_alac_m4a_and_skips_aac_m4a(self, temp_dir, probe):
        source, config = _config(temp_dir, ["alac"])
        _touch(source, "a.m4a", "b.m4a", "c.flac")
        probe.answers.update({"a.m4a": "alac", "b.m4a": "aac"})
        found = sorted(Path(f).name for f in walk_sources(config))
        assert found == ["a.m4a"]

    def test_aac_keeps_aac_m4a_and_skips_alac_m4a(self, temp_dir, probe):
        source, config = _config(temp_dir, ["aac"])
        _touch(source, "a.m4a", "b.m4a", "c.aac")
        probe.answers.update({"a.m4a": "alac", "b.m4a": "aac"})
        found = sorted(Path(f).name for f in walk_sources(config))
        assert found == ["b.m4a", "c.aac"]

    def test_m4a_is_not_probed_when_both_are_listed(self, temp_dir, probe):
        source, config = _config(temp_dir, ["alac", "aac"])
        _touch(source, "a.m4a", "c.flac")
        assert [Path(f).name for f in walk_sources(config)] == ["a.m4a"]
        assert probe.calls == []

    def test_unlisted_source_gets_no_output(self, temp_dir):
        source, config = _config(temp_dir, ["flac"], codecs=("mp3",))
        _touch(source, "x.mp3")
        with patch(ENCODE, side_effect=_fake_encode(b"enc")) as enc:
            process_source_file(str(source / "x.mp3"), config, check_stable=False)
        enc.assert_not_called()
        assert _files(Path(temp_dir) / "mp3") == []

    def test_outputs_of_unlisted_sources_are_orphans(self, temp_dir):
        source, config = _config(temp_dir, ["flac"], codecs=("alac",))
        out = Path(temp_dir) / "alac"
        _touch(source, "A/x.flac", "A/y.mp3", "A/y.lrc")
        _touch(out, "A/x.m4a", "A/y.mp3", "A/y.lrc")
        _cleanup_orphans(config)
        assert _files(out) == ["A/x.m4a"]

    def test_an_unlisted_lossless_twin_gives_the_lossy_file_its_output(self, temp_dir):
        source, config = _config(temp_dir, ["mp3"], codecs=("mp3",))
        _touch(source, "x.flac", "x.mp3")
        with patch(ENCODE, side_effect=_fake_encode(b"enc")) as enc:
            process_source_file(str(source / "x.mp3"), config, check_stable=False)
        enc.assert_not_called()  # an MP3 into an MP3 output is a copy
        assert _files(Path(temp_dir) / "mp3") == ["x.mp3"]

    def test_guard_trips_when_no_listed_format_is_left(self, temp_dir, caplog):
        source, config = _config(temp_dir, ["flac"])
        _touch(source, "A/x.mp3", "A/y.ogg")
        out = Path(temp_dir) / "alac"
        _touch(out, "A/x.mp3")
        with caplog.at_level(logging.INFO, logger="audio_transcode_watcher.sync"):
            initial_sync(config)
        assert _files(out) == ["A/x.mp3"]
        warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings == [
            (
                "Safety guard active: every audio file in the source matches "
                "source.formats, so the source counts as empty"
            )
        ]

    def test_guard_names_both_settings(self, temp_dir, caplog):
        source, config = _config(temp_dir, ["flac"], ignored_files=["Old"])
        _touch(source, "Old/x.flac", "A/y.mp3")
        with caplog.at_level(logging.WARNING, logger="audio_transcode_watcher.sync"):
            assert safety_guard_active(config)
        assert "matches settings.ignored_files or source.formats" in caplog.text

    @patch("audio_transcode_watcher.sync._cleanup_orphans")
    @patch("audio_transcode_watcher.sync.process_source_file")
    def test_full_sync_counts_skipped_files(
        self, mock_proc, _orphans, temp_dir, caplog
    ):
        source, config = _config(temp_dir, ["flac"])
        _touch(source, "A/x.flac", "A/y.mp3", "B/z.wav")
        with caplog.at_level(logging.INFO, logger="audio_transcode_watcher.sync"):
            initial_sync(config, periodic=True)
        assert mock_proc.call_count == 1
        assert (
            "source.formats: 0 folders not read, 2 more audio files skipped"
            in caplog.text
        )

    def test_watcher_ignores_events_for_unlisted_formats(self, temp_dir):
        source, config = _config(temp_dir, ["flac"])
        _touch(source, "x.mp3")
        handler = AudioSyncHandler(config)
        event = MagicMock(is_directory=False, src_path=str(source / "x.mp3"))
        with patch.object(handler, "_process_later") as later:
            handler.on_created(event)
        later.assert_not_called()


class TestAlacSourceIsLossless:
    """An ALAC .m4a source is encoded like any other lossless source."""

    def test_plan(self, temp_dir, probe):
        source, config = _config(temp_dir, codecs=("alac", "aac", "flac", "mp3"))
        _touch(source, "x.m4a")
        probe.answers["x.m4a"] = "alac"
        src = str(source / "x.m4a")
        plans = [plan_output(src, o) for o in config.outputs]
        assert plans == [
            ("x.m4a", "encode"),
            ("x.m4a", "encode"),
            ("x.flac", "encode"),
            ("x.mp3", "encode"),
        ]

    def test_aac_m4a_plan_is_unchanged(self, temp_dir, probe):
        source, config = _config(temp_dir, codecs=("alac", "aac", "flac"))
        _touch(source, "x.m4a")
        probe.answers["x.m4a"] = "aac"
        src = str(source / "x.m4a")
        plans = [plan_output(src, o) for o in config.outputs]
        assert plans == [("x.m4a", "copy"), ("x.m4a", "copy"), ("x.m4a", "copy")]

    def test_alac_wins_over_a_lossy_twin(self, temp_dir, probe):
        source, config = _config(temp_dir, codecs=("alac",))
        out = Path(temp_dir) / "alac"
        _touch(source, "x.m4a", "x.mp3")
        probe.answers["x.m4a"] = "alac"
        assert _yields_to_twin(str(source / "x.mp3"), config)
        assert not _yields_to_twin(str(source / "x.m4a"), config)
        _touch(out, "x.m4a", "x.mp3")  # x.mp3: an old copy of the MP3
        _cleanup_orphans(config)
        assert _files(out) == ["x.m4a"]

    def test_alac_wins_over_a_lossy_twin_at_process_time(self, temp_dir, probe):
        source, config = _config(temp_dir, codecs=("alac",))
        out = Path(temp_dir) / "alac"
        _touch(source, "x.m4a", "x.mp3")
        probe.answers["x.m4a"] = "alac"
        with patch(ENCODE, side_effect=_fake_encode(b"enc")) as enc:
            process_source_file(str(source / "x.mp3"), config, check_stable=False)
            enc.assert_not_called()
            assert _files(out) == []
            process_source_file(str(source / "x.m4a"), config, check_stable=False)
        assert (out / "x.m4a").read_bytes() == b"enc"

    def test_flac_still_wins_over_an_alac_twin(self, temp_dir, probe):
        source, config = _config(temp_dir, codecs=("aac",))
        _touch(source, "x.flac", "x.m4a")
        probe.answers["x.m4a"] = "alac"
        assert _yields_to_twin(str(source / "x.m4a"), config)
        assert not _yields_to_twin(str(source / "x.flac"), config)

    def test_deleting_an_alac_source_removes_its_outputs(self, temp_dir, probe):
        source, config = _config(temp_dir, codecs=("flac", "aac"))
        _touch(source, "x.m4a", "y.flac")
        probe.answers["x.m4a"] = "alac"
        assert utils.is_lossless(str(source / "x.m4a"))
        _touch(Path(temp_dir) / "flac", "x.flac", "y.flac")
        _touch(Path(temp_dir) / "aac", "x.m4a", "y.m4a")
        (source / "x.m4a").unlink()
        delete_outputs(str(source / "x.m4a"), config)
        assert _files(Path(temp_dir) / "flac") == ["y.flac"]
        assert _files(Path(temp_dir) / "aac") == ["y.m4a"]

    def test_an_old_copy_in_the_aac_output_is_replaced(self, temp_dir, probe, caplog):
        """Before, an ALAC source was copied as is into an AAC output."""
        source, config = _config(temp_dir, codecs=("aac",))
        out = Path(temp_dir) / "aac"
        _touch(source, "x.m4a", content=b"lossless")
        src = str(source / "x.m4a")
        probe.answers["x.m4a"] = "aac"  # how every .m4a was read before
        process_source_file(src, config, check_stable=False)
        assert (out / "x.m4a").read_bytes() == b"lossless"
        utils._probe_cache.clear()
        probe.answers["x.m4a"] = "alac"
        with (
            patch(ENCODE, side_effect=_fake_encode(b"aac encode")),
            caplog.at_level(logging.INFO, logger="audio_transcode_watcher.sync"),
        ):
            process_source_file(src, config, check_stable=False)
        assert (out / "x.m4a").read_bytes() == b"aac encode"
        assert manifest.lookup(str(out), str(out / "x.m4a"))["kind"] == "encode"
        assert f"Replacing {out / 'x.m4a'}: it is a copy of {src}" in caplog.text

    def test_encode_of_an_unlisted_twin_is_rebuilt(self, temp_dir, caplog):
        source, config = _config(temp_dir, codecs=("aac",))
        out = Path(temp_dir) / "aac"
        _touch(source, "x.flac", "x.mp3")
        with patch(ENCODE, side_effect=_fake_encode(b"from flac")):
            process_source_file(str(source / "x.flac"), config, check_stable=False)
        config.source_formats = frozenset({"mp3"})
        with (
            patch(ENCODE, side_effect=_fake_encode(b"from mp3")),
            caplog.at_level(logging.INFO, logger="audio_transcode_watcher.sync"),
        ):
            process_source_file(str(source / "x.mp3"), config, check_stable=False)
        assert (out / "x.m4a").read_bytes() == b"from mp3"
        assert "its source is left out by source.formats" in caplog.text


@needs_ffmpeg
class TestRealAlacSource:
    """End to end with real files, through ffmpeg and ffprobe."""

    def test_alac_source_lands_lossless_and_lossy(self, temp_dir):
        source, config = _config(temp_dir, codecs=("alac", "aac", "flac", "mp3"))
        _make(source / "Album/x.m4a", "alac")
        m4a = MP4(source / "Album/x.m4a")
        m4a.tags["----:com.apple.iTunes:replaygain_track_gain"] = [
            MP4FreeForm(b"-3.00 dB")
        ]
        m4a.save()
        initial_sync(config)
        root = Path(temp_dir)
        assert _codec_of(root / "alac/Album/x.m4a") == "alac"
        assert _codec_of(root / "aac/Album/x.m4a") == "aac"
        assert _codec_of(root / "flac/Album/x.flac") == "flac"
        assert _codec_of(root / "mp3/Album/x.mp3") == "mp3"
        assert _files(root / "flac") == ["Album/x.flac"]
        aac = MP4(root / "aac/Album/x.m4a")
        gain = aac.tags["----:com.apple.iTunes:replaygain_track_gain"]
        assert bytes(gain[0]) == b"-3.00 dB"

    def test_alac_only_skips_aac_sources(self, temp_dir):
        source, config = _config(temp_dir, ["alac"], codecs=("mp3",))
        _make(source / "l.m4a", "alac")
        _make(source / "y.m4a", "aac")
        initial_sync(config)
        assert _files(Path(temp_dir) / "mp3") == ["l.mp3"]


class TestMp4SourceTags:
    def test_freeform_atoms_of_an_m4a_source_are_copied(self, temp_dir):
        src, dest = Path(temp_dir) / "s.m4a", Path(temp_dir) / "d.m4a"
        with patch("mutagen.File") as open_file:
            from mutagen.mp4 import MP4Tags

            tags = MP4Tags()
            tags["----:com.apple.iTunes:MusicBrainz Track Id"] = [MP4FreeForm(b"id")]
            tags["----:com.apple.iTunes:replaygain_track_gain"] = [
                MP4FreeForm(b"-1.00 dB")
            ]
            tags["soar"] = ["Artist, The"]
            open_file.return_value = MagicMock(tags=tags)
            mp4 = MagicMock(tags={})
            with patch("audio_transcode_watcher.encoder.MP4", return_value=mp4):
                assert copy_mp4_tags(str(src), str(dest)) == 3
        assert mp4.tags["----:com.apple.iTunes:MusicBrainz Track Id"] == [
            MP4FreeForm(b"id")
        ]
        assert mp4.tags["soar"] == ["Artist, The"]
        mp4.save.assert_called_once()
