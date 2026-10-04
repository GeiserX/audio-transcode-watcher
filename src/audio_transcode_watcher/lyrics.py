"""Automatic lyrics fetching with syncedlyrics."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import mutagen
import syncedlyrics

from .utils import nfc, nfc_path

logger = logging.getLogger(__name__)

# A synced line: one or more [mm:ss.xx] stamps, then the text.
_TIMED_LINE = re.compile(r"^\s*(?:\[\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?\])+(.*)$")
# A URL or a bare domain (``site.co``, ``lyrics.example.xyz``).
_URL = re.compile(r"(https?://|www\.)\S+|\b[\w-]+(\.[\w-]+)*\.[a-z]{2,24}\b", re.IGNORECASE)
_TOKEN = re.compile(r"\w+|[^\w\s]+")

# Fewer timed lines than this is not a usable synced lyric.
MIN_TIMED_LINES = 4


def lyrics_reject_reason(content: str) -> str | None:
    """
    Return why a fetched lyric should not be saved, or None if it is usable.

    Rejects plain or near-empty results (fewer than MIN_TIMED_LINES timed
    lines with text), results made of a single repeated token (``"♪ ♪ ♪"``),
    and results whose only text is an advertisement line carrying a URL.
    """
    texts = []
    for line in content.splitlines():
        m = _TIMED_LINE.match(line)
        if m and m.group(1).strip():
            texts.append(m.group(1).strip())

    if texts and all(_URL.search(t) for t in texts):
        return "only an advertisement line"

    tokens = {t.lower() for text in texts for t in _TOKEN.findall(text)}
    if len(tokens) == 1:
        return f"a single repeated token ({next(iter(tokens))!r})"

    if len(texts) < MIN_TIMED_LINES:
        return f"{len(texts)} timed lines (need {MIN_TIMED_LINES})"

    return None


def extract_metadata(filepath: str) -> tuple[str, str] | None:
    """
    Extract artist and title from an audio file.

    Tries embedded metadata first (mutagen), then falls back to parsing
    the filename as "Artist - Title.ext".

    Returns:
        Tuple of (artist, title) or None if not extractable.
    """
    # Try embedded metadata via mutagen
    try:
        audio = mutagen.File(filepath, easy=True)
        if audio and audio.tags:
            artists = audio.tags.get("artist", [])
            titles = audio.tags.get("title", [])
            if artists and titles:
                artist = artists[0].strip()
                title = titles[0].strip()
                if artist and title:
                    return artist, title
    except Exception:
        logger.debug("Could not read metadata from %s", filepath)

    # Fallback: parse filename "Artist - Title.ext"
    stem = Path(filepath).stem
    # Strip leading track numbers like "01 - ", "01. ", "1 "
    stem = re.sub(r"^\d+[\s.\-]+\s*", "", stem).strip()
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        artist = artist.strip()
        title = title.strip()
        if artist and title:
            return artist, title

    return None


def fetch_lyrics_for_file(filepath: str) -> str | None:
    """
    Fetch synced lyrics (.lrc) for an audio file if not already present.

    Strategy:
      1. Check if .lrc sidecar already exists -> skip
      2. Try syncedlyrics providers (Musixmatch, LRCLIB, NetEase)
      3. Write the result only if it passes lyrics_reject_reason(); when
         nothing usable is found, write nothing

    Returns:
        Path to the written .lrc file, or None if lyrics were not found,
        were rejected, or already existed.
    """
    filepath = nfc_path(filepath)
    lrc_path = nfc_path(str(Path(filepath).with_suffix(".lrc")))

    # Already has lyrics
    if os.path.isfile(lrc_path):
        return None

    meta = extract_metadata(filepath)
    if meta is None:
        logger.debug("Cannot extract metadata for lyrics: %s", filepath)
        return None

    artist, title = meta
    query = f"{artist} {title}"
    lrc_content: str | None = None
    try:
        lrc_content = syncedlyrics.search(query)
    except Exception:
        logger.warning("syncedlyrics search failed for: %s", query, exc_info=True)

    if not lrc_content:
        logger.info("No lyrics found via syncedlyrics for: %s - %s", artist, title)
        return None

    reason = lyrics_reject_reason(lrc_content)
    if reason:
        logger.info("Rejected lyrics for %s - %s: %s", artist, title, reason)
        return None

    return _write_lrc(
        lrc_path, lrc_content, f"{artist} - {title}", "syncedlyrics", owner_of=filepath
    )


def _write_lrc(
    lrc_path: str,
    content: str,
    label: str,
    source: str,
    owner_of: str | None = None,
) -> str | None:
    """Write LRC content to disk, owned like *owner_of* with mode 0664."""
    try:
        with open(lrc_path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        logger.error("Failed to write lyrics file: %s", lrc_path, exc_info=True)
        return None

    if owner_of:
        _match_owner(lrc_path, owner_of)
    logger.info("♫ lyrics saved (%s): %s → %s", source, label, lrc_path)
    return lrc_path


def _match_owner(path: str, reference: str) -> None:
    """Give *path* the uid:gid of *reference* and mode 0664 (best effort)."""
    try:
        st = os.stat(reference)
        os.chown(path, st.st_uid, st.st_gid)
    except OSError as e:
        logger.debug("Could not chown %s like %s: %s", path, reference, e)
    try:
        os.chmod(path, 0o664)
    except OSError as e:
        logger.debug("Could not chmod %s: %s", path, e)
