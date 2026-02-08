"""Automatic lyrics fetching for audio files using syncedlyrics."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import mutagen
import syncedlyrics

from .utils import nfc, nfc_path

logger = logging.getLogger(__name__)


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

    Checks if an .lrc sidecar already exists.  If not, extracts metadata
    and queries syncedlyrics providers (Musixmatch, LRCLIB, NetEase, etc.).

    Args:
        filepath: Path to the audio file.

    Returns:
        Path to the written .lrc file, or None if lyrics were not found
        or already existed.
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

    try:
        lrc_content = syncedlyrics.search(query)
    except Exception:
        logger.warning("syncedlyrics search failed for: %s", query, exc_info=True)
        return None

    if not lrc_content:
        logger.info("No lyrics found for: %s - %s", artist, title)
        return None

    try:
        with open(lrc_path, "w", encoding="utf-8") as f:
            f.write(lrc_content)
        logger.info("♫ lyrics saved: %s - %s → %s", artist, title, lrc_path)
        return lrc_path
    except Exception:
        logger.error("Failed to write lyrics file: %s", lrc_path, exc_info=True)
        return None
