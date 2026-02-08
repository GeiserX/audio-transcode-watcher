"""Utility functions for audio-transcode-watcher."""

from __future__ import annotations

import os
import time
import unicodedata
from pathlib import Path

# Audio file extensions considered as source files
LOSSLESS_EXTENSIONS = {
    ".flac", ".alac", ".wav", ".ape", ".aiff",
    ".wv", ".tta", ".ogg", ".opus"
}

LOSSY_EXTENSIONS = {".mp3", ".aac", ".m4a"}

AUDIO_EXTENSIONS = LOSSLESS_EXTENSIONS | LOSSY_EXTENSIONS | {".mp3"}

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
        return True


def wait_for_stable(
    path: str,
    min_stable_secs: float = 1.0,
    timeout: float = 60.0
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
