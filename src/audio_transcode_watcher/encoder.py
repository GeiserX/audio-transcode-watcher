"""FFmpeg encoding logic for audio-transcode-watcher."""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable

import mutagen
from mutagen.apev2 import APETextValue
from mutagen.mp4 import MP4, MP4FreeForm

from .config import OutputConfig
from .utils import nfc_path

logger = logging.getLogger(__name__)

# Exit code returned when ffmpeg exits 0 but reported a decode error.
DECODE_ERROR_RC = 69

# A single encode that runs longer than this (seconds) is killed and counts
# as a failure, so a hung ffmpeg cannot stall the sync for good.
FFMPEG_TIMEOUT = 1800
TIMEOUT_RC = 124

# stderr text that means the audio did not decode cleanly, even when
# ffmpeg exits 0 (a corrupt FLAC used to come out seconds short).
_DECODE_ERROR_HINTS = ("decode_frame() failed", "invalid", "error while decoding")

# stderr text that points at the attached picture (cover art) stream.
# Only these trigger the retry without artwork.
_ARTWORK_ERROR_HINTS = (
    "could not find tag for codec",
    "attached pic",
    "video stream",
    "mjpeg",
    "png",
    "vist#",
    "vf#",
)

# Tags ffmpeg drops on an ALAC or AAC encode, copied from the source after
# it: source tag name -> MP4 atom. The atom names are the ones MusicBrainz
# Picard writes, so Picard, TagLib (Navidrome, Jellyfin), foobar2000 and
# iTunes all recognise them. Sort names go to the standard sort atoms.
_FREEFORM = "----:com.apple.iTunes:"
MP4_TAG_ATOMS = {
    "REPLAYGAIN_TRACK_GAIN": _FREEFORM + "replaygain_track_gain",
    "REPLAYGAIN_TRACK_PEAK": _FREEFORM + "replaygain_track_peak",
    "REPLAYGAIN_ALBUM_GAIN": _FREEFORM + "replaygain_album_gain",
    "REPLAYGAIN_ALBUM_PEAK": _FREEFORM + "replaygain_album_peak",
    "MUSICBRAINZ_TRACKID": _FREEFORM + "MusicBrainz Track Id",
    "MUSICBRAINZ_ALBUMID": _FREEFORM + "MusicBrainz Album Id",
    "MUSICBRAINZ_ARTISTID": _FREEFORM + "MusicBrainz Artist Id",
    "MUSICBRAINZ_ALBUMARTISTID": _FREEFORM + "MusicBrainz Album Artist Id",
    "MUSICBRAINZ_RELEASEGROUPID": _FREEFORM + "MusicBrainz Release Group Id",
    "ISRC": _FREEFORM + "ISRC",
    "LABEL": _FREEFORM + "LABEL",
    "CATALOGNUMBER": _FREEFORM + "CATALOGNUMBER",
    "ARTISTSORT": "soar",
    "ALBUMARTISTSORT": "soaa",
    "ALBUMSORT": "soal",
}

# How the same tags are stored in ID3 (WAV, AIFF and MP3 sources).
_ID3_FRAMES = {
    "ISRC": "TSRC",
    "LABEL": "TPUB",
    "ARTISTSORT": "TSOP",
    "ALBUMARTISTSORT": "TSO2",
    "ALBUMSORT": "TSOA",
}
_ID3_TXXX = {
    "MUSICBRAINZ_ALBUMID": "MusicBrainz Album Id",
    "MUSICBRAINZ_ARTISTID": "MusicBrainz Artist Id",
    "MUSICBRAINZ_ALBUMARTISTID": "MusicBrainz Album Artist Id",
    "MUSICBRAINZ_RELEASEGROUPID": "MusicBrainz Release Group Id",
}


