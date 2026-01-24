"""FFmpeg encoding logic for audio-transcode-watcher."""

from __future__ import annotations

import logging
import os
import subprocess

from .config import OutputConfig
from .utils import nfc_path

logger = logging.getLogger(__name__)


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
    cmd = [
        "ffmpeg", "-loglevel", "error", "-y",
        "-i", source,
        "-map", "0:a:0",  # First audio stream
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


def atomic_ffmpeg_encode(
    cmd: list[str],
    final_dest: str,
    retry_without_artwork: bool = True,
) -> int:
    """
    Run FFmpeg with atomic output (write to temp, then rename).
    
    Args:
        cmd: FFmpeg command (last element is destination)
        final_dest: Final destination path
        retry_without_artwork: If True, retry without artwork on failure
    
    Returns:
        Return code (0 for success)
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
    
    logger.info("► %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True)
    rc = proc.returncode
    
    if rc == 0:
        try:
            os.replace(tmp_dest, final_dest)
            return 0
        except Exception as e:
            logger.error("Atomic replace failed for %s: %s", final_dest, e)
            _cleanup_temp(tmp_dest)
            return 1
    
    # Handle failure
    logger.error("FFmpeg failed (rc=%s) for %s", rc, final_dest)
    stderr = proc.stderr.decode("utf-8", errors="ignore") if proc.stderr else ""
    _cleanup_temp(tmp_dest)
    
    # Retry without artwork if error seems artwork-related
    artwork_error_hints = ["vf#", "vist#", "VipsJpeg", "png", "mjpeg", "decode"]
    if retry_without_artwork and any(h in stderr.lower() for h in artwork_error_hints):
        logger.warning("Retrying without cover art for %s", final_dest)
        
        filtered_cmd = _remove_artwork_from_command(cmd)
        filtered_cmd[-1] = tmp_dest
        
        logger.info("► (retry) %s", " ".join(filtered_cmd))
        proc2 = subprocess.run(filtered_cmd, capture_output=True)
        rc = proc2.returncode
        
        if rc == 0:
            try:
                os.replace(tmp_dest, final_dest)
                return 0
            except Exception as e:
                logger.error("Atomic replace failed for %s: %s", final_dest, e)
                _cleanup_temp(tmp_dest)
                return 1
        
        logger.error("FFmpeg retry also failed (rc=%s) for %s", rc, final_dest)
        _cleanup_temp(tmp_dest)
    
    return rc


def _cleanup_temp(path: str) -> None:
    """Clean up a temporary file."""
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass
