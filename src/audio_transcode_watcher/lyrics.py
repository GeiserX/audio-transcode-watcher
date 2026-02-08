"""Automatic lyrics fetching with syncedlyrics and Whisper fallback."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import mutagen
import syncedlyrics

from .utils import nfc, nfc_path

logger = logging.getLogger(__name__)

# Lazy-loaded Whisper model (heavyweight, only load once when needed)
_whisper_model = None
_whisper_load_failed = False


def _get_whisper_model(model_name: str = "base"):
    """Lazy-load and cache the Whisper model."""
    global _whisper_model, _whisper_load_failed
    if _whisper_load_failed:
        return None
    if _whisper_model is not None:
        return _whisper_model
    try:
        import whisper

        logger.info("Loading Whisper model '%s' (first use, may take a moment)...", model_name)
        _whisper_model = whisper.load_model(model_name)
        logger.info("Whisper model '%s' loaded", model_name)
        return _whisper_model
    except Exception:
        logger.warning("Failed to load Whisper model", exc_info=True)
        _whisper_load_failed = True
        return None


def _segments_to_lrc(segments: list[dict]) -> str:
    """Convert Whisper transcript segments to LRC format."""
    lines = []
    for seg in segments:
        start = seg.get("start", 0.0)
        text = seg.get("text", "").strip()
        if not text:
            continue
        mins = int(start // 60)
        secs = start % 60
        lines.append(f"[{mins:02d}:{secs:05.2f}] {text}")
    return "\n".join(lines)


def _transcribe_with_whisper(filepath: str, model_name: str = "base") -> str | None:
    """
    Transcribe audio to synced lyrics using Whisper.

    Args:
        filepath: Path to the audio file.
        model_name: Whisper model size (tiny, base, small, medium, large).

    Returns:
        LRC-formatted string, or None on failure.
    """
    model = _get_whisper_model(model_name)
    if model is None:
        return None

    try:
        logger.info("Transcribing with Whisper: %s", Path(filepath).name)
        result = model.transcribe(filepath, verbose=False)
        segments = result.get("segments", [])
        if not segments:
            logger.info("Whisper produced no segments for: %s", Path(filepath).name)
            return None
        lrc = _segments_to_lrc(segments)
        logger.info(
            "Whisper transcribed %d segments for: %s", len(segments), Path(filepath).name
        )
        return lrc
    except Exception:
        logger.warning("Whisper transcription failed for: %s", filepath, exc_info=True)
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


def fetch_lyrics_for_file(
    filepath: str,
    whisper_fallback: bool = True,
    whisper_model: str = "base",
) -> str | None:
    """
    Fetch synced lyrics (.lrc) for an audio file if not already present.

    Strategy:
      1. Check if .lrc sidecar already exists -> skip
      2. Try syncedlyrics providers (Musixmatch, LRCLIB, NetEase)
      3. If nothing found and whisper_fallback enabled, transcribe locally

    Args:
        filepath: Path to the audio file.
        whisper_fallback: Use Whisper local transcription as fallback.
        whisper_model: Whisper model size (tiny, base, small, medium, large).

    Returns:
        Path to the written .lrc file, or None if lyrics were not found
        or already existed.
    """
    filepath = nfc_path(filepath)
    lrc_path = nfc_path(str(Path(filepath).with_suffix(".lrc")))

    # Already has lyrics
    if os.path.isfile(lrc_path):
        return None

    lrc_content: str | None = None

    # Step 1: Try syncedlyrics
    meta = extract_metadata(filepath)
    if meta is not None:
        artist, title = meta
        query = f"{artist} {title}"
        try:
            lrc_content = syncedlyrics.search(query)
        except Exception:
            logger.warning("syncedlyrics search failed for: %s", query, exc_info=True)

        if lrc_content:
            return _write_lrc(lrc_path, lrc_content, f"{artist} - {title}", "syncedlyrics")

        logger.info("No lyrics found via syncedlyrics for: %s - %s", artist, title)
    else:
        logger.debug("Cannot extract metadata for lyrics: %s", filepath)

    # Step 2: Whisper fallback
    if whisper_fallback:
        lrc_content = _transcribe_with_whisper(filepath, whisper_model)
        if lrc_content:
            label = f"{meta[0]} - {meta[1]}" if meta else Path(filepath).stem
            return _write_lrc(lrc_path, lrc_content, label, "whisper")

    return None


def _write_lrc(lrc_path: str, content: str, label: str, source: str) -> str | None:
    """Write LRC content to disk."""
    try:
        with open(lrc_path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info("♫ lyrics saved (%s): %s → %s", source, label, lrc_path)
        return lrc_path
    except Exception:
        logger.error("Failed to write lyrics file: %s", lrc_path, exc_info=True)
        return None
