"""ReplayGain 2.0 track tags.

A file is measured with FFmpeg's EBU R128 filter (``ebur128=peak=true``):

- ``REPLAYGAIN_TRACK_GAIN`` = -18 LUFS (the ReplayGain 2.0 reference) minus
  the integrated loudness, written like ``-6.32 dB``;
- ``REPLAYGAIN_TRACK_PEAK`` = the true peak as a linear value, ``0.988553``.

Album gain is not computed: an album is not something a folder of files can
be trusted to describe.

The tags are written in place with mutagen, in the file's own tag format:
Vorbis comments (FLAC, Ogg, Opus), ID3 ``TXXX`` frames (MP3, WAV, AIFF),
MP4 freeform atoms under the names Picard uses (M4A) and APEv2 (APE,
WavPack, TAK). Other formats (raw AAC, WMA) are left alone.

A tag write changes the file's size and modification time. Writes made
here are remembered, so the watcher can tell them from a real change and
does not re-encode the file because of its own edit.
"""

from __future__ import annotations

import logging
import math
import os
import re
import subprocess
import threading

import mutagen
from mutagen._vorbis import VCommentDict
from mutagen.apev2 import APEv2
from mutagen.id3 import ID3, TXXX, Encoding
from mutagen.mp4 import MP4FreeForm, MP4Tags

from .utils import nfc_path

logger = logging.getLogger(__name__)

REFERENCE_LUFS = -18.0
GAIN_TAG = "REPLAYGAIN_TRACK_GAIN"
PEAK_TAG = "REPLAYGAIN_TRACK_PEAK"
_TAGS = (GAIN_TAG, PEAK_TAG)

# Integrated loudness at or below this means the gate found nothing to
# measure (digital silence); such a file gets no tag.
SILENCE_LUFS = -70.0

# A measurement that runs longer than this (seconds) is killed and skipped.
MEASURE_TIMEOUT = 1800

_MP4_FREEFORM = "----:com.apple.iTunes:"

_INTEGRATED_RE = re.compile(r"I:\s+(-?(?:[\d.]+|inf))\s+LUFS")
_PEAK_RE = re.compile(r"Peak:\s+(-?(?:[\d.]+|inf))\s+dBFS")

_lock = threading.Lock()
# Source path -> (size, mtime_ns) when it was last checked, so a periodic
# sync does not reread every file's tags. Lives for the process lifetime.
_checked: dict[str, tuple[int, int]] = {}
# Path -> (size, mtime_ns) after a tag write made here; None while the
# write is still running.
_own_writes: dict[str, tuple[int, int] | None] = {}


def _signature(path: str) -> tuple[int, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


def measure(path: str) -> tuple[float, float] | None:
    """Return (integrated loudness in LUFS, true peak in dBTP), or None."""
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-nostats",
        "-i",
        path,
        "-map",
        "0:a:0",
        # framelog=verbose keeps the per-frame lines out of the info log,
        # so stderr holds only the summary.
        "-af",
        "ebur128=peak=true:framelog=verbose",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=MEASURE_TIMEOUT, check=False
        )
    except subprocess.TimeoutExpired:
        logger.warning("ReplayGain: measuring %s timed out", path)
        return None
    except OSError as e:
        logger.warning("ReplayGain: could not run ffmpeg for %s: %s", path, e)
        return None
    stderr = proc.stderr.decode("utf-8", errors="ignore") if proc.stderr else ""
    if proc.returncode != 0 or "Summary:" not in stderr:
        last = (stderr.strip().splitlines() or [""])[-1]
        logger.warning(
            "ReplayGain: could not measure %s (rc=%s): %s", path, proc.returncode, last
        )
        return None
    summary = stderr.rsplit("Summary:", 1)[1]
    integrated = _INTEGRATED_RE.search(summary)
    peak = _PEAK_RE.search(summary)
    if not integrated or not peak:
        logger.warning("ReplayGain: no loudness summary for %s", path)
        return None
    return float(integrated.group(1)), float(peak.group(1))


def track_values(integrated_lufs: float, peak_dbtp: float) -> dict[str, str] | None:
    """The two tag values for a measurement, or None for silence."""
    if not math.isfinite(integrated_lufs) or integrated_lufs <= SILENCE_LUFS:
        return None
    gain = REFERENCE_LUFS - integrated_lufs
    peak = 10 ** (peak_dbtp / 20) if math.isfinite(peak_dbtp) else 0.0
    return {GAIN_TAG: f"{gain:.2f} dB", PEAK_TAG: f"{peak:.6f}"}


def _writable(tags) -> bool:
    return isinstance(tags, (ID3, MP4Tags, VCommentDict, APEv2))


def _read(tags) -> dict[str, str]:
    """REPLAYGAIN_TRACK_GAIN and _PEAK from *tags*, whatever the format or case."""
    found: dict[str, str] = {}
    if isinstance(tags, ID3):
        for frame in tags.getall("TXXX"):
            name = frame.desc.upper()
            if name in _TAGS and frame.text:
                found[name] = str(frame.text[0])
    elif isinstance(tags, MP4Tags):
        for key, values in tags.items():
            if not key.startswith("----:") or not values:
                continue
            name = key.rsplit(":", 1)[-1].upper()
            if name in _TAGS:
                value = values[0]
                if isinstance(value, bytes):
                    value = value.decode("utf-8", "ignore")
                found[name] = str(value)
    else:
        for key, value in tags.items():
            name = str(key).upper()
            if name in _TAGS:
                if isinstance(value, list):
                    value = value[0] if value else ""
                found[name] = str(value)
    return {k: v.strip() for k, v in found.items() if v.strip()}


