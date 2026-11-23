"""Utility functions for audio-transcode-watcher."""

from __future__ import annotations

import logging
import os
import time
import unicodedata
from collections.abc import Callable
from fnmatch import fnmatchcase
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

# Audio file extensions considered as source files
LOSSLESS_EXTENSIONS = {
    ".flac",
    ".alac",
    ".wav",
    ".aiff",
    ".aif",
    ".ape",
    ".wv",
    ".tta",
    ".tak",
}

LOSSY_EXTENSIONS = {".mp3", ".aac", ".m4a", ".ogg", ".opus", ".wma"}

AUDIO_EXTENSIONS = LOSSLESS_EXTENSIONS | LOSSY_EXTENSIONS

# Codec of a lossy source, by extension, in the vocabulary of output codecs.
# A lossy source is copied unchanged into a lossy output of the same codec.
LOSSY_SOURCE_CODECS = {
    ".mp3": "mp3",
    ".aac": "aac",
    ".m4a": "aac",
    ".ogg": "vorbis",
    ".opus": "opus",
    ".wma": "wma",
}

# Sidecar file extensions to copy alongside transcoded audio
SIDECAR_EXTENSIONS = {".lrc"}


def nfc(s: str) -> str:
    """Normalize string to NFC Unicode form."""
    return unicodedata.normalize("NFC", s)


def nfc_path(p: str) -> str:
    """Normalize every path component to NFC Unicode form."""
    parts = list(Path(p).parts)
    if not parts:
        return p

    if parts[0] == os.sep:
        # Absolute path handling on POSIX
        normalized = os.sep + os.path.join(*[nfc(x) for x in parts[1:]])
    else:
        normalized = os.path.join(*[nfc(x) for x in parts])

    return normalized


def has_audio_extension(path: str) -> bool:
    """Check if a path has a recognized audio file extension (no existence check).

    Use this instead of is_audio_file() when the file may no longer exist
    on disk (e.g. in watchdog on_deleted / on_moved handlers).
    """
    return Path(nfc_path(path)).suffix.lower() in AUDIO_EXTENSIONS


def is_audio_file(path: str) -> bool:
    """Check if a file is a recognized audio file (must exist on disk)."""
    normalized = nfc_path(path)
    if not os.path.isfile(normalized):
        return False
    return Path(normalized).suffix.lower() in AUDIO_EXTENSIONS


def has_sidecar_extension(path: str) -> bool:
    """Check if a path has a recognized sidecar file extension (e.g. .lrc)."""
    return Path(nfc_path(path)).suffix.lower() in SIDECAR_EXTENSIONS


def is_lossless(path: str) -> bool:
    """Check if a file is a lossless audio file."""
    return Path(path).suffix.lower() in LOSSLESS_EXTENSIONS


def is_lossy(path: str) -> bool:
    """Check if a file is a lossy audio file."""
    return Path(path).suffix.lower() in LOSSY_EXTENSIONS


def lossy_source_codec(path: str) -> str | None:
    """Return the codec of a lossy source (e.g. ``"mp3"``), or None if lossless."""
    return LOSSY_SOURCE_CODECS.get(Path(path).suffix.lower())


def is_mp3(path: str) -> bool:
    """Check if a file is an MP3."""
    return Path(path).suffix.lower() == ".mp3"


def appears_empty_dir(path: str) -> bool:
    """
    Check if a directory appears empty (no non-hidden files).

    Returns True if:
    - Directory doesn't exist
    - Directory is not accessible
    - Directory contains only hidden files (starting with .)
    """
    try:
        if not os.path.isdir(path):
            return True

        with os.scandir(path) as entries:
            for entry in entries:
                if not entry.name.startswith("."):
                    return False
        return True
    except Exception:
        # On any error, treat as empty to be safe
        logger.debug("Cannot list %s, treating it as empty", path, exc_info=True)
        return True


def wait_for_stable(
    path: str, min_stable_secs: float = 1.0, timeout: float = 60.0
) -> bool:
    """
    Wait until file size stays unchanged for min_stable_secs.

    Args:
        path: Path to the file
        min_stable_secs: Minimum seconds the file must be stable
        timeout: Maximum time to wait

    Returns:
        True if file is stable, False if timed out or file doesn't exist
    """
    path = nfc_path(path)
    start = time.time()

    try:
        last_size = os.path.getsize(path)
    except OSError:
        return False

    last_change = time.time()

    while time.time() - start < timeout:
        try:
            cur_size = os.path.getsize(path)
        except OSError:
            return False

        if cur_size != last_size:
            last_size = cur_size
            last_change = time.time()
        elif time.time() - last_change >= min_stable_secs:
            return True

        time.sleep(0.2)

    return False


def get_output_filename(source_path: str, extension: str) -> str:
    """
    Get the output filename for a source file.

    Uses the stem of the source file and adds the new extension.
    All components are NFC-normalized.
    """
    stem = nfc(Path(source_path).stem)
    return f"{stem}{extension}"