def build_ffmpeg_command(
    source: str,
    dest: str,
    output_config: OutputConfig,
) -> list[str]:
    """
    Build an FFmpeg command for transcoding.

    Args:
        source: Path to source audio file
        dest: Path to destination file
        output_config: Output configuration

    Returns:
        FFmpeg command as list of arguments
    """
    source = nfc_path(source)
    dest = nfc_path(dest)

    # Common arguments
    # -err_detect crccheck+explode makes the decoder verify each frame's
    # checksum and stop on a mismatch. Without it ffmpeg 7.1 (the image's)
    # decodes a FLAC with one flipped bit silently.
    cmd = [
        "ffmpeg",
        "-loglevel",
        "error",
        "-xerror",
        "-y",
        "-err_detect",
        "crccheck+explode",
        "-i",
        source,
        "-map",
        "0:a:0",  # First audio stream
    ]

    # Add video/artwork mapping if enabled
    if output_config.include_artwork:
        cmd.extend(["-map", "0:v:0?"])  # First video/image stream (optional)

    # Copy metadata
    cmd.extend(["-map_metadata", "0"])

    # Codec-specific options
    codec = output_config.codec

    if codec == "alac":
        cmd.extend(["-c:a", "alac"])
        if output_config.include_artwork:
            cmd.extend(["-c:v", "copy"])
        # Ensure album_artist is mapped correctly for M4A (aART tag)
        cmd.extend(["-movflags", "+faststart", "-f", "mp4"])

    elif codec == "aac":
        cmd.extend(["-c:a", "aac", "-b:a", output_config.bitrate])
        if output_config.include_artwork:
            cmd.extend(["-c:v", "copy"])
        cmd.extend(["-movflags", "+faststart", "-f", "mp4"])

    elif codec == "mp3":
        cmd.extend(["-c:a", "libmp3lame", "-b:a", output_config.bitrate])
        if output_config.include_artwork:
            # MP3 needs mjpeg for ID3 APIC artwork
            cmd.extend(["-c:v", "mjpeg"])
        cmd.extend(["-id3v2_version", "3", "-write_id3v2", "1", "-f", "mp3"])

    elif codec == "opus":
        cmd.extend(["-c:a", "libopus", "-b:a", output_config.bitrate])
        cmd.extend(["-f", "opus"])

    elif codec == "flac":
        cmd.extend(["-c:a", "flac"])
        if output_config.include_artwork:
            cmd.extend(["-c:v", "copy"])
        cmd.extend(["-f", "flac"])

    elif codec == "wav":
        cmd.extend(["-c:a", "pcm_s16le", "-f", "wav"])

    else:
        raise ValueError(f"Unsupported codec: {codec}")

    cmd.append(dest)
    return cmd


def _remove_artwork_from_command(cmd: list[str]) -> list[str]:
    """
    Remove artwork-related options from an FFmpeg command.

    Used for retry when artwork encoding fails.
    """
    filtered = []
    i = 0

    while i < len(cmd):
        arg = cmd[i]

        # Skip -map 0:v:0? pair
        if arg == "-map" and i + 1 < len(cmd) and cmd[i + 1] == "0:v:0?":
            i += 2
            continue

        # Skip -c:v and its value
        if arg == "-c:v":
            i += 2
            continue

        # Skip -vf and its value
        if arg.startswith("-vf"):
            i += 2
            continue

        filtered.append(arg)
        i += 1

    return filtered


def _source_from_cmd(cmd: list[str]) -> str:
    """Return the input file of an ffmpeg command, for log lines."""
    try:
        return cmd[cmd.index("-i") + 1]
    except ValueError, IndexError:
        return "?"


def _decode_error(stderr: str) -> str | None:
    """Return the first stderr line that reports a decode error, if any."""
    for line in stderr.splitlines():
        low = line.lower()
        if any(h in low for h in _DECODE_ERROR_HINTS):
            return line.strip()
    return None


def _is_artwork_error(stderr: str) -> bool:
    """True when stderr points at the attached picture stream."""
    low = stderr.lower()
    return any(h in low for h in _ARTWORK_ERROR_HINTS)