def read_track_tags(path: str) -> dict[str, str]:
    """The ReplayGain track tags *path* already has (empty if none or unreadable)."""
    audio = mutagen.File(path)
    if audio is None or audio.tags is None:
        return {}
    return _read(audio.tags)


def _write(path: str, values: dict[str, str]) -> bool:
    """Write *values* into *path* in its own tag format. False if unsupported."""
    audio = mutagen.File(path)
    if audio is None:
        return False
    if audio.tags is None:
        try:
            audio.add_tags()
        except Exception:  # noqa: BLE001 - the format takes no tags
            return False
    tags = audio.tags
    if isinstance(tags, ID3):
        for name, value in values.items():
            for frame in tags.getall("TXXX"):
                if frame.desc.upper() == name:
                    tags.delall(frame.HashKey)
            tags.add(TXXX(encoding=Encoding.LATIN1, desc=name, text=[value]))
        # Keep an ID3v2.3 file at 2.3: some car stereos read nothing newer.
        audio.save(v2_version=3 if tags.version[:2] == (2, 3) else 4)
    elif isinstance(tags, MP4Tags):
        for name, value in values.items():
            for key in [k for k in tags if k.startswith("----:")]:
                if key.rsplit(":", 1)[-1].upper() == name:
                    del tags[key]
            tags[_MP4_FREEFORM + name.lower()] = [MP4FreeForm(value.encode("utf-8"))]
        audio.save()
    elif isinstance(tags, VCommentDict):
        for name, value in values.items():
            tags[name] = [value]
        audio.save()
    elif isinstance(tags, APEv2):
        for name, value in values.items():
            tags[name] = value
        audio.save()
    else:
        return False
    return True


def write_track_tags(path: str, values: dict[str, str]) -> bool:
    """Write *values* into *path*, remembered as our own edit. False if unsupported."""
    path = nfc_path(path)
    with _lock:
        _own_writes[path] = None
    ok = False
    try:
        ok = _write(path, values)
    finally:
        signature = _signature(path) if ok else None
        with _lock:
            if signature is None:
                _own_writes.pop(path, None)
            else:
                _own_writes[path] = signature
    return ok


def is_own_write(path: str) -> bool:
    """True if *path* is as a tag write made here left it (or one is running)."""
    path = nfc_path(path)
    with _lock:
        if path not in _own_writes:
            return False
        expected = _own_writes[path]
    if expected is None:
        return True
    if _signature(path) == expected:
        return True
    with _lock:
        if _own_writes.get(path) == expected:
            del _own_writes[path]
    return False


def tag_source(path: str) -> tuple[os.stat_result, os.stat_result] | None:
    """
    Measure *path* and write its track tags, unless it already has a gain.

    Returns (stat before, stat after) when the file was written, else None.
    A file that cannot be read, measured or tagged is logged and skipped.
    """
    try:
        audio = mutagen.File(path)
    except Exception as e:  # noqa: BLE001 - unreadable means skip
        logger.warning("ReplayGain: cannot read tags of %s: %s", path, e)
        return None
    if audio is None:
        logger.debug("ReplayGain: unknown format, skipping %s", path)
        return None
    if audio.tags is not None and GAIN_TAG in _read(audio.tags):
        return None
    if audio.tags is None:
        try:
            audio.add_tags()
        except Exception:  # noqa: BLE001 - the format takes no tags
            logger.debug("ReplayGain: %s cannot hold tags, skipping", path)
            return None
    if not _writable(audio.tags):
        logger.debug("ReplayGain: tag format of %s is not supported", path)
        return None

    measured = measure(path)
    if measured is None:
        return None
    values = track_values(*measured)
    if values is None:
        logger.info("ReplayGain: %s is silent, no tag written", path)
        return None

    try:
        before = os.stat(path)
        if not write_track_tags(path, values):
            return None
        after = os.stat(path)
    except Exception as e:  # noqa: BLE001 - a failed write is logged, never fatal
        logger.warning("ReplayGain: could not tag %s: %s", path, e)
        return None
    logger.info(
        "♫ ReplayGain %s: gain %s, peak %s", path, values[GAIN_TAG], values[PEAK_TAG]
    )
    return before, after


def is_checked(path: str) -> bool:
    """True if *path* was checked and has not changed since."""
    with _lock:
        recorded = _checked.get(path)
    return recorded is not None and recorded == _signature(path)


def mark_checked(path: str) -> None:
    """Remember that *path* and its outputs carry their tags as of now."""
    signature = _signature(path)
    with _lock:
        if signature is None:
            _checked.pop(path, None)
        else:
            _checked[path] = signature


def forget(path: str) -> None:
    """Check *path* and its outputs again on the next pass."""
    with _lock:
        _checked.pop(path, None)