def get_output_file_path(
    source_file: str,
    source_root: str,
    output_root: str,
    output_filename: str,
) -> str:
    """
    Compute destination path preserving source directory structure.

    For flat source dirs the result is ``output_root/output_filename``.
    For nested dirs the relative subdirectory is mirrored in the output.
    """
    rel_dir = os.path.relpath(os.path.dirname(source_file), source_root)
    if rel_dir == ".":
        return nfc_path(os.path.join(output_root, output_filename))
    return nfc_path(os.path.join(output_root, rel_dir, output_filename))


def get_rel_stem(filepath: str, root: str) -> str:
    """
    Return the relative path from *root* with the file extension stripped.

    Example: ``get_rel_stem("/music/flac/album/song.flac", "/music/flac")``
    returns ``"album/song"``.  For flat dirs it returns just the stem.
    """
    rel = os.path.relpath(filepath, root)
    rel_dir = os.path.dirname(rel)
    stem = nfc(Path(rel).stem)
    if rel_dir == ".":
        return stem
    return nfc_path(os.path.join(rel_dir, stem))


def ignore_entry_parts(entry: str) -> tuple[str, ...]:
    """Split an ignored_files entry into NFC path parts.

    Leading and trailing slashes and ``.`` parts are dropped, so
    ``.stversions``, ``/.stversions/`` and ``./.stversions`` are one entry.
    """
    return Path(nfc(entry).strip("/")).parts


@lru_cache(maxsize=32)
def _ignore_rules(entries: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
    return tuple(p for p in map(ignore_entry_parts, entries) if p)


def _part_matches(name: str, pattern: str) -> bool:
    # An exact name always matches, so an entry such as "Live [1999]" that
    # worked before patterns existed still names that folder.
    return name == pattern or fnmatchcase(name, pattern)


def _prefix_matches(pattern: tuple[str, ...], parts: tuple[str, ...]) -> bool:
    """True if *pattern* matches the first parts of *parts*, part by part.

    ``**`` matches any number of parts, none included. Matching a prefix
    means an entry that names a folder covers everything inside it.
    """
    if not pattern:
        return True
    if pattern[0] == "**":
        rest = pattern[1:]
        return any(_prefix_matches(rest, parts[i:]) for i in range(len(parts) + 1))
    if not parts or not _part_matches(parts[0], pattern[0]):
        return False
    return _prefix_matches(pattern[1:], parts[1:])


def matches_ignored(rel_path: str, entries: list[str]) -> bool:
    """True if *rel_path*, relative to the source folder, matches an entry.

    Each entry is a path whose parts are compared one by one with fnmatch
    rules (``*``, ``?``, ``[...]``, case-sensitive). ``**`` matches any
    number of folders, so ``**/.stversions`` matches that folder at any
    depth; any other entry is anchored at the source folder.
    """
    if not entries:
        return False
    parts = Path(nfc_path(rel_path)).parts
    if not parts or parts[0] == os.pardir:
        return False
    return any(_prefix_matches(p, parts) for p in _ignore_rules(tuple(entries)))


def walk_audio_files(
    directory: str,
    ignored: Callable[[str], bool] | None = None,
    counts: dict[str, int] | None = None,
) -> list[str]:
    """Recursively collect all audio files under *directory*.

    With *ignored*, a folder for which it returns True is not entered and a
    file for which it returns True is left out. *counts*, if given, gets
    the number of skipped ``folders`` and audio ``files``.
    """
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(directory):
        if ignored is not None:
            kept = [d for d in dirnames if not ignored(os.path.join(dirpath, d))]
            if counts is not None:
                counts["folders"] = counts.get("folders", 0) + len(dirnames) - len(kept)
            dirnames[:] = kept
        for fname in filenames:
            full = os.path.join(dirpath, fname)
            if has_audio_extension(full):
                if ignored is not None and ignored(full):
                    if counts is not None:
                        counts["files"] = counts.get("files", 0) + 1
                    continue
                files.append(full)
    return files


def has_unignored_audio(directory: str, ignored: Callable[[str], bool]) -> bool:
    """True if *directory* holds at least one audio file *ignored* keeps.

    Stops at the first one, so it is cheap for a normal library.
    """
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames[:] = [d for d in dirnames if not ignored(os.path.join(dirpath, d))]
        for fname in filenames:
            full = os.path.join(dirpath, fname)
            if has_audio_extension(full) and not ignored(full):
                return True
    return False


def remove_empty_dirs(root: str) -> None:
    """Remove empty subdirectories bottom-up (never removes *root* itself)."""
    for dirpath, _dirnames, _filenames in os.walk(root, topdown=False):
        if dirpath == root:
            continue
        try:
            if not os.listdir(dirpath):
                os.rmdir(dirpath)
        except OSError:
            pass