def _run_ffmpeg(cmd: list[str]) -> tuple[int, str]:
    """Run ffmpeg; a decode error on stderr counts as a failure even with rc 0."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=FFMPEG_TIMEOUT, check=False
        )
    except subprocess.TimeoutExpired:
        return TIMEOUT_RC, f"ffmpeg timed out after {FFMPEG_TIMEOUT} s"
    stderr = proc.stderr.decode("utf-8", errors="ignore") if proc.stderr else ""
    rc = proc.returncode
    if rc == 0 and _decode_error(stderr):
        rc = DECODE_ERROR_RC
    return rc, stderr


def atomic_ffmpeg_encode(
    cmd: list[str],
    final_dest: str,
    retry_without_artwork: bool = True,
    finalize: Callable[[str], None] | None = None,
) -> int:
    """
    Run FFmpeg with atomic output (write to temp, then rename).

    Args:
        cmd: FFmpeg command (last element is destination)
        final_dest: Final destination path
        retry_without_artwork: If True, retry without artwork when the
            failure points at the attached picture stream
        finalize: Optional callable run on the finished temp file before
            the rename (used to copy extra tags)

    Returns:
        Return code (0 for success). No output is written on failure.
    """
    final_dest = nfc_path(final_dest)
    dest_dir = os.path.dirname(final_dest)
    os.makedirs(dest_dir, exist_ok=True)

    tmp_dest = final_dest + ".tmp__ff"

    # Clean up any stale temp file
    try:
        if os.path.exists(tmp_dest):
            os.remove(tmp_dest)
    except Exception:
        pass

    # Replace destination with temp path
    cmd = list(cmd)
    cmd[-1] = tmp_dest
    source = _source_from_cmd(cmd)

    logger.info("► %s", " ".join(cmd))
    rc, stderr = _run_ffmpeg(cmd)

    if rc != 0 and retry_without_artwork and _is_artwork_error(stderr):
        _cleanup_temp(tmp_dest)
        logger.warning("Retrying without cover art for %s", final_dest)
        cmd = _remove_artwork_from_command(cmd)
        cmd[-1] = tmp_dest
        logger.info("► (retry) %s", " ".join(cmd))
        rc, stderr = _run_ffmpeg(cmd)

    if rc != 0:
        detail = _decode_error(stderr) or (stderr.strip().splitlines() or [""])[-1]
        logger.error(
            "FFmpeg failed (rc=%s) for %s → %s: %s", rc, source, final_dest, detail
        )
        _cleanup_temp(tmp_dest)
        return rc

    if finalize is not None:
        try:
            finalize(tmp_dest)
        except Exception as e:
            logger.warning("Post-encode step failed for %s: %s", final_dest, e)

    try:
        os.replace(tmp_dest, final_dest)
        return 0
    except Exception as e:
        logger.error("Atomic replace failed for %s: %s", final_dest, e)
        _cleanup_temp(tmp_dest)
        return 1


def _source_tag_values(source: str) -> dict[str, list[str]]:
    """
    Read the MP4_TAG_ATOMS source tags from *source*, whatever its tag format.

    Vorbis comments (FLAC, Ogg, Opus) and APEv2 (APE, WavPack, TAK) store
    them under the same names; ID3 (WAV, AIFF, MP3) uses TXXX and a few
    standard frames.
    """
    audio = mutagen.File(source)
    if audio is None or audio.tags is None:
        return {}
    tags = audio.tags

    # Name -> values, with keys upper-cased for case-insensitive lookup.
    plain: dict[str, list[str]] = {}
    is_id3 = hasattr(tags, "getall")
    if is_id3:
        for frame in tags.getall("TXXX"):
            plain[frame.desc.upper()] = [str(v) for v in frame.text]
        for name, frame_id in _ID3_FRAMES.items():
            frame = tags.get(frame_id)
            if frame is not None:
                plain[name] = [str(v) for v in frame.text]
        ufid = tags.get("UFID:http://musicbrainz.org")
        if ufid is not None:
            plain["MUSICBRAINZ_TRACKID"] = [ufid.data.decode("ascii", "ignore")]
        for name, desc in _ID3_TXXX.items():
            if desc.upper() in plain:
                plain[name] = plain[desc.upper()]
    else:
        for key, value in tags.items():
            if isinstance(value, list):
                values = value
            elif isinstance(value, APETextValue):
                values = list(value)
            else:
                values = [value]
            plain[str(key).upper()] = [str(v) for v in values]

    out: dict[str, list[str]] = {}
    for name in MP4_TAG_ATOMS:
        values = [v for v in plain.get(name, []) if v.strip()]
        if values:
            out[name] = values
    return out


def copy_mp4_tags(source: str, dest: str) -> int:
    """
    Copy the MP4_TAG_ATOMS tags from *source* into the MP4 file *dest*.

    Freeform atoms hold UTF-8 bytes; the sort atoms hold text.
    Returns the number of tags written.
    """
    values = _source_tag_values(source)
    if not values:
        return 0
    mp4 = MP4(dest)
    if mp4.tags is None:
        mp4.add_tags()
    for name, vals in values.items():
        atom = MP4_TAG_ATOMS[name]
        if atom.startswith(_FREEFORM):
            mp4.tags[atom] = [MP4FreeForm(v.encode("utf-8")) for v in vals]
        else:
            mp4.tags[atom] = vals
    mp4.save()
    return len(values)


def _cleanup_temp(path: str) -> None:
    """Clean up a temporary file."""
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass
