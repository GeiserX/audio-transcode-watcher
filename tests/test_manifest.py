"""Tests for the per-output provenance manifest."""

import json

import pytest

from audio_transcode_watcher import manifest


@pytest.fixture(autouse=True)
def _clean():
    for store in (manifest._rows, manifest._dirty, manifest._last_write):
        store.clear()
    yield
    for store in (manifest._rows, manifest._dirty, manifest._last_write):
        store.clear()


def _read(root):
    return json.loads((root / manifest.MANIFEST_NAME).read_text())


def test_nothing_written_until_something_changes(tmp_path):
    manifest.flush(str(tmp_path), force=True)
    assert not (tmp_path / manifest.MANIFEST_NAME).exists()


def test_flush_is_throttled_unless_forced(tmp_path):
    src = tmp_path / "a.flac"
    src.write_bytes(b"abc")
    root = str(tmp_path)
    manifest.record(root, str(tmp_path / "a.m4a"), root, str(src), "encode")
    manifest.flush(root)
    assert set(_read(tmp_path)) == {"a.m4a"}

    manifest.record(root, str(tmp_path / "b.m4a"), root, str(src), "encode")
    manifest.flush(root)  # within FLUSH_INTERVAL: not yet
    assert set(_read(tmp_path)) == {"a.m4a"}
    manifest.flush(root, force=True)
    assert set(_read(tmp_path)) == {"a.m4a", "b.m4a"}
    assert _read(tmp_path)["a.m4a"]["size"] == 3


def test_missing_source_records_unknown_size(tmp_path):
    root = str(tmp_path)
    manifest.record(
        root, str(tmp_path / "x.m4a"), root, str(tmp_path / "gone.flac"), "copy"
    )
    row = manifest.lookup(root, str(tmp_path / "x.m4a"))
    assert row["size"] is None and row["mtime"] is None and row["kind"] == "copy"


def test_non_object_manifest_is_ignored(tmp_path, caplog):
    (tmp_path / manifest.MANIFEST_NAME).write_text("[1, 2]")
    assert manifest.lookup(str(tmp_path), str(tmp_path / "x.m4a")) is None
    assert "not a JSON object" in caplog.text


def test_write_failure_is_logged_and_retried_later(tmp_path, caplog):
    root = tmp_path / "missing-output"
    manifest.record(
        str(root),
        str(root / "x.m4a"),
        str(tmp_path),
        str(tmp_path / "x.flac"),
        "encode",
    )
    manifest.flush(str(root), force=True)
    assert "Could not write manifest" in caplog.text
    assert str(root) in manifest._dirty


def test_prune_drops_rows_without_a_file(tmp_path):
    root = str(tmp_path)
    (tmp_path / "here.m4a").touch()
    for name in ("here.m4a", "gone.m4a"):
        manifest.record(
            root, str(tmp_path / name), root, str(tmp_path / "s.flac"), "encode"
        )
    assert manifest.prune(root) == 1
    assert manifest.lookup(root, str(tmp_path / "gone.m4a")) is None
    assert manifest.lookup(root, str(tmp_path / "here.m4a")) is not None


def test_nested_paths_are_relative_to_the_output(tmp_path):
    src_root = tmp_path / "src"
    (src_root / "Artist").mkdir(parents=True)
    src = src_root / "Artist" / "X.ogg"
    src.touch()
    out_root = tmp_path / "out"
    manifest.record(
        str(out_root),
        str(out_root / "Artist" / "X.mp3"),
        str(src_root),
        str(src),
        "transcode",
    )
    out_root.mkdir()
    manifest.flush_all(force=True)
    assert _read(out_root) == {
        "Artist/X.mp3": {
            "source": "Artist/X.ogg",
            "size": 0,
            "mtime": src.stat().st_mtime,
            "kind": "transcode",
        }
    }
