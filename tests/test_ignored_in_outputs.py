"""Tests for settings.ignored_files_in_outputs (issue #55)."""

import hashlib
import os
import unicodedata
from pathlib import Path
from unittest.mock import patch

import pytest

import audio_transcode_watcher.sync as sync_mod
from audio_transcode_watcher import manifest
from audio_transcode_watcher.config import Config, OutputConfig
from audio_transcode_watcher.sync import (
    cleanup_stale_temp_files,
    delete_outputs,
    initial_sync,
    is_ignored_file,
    plan_output,
    purge_all_outputs,
)
from audio_transcode_watcher.utils import get_output_file_path

from .test_sync import _fake_encode, _files, _touch, _tree_config

SYNCTHING = [".stfolder", ".stignore", ".stversions"]
ENCODE = "audio_transcode_watcher.sync.atomic_ffmpeg_encode"


@pytest.fixture(autouse=True)
def _no_age_guards(monkeypatch, fresh_manifests):
    """Every file the test makes counts as old enough to clean up."""
    monkeypatch.setattr(sync_mod, "ORPHAN_MIN_AGE", 0.0)
    monkeypatch.setattr(sync_mod, "TEMP_MIN_AGE", 0.0)
    monkeypatch.setattr(sync_mod, "_last_safety_log_ts", 0.0)
    sync_mod._unignored_audio_seen.clear()
    sync_mod._failed_sources.clear()
    yield
    sync_mod._unignored_audio_seen.clear()
    sync_mod._failed_sources.clear()


@pytest.fixture
def fresh_manifests():
    for cache in (manifest._rows, manifest._dirty, manifest._last_write):
        cache.clear()
    yield
    for cache in (manifest._rows, manifest._dirty, manifest._last_write):
        cache.clear()


def _keep_config(temp_dir, ignored, codec="aac", keep=True, **kwargs):
    source, out, config = _tree_config(temp_dir, ignored, codec=codec, **kwargs)
    config.ignored_files_in_outputs = keep
    return source, out, config


def _snapshot(root, *rels):
    """Every path under *rels* in *root*, with a sha256 for each file."""
    found = {}
    for rel in rels:
        top = Path(root) / rel
        for p in [top, *(top.rglob("*") if top.is_dir() else [])]:
            key = str(p.relative_to(root))
            if p.is_file():
                found[key] = hashlib.sha256(p.read_bytes()).hexdigest()
            elif p.is_dir():
                found[key] = "dir"
    return found


def _cfg(entries, keep=True):
    return Config(
        source_path="/music/src",
        outputs=[OutputConfig(name="out", codec="aac", path="/music/out")],
        ignored_files=entries,
        ignored_files_in_outputs=keep,
    )


OUT = "/music/out"
NFD_CAFE = unicodedata.normalize("NFD", "Café")


class TestKeptInOutput:
    """The rule: ignored_files entries, relative to each output folder."""

    @pytest.mark.parametrize(
        ("entries", "rel", "folder", "expected"),
        [
            # Entries match by folder: everything inside is protected.
            ([".stversions"], ".stversions", True, True),
            ([".stversions"], ".stversions/x.m4a", False, True),
            ([".stversions"], ".stversions/x.lrc", False, True),
            ([".stversions"], ".stversions/x.m4a.tmp__ff", False, True),
            (["**/.stversions"], "A/B/.stversions/x.m4a", False, True),
            # A file name the watcher never writes is protected by its name.
            ([".stignore"], ".stignore", False, True),
            # A watcher file name is never protected by its own name.
            (["**/*.wav"], "Song.wav", False, False),
            (["Album/Song.m4a"], "Album/Song.m4a", False, False),
            ([".*"], ".stfolder", True, True),
            ([".*"], ".atw-manifest.json", False, False),
            ([".*"], ".atw-manifest.json.tmp", False, False),
            ([".*"], "X.m4a.tmp__ff", False, False),
            # The output folder itself is never protected.
            ([".*"], ".", True, False),
            (["*"], ".", True, False),
            # NFD names on disk match NFC entries.
            (["Café"], f"{NFD_CAFE}/x.m4a", False, True),
        ],
    )
    def test_rule(self, entries, rel, folder, expected):
        path = os.path.normpath(os.path.join(OUT, rel))
        got = sync_mod.kept_in_output(path, OUT, _cfg(entries), folder=folder)
        assert got is expected

    @pytest.mark.parametrize(
        ("rel", "folder"),
        [(".stversions", True), (".stversions/x.m4a", False), (".stignore", False)],
    )
    def test_flag_off_protects_nothing(self, rel, folder):
        config = _cfg([".stversions", ".stignore"], keep=False)
        path = os.path.join(OUT, rel)
        assert sync_mod.kept_in_output(path, OUT, config, folder=folder) is False

    def test_keeper_only_when_on_with_entries(self):
        assert sync_mod.output_keeper(OUT, _cfg([".stversions"], keep=False)) is None
        assert sync_mod.output_keeper(OUT, _cfg([])) is None
        keep = sync_mod.output_keeper(OUT, _cfg([".stversions"]))
        assert keep(os.path.join(OUT, ".stversions")) is True
        assert keep(os.path.join(OUT, "Album")) is False

    def test_a_kept_source_never_lands_in_a_protected_path(self, temp_dir):
        """So the writes need no guard: kept sources write only unprotected paths."""
        entries = [
            ".stfolder",
            ".stversions",
            "**/.stversions",
            "**/*.wav",
            "Album/song.mp3",
            ".*",
            "*.sync-conflict-*",
        ]
        sources = [
            "Album/song.flac",
            "Album/song.mp3",
            "Artist/Album/t.wav",
            "Song.flac",
            "Live/.hidden/x.flac",
        ]
        outputs = [
            OutputConfig(name=c, codec=c, path=os.path.join(temp_dir, c))
            for c in ("alac", "aac", "mp3", "opus", "flac", "wav")
        ]
        src = os.path.join(temp_dir, "src")
        config = Config(
            source_path=src,
            outputs=outputs,
            ignored_files=entries,
            ignored_files_in_outputs=True,
        )
        kept = [s for s in sources if not is_ignored_file(os.path.join(src, s), config)]
        assert kept == ["Album/song.flac", "Song.flac", "Live/.hidden/x.flac"]
        for rel in kept:
            source = os.path.join(src, rel)
            stem = Path(rel).stem
            for output in outputs:
                filename, _action = plan_output(source, output)
                for name in (filename, f"{stem}.lrc", f"{filename}.tmp__ff"):
                    path = get_output_file_path(source, src, output.path, name)
                    assert not sync_mod.kept_in_output(path, output.path, config)


class TestDefaultUnchanged:
    """Without the key, 0.8.0's deletions in the outputs stay."""

    def test_full_sync_still_removes_syncthing_files(self, temp_dir):
        source, out, config = _keep_config(temp_dir, SYNCTHING, keep=False)
        _touch(source, "Album/Song.flac")
        _touch(out, ".stversions/Album/Song~v.m4a", ".stversions/Album/Song~v.lrc")
        (out / ".stfolder").mkdir()
        with patch(ENCODE, side_effect=_fake_encode(b"enc")):
            initial_sync(config)
        assert _files(out) == ["Album/Song.m4a"]
        assert not (out / ".stfolder").exists()
        assert not (out / ".stversions").exists()

    def test_purge_still_removes_everything(self, temp_dir):
        source, out, config = _keep_config(temp_dir, SYNCTHING, keep=False)
        _touch(source, "Album/Song.flac")
        _touch(out, ".stignore", ".stfolder/syncthing-folder-x.txt", "Album/Song.m4a")
        purge_all_outputs(config)
        assert _files(out) == []


class TestIssue55:
    """A second Syncthing connection keeps its own files in the output."""

    def test_syncthing_files_in_the_output_survive(self, temp_dir):
        source, out, config = _keep_config(temp_dir, SYNCTHING)
        _touch(
            source,
            "Album/Song.flac",
            ".stversions/Album/Song~20261120-101010.flac",
            ".stfolder/syncthing-folder-src.txt",
            ".stignore",
        )
        _touch(
            out,
            ".stversions/Album/Song~20261120-101010.m4a",
            ".stversions/Album/Song~20261120-101010.lrc",
            ".stversions/Album/Song~20261121-090000.m4a",
            ".stversions/Album/Song~20261121-090000.lrc",
            ".stversions/OldAlbum/Mine~20261121-090000.m4a",
            ".stignore",
            "Gone.m4a",
            "GoneAlbum/Old.m4a",
        )
        (out / ".stfolder").mkdir()
        before = _snapshot(out, ".stfolder", ".stversions", ".stignore")
        with patch(ENCODE, side_effect=_fake_encode(b"enc")) as enc:
            initial_sync(config)
            initial_sync(config, periodic=True)
        assert _snapshot(out, ".stfolder", ".stversions", ".stignore") == before
        assert (out / "Album/Song.m4a").read_bytes() == b"enc"
        assert enc.call_count == 1
        assert not (out / "Gone.m4a").exists()
        assert not (out / "GoneAlbum").exists()

    def test_delete_outputs_keeps_an_empty_protected_folder(self, temp_dir):
        source, out, config = _keep_config(temp_dir, [".stfolder"])
        _touch(source, "Album/a.flac", "Album/b.flac")
        _touch(out, "Album/a.m4a")
        (out / ".stfolder").mkdir()
        (source / "Album/a.flac").unlink()
        delete_outputs(str(source / "Album/a.flac"), config)
        assert not (out / "Album/a.m4a").exists()
        assert (out / ".stfolder").is_dir()

    def test_purge_keeps_protected_paths(self, temp_dir):
        source, out, config = _keep_config(temp_dir, SYNCTHING, force_reencode=True)
        _touch(source, "Album/Song.flac")
        _touch(
            out,
            ".stignore",
            ".stfolder/syncthing-folder-x.txt",
            ".stversions/a.m4a",
            "Album/Song.m4a",
            "Album/Song.lrc",
            "notes.txt",
        )
        purge_all_outputs(config)
        assert _files(out) == [
            ".stfolder/syncthing-folder-x.txt",
            ".stignore",
            ".stversions/a.m4a",
        ]

    def test_stale_temps_in_protected_folders_are_kept(self, temp_dir):
        _source, out, config = _keep_config(temp_dir, [".stversions"])
        _touch(out, ".stversions/x.m4a.tmp__ff", "Album/x.m4a.tmp__ff")
        assert cleanup_stale_temp_files(config) == 1
        assert _files(out) == [".stversions/x.m4a.tmp__ff"]

    def test_safety_guard_still_stops_the_sync(self, temp_dir):
        source, out, config = _keep_config(temp_dir, ["Old", ".stversions"])
        _touch(source, "Old/a.flac")
        _touch(out, "Album/song.m4a", ".stversions/x.m4a")
        initial_sync(config)
        assert _files(out) == [".stversions/x.m4a", "Album/song.m4a"]


class TestWhatStaysUnprotected:
    """The key protects only through ignored_files paths in the outputs."""

    def test_own_wav_output_is_still_written_and_removed(self, temp_dir):
        source, out, config = _keep_config(temp_dir, ["**/*.wav"], codec="wav")
        _touch(source, "Song.flac", "Other.flac", "Keep.flac")
        with patch(ENCODE, side_effect=_fake_encode(b"enc")):
            initial_sync(config)
        assert _files(out) == ["Keep.wav", "Other.wav", "Song.wav"]
        (source / "Song.flac").unlink()
        initial_sync(config)
        assert _files(out) == ["Keep.wav", "Other.wav"]
        (source / "Other.flac").unlink()
        delete_outputs(str(source / "Other.flac"), config)
        assert _files(out) == ["Keep.wav"]

    def test_output_of_an_ignored_source_is_still_an_orphan(self, temp_dir):
        source, out, config = _keep_config(temp_dir, ["**/*.wav"])
        _touch(source, "Live.wav", "Song.flac")
        _touch(out, "Live.m4a")
        with patch(ENCODE, side_effect=_fake_encode(b"enc")):
            initial_sync(config)
        assert _files(out) == ["Song.m4a"]

    def test_output_of_a_format_left_out_is_still_an_orphan(self, temp_dir):
        source, out, config = _keep_config(
            temp_dir, [".stversions"], codec="mp3", source_formats=["flac"]
        )
        _touch(source, "Song.mp3", "Other.flac")
        _touch(out, "Song.mp3")
        with patch(ENCODE, side_effect=_fake_encode(b"enc")):
            initial_sync(config)
        assert _files(out) == ["Other.mp3"]
