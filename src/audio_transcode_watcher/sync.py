"""Synchronization logic for audio-transcode-watcher."""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .config import Config, OutputConfig
from .encoder import atomic_ffmpeg_encode, build_ffmpeg_command
from .utils import (
    appears_empty_dir,
    get_output_filename,
    is_audio_file,
    is_mp3,
    nfc,
    nfc_path,
    wait_for_stable,
)

# Default number of parallel encoding workers (can be overridden in config)
DEFAULT_PARALLEL_WORKERS = 4

logger = logging.getLogger(__name__)

# Global state for tracking in-progress files
_in_progress: set[str] = set()
_in_progress_lock = threading.Lock()

# Safety guard logging throttle
_last_safety_log_ts = 0.0
_SAFETY_LOG_INTERVAL = 10.0


def safety_guard_active(config: Config) -> bool:
    """
    Check if safety guard should prevent operations.
    
    Safety guard activates if source appears empty.
    Empty destinations are allowed if allow_initial_bulk_encode is True.
    """
    global _last_safety_log_ts
    
    src_empty = appears_empty_dir(config.source_path)
    
    # Source being empty is always a problem
    if src_empty:
        now = time.time()
        if now - _last_safety_log_ts >= _SAFETY_LOG_INTERVAL:
            logger.warning("Safety guard active: source directory appears empty")
            _last_safety_log_ts = now
        return True
    
    # Empty destinations are OK if allow_initial_bulk_encode is True
    if config.allow_initial_bulk_encode:
        return False
    
    # Otherwise, check destinations
    dest_empty = {o.name: appears_empty_dir(o.path) for o in config.outputs}
    
    if any(dest_empty.values()):
        now = time.time()
        if now - _last_safety_log_ts >= _SAFETY_LOG_INTERVAL:
            empty_dests = [name for name, empty in dest_empty.items() if empty]
            logger.warning(
                "Safety guard active: empty_outputs=%s (set allow_initial_bulk_encode: true to allow)",
                empty_dests,
            )
            _last_safety_log_ts = now
        return True
    
    return False


def process_source_file(
    source_path: str,
    config: Config,
    force: bool = False,
    check_stable: bool = True,
) -> None:
    """
    Process a source file and create all configured outputs.
    
    Args:
        source_path: Path to source audio file
        config: Configuration
        force: If True, re-encode even if output exists
        check_stable: If True, wait for file to be stable first
    """
    source_path = nfc_path(source_path)
    
    if not is_audio_file(source_path):
        return
    
    if safety_guard_active(config):
        return
    
    # Wait for file to stabilize if needed
    if check_stable and not wait_for_stable(
        source_path,
        min_stable_secs=config.min_stable_seconds,
        timeout=config.stability_timeout,
    ):
        logger.warning("Source not stable or disappeared: %s", source_path)
        return
    
    # Prevent duplicate concurrent processing
    with _in_progress_lock:
        if source_path in _in_progress:
            return
        _in_progress.add(source_path)
    
    try:
        _process_outputs(source_path, config, force)
    finally:
        with _in_progress_lock:
            _in_progress.discard(source_path)


def _process_outputs(source_path: str, config: Config, force: bool) -> None:
    """Process all outputs for a source file."""
    stem = nfc(Path(source_path).stem)
    source_is_mp3 = is_mp3(source_path)
    
    for output in config.outputs:
        if safety_guard_active(config):
            return
        
        # Determine output filename
        if source_is_mp3 and output.codec == "alac":
            # Special case: copy MP3 to ALAC folder unchanged
            out_filename = os.path.basename(source_path)
            out_path = nfc_path(os.path.join(output.path, out_filename))
            
            if force or not os.path.exists(out_path):
                logger.info("► copy %s → %s", source_path, out_path)
                os.makedirs(output.path, exist_ok=True)
                try:
                    shutil.copy2(source_path, out_path)
                except Exception as e:
                    logger.error("Copy failed %s → %s: %s", source_path, out_path, e)
        else:
            # Transcode
            out_filename = get_output_filename(source_path, output.extension)
            out_path = nfc_path(os.path.join(output.path, out_filename))
            
            if force or not os.path.exists(out_path):
                cmd = build_ffmpeg_command(source_path, out_path, output)
                rc = atomic_ffmpeg_encode(cmd, out_path)
                if rc != 0:
                    logger.error(
                        "%s encode failed for %s",
                        output.name.upper(),
                        source_path,
                    )


def delete_outputs(source_path: str, config: Config) -> None:
    """
    Delete all output files corresponding to a source file.
    
    Called when source file is deleted or renamed.
    """
    source_path = nfc_path(source_path)
    
    if safety_guard_active(config):
        return
    
    stem = nfc(Path(source_path).stem)
    source_basename = os.path.basename(source_path)
    
    for output in config.outputs:
        # Try both the transcoded filename and original filename (for MP3 copies)
        filenames_to_check = [
            f"{stem}{output.extension}",
            source_basename,
        ]
        
        for filename in filenames_to_check:
            filepath = nfc_path(os.path.join(output.path, filename))
            if os.path.exists(filepath):
                try:
                    logger.info("✘ remove %s", filepath)
                    os.remove(filepath)
                except Exception as e:
                    logger.error("Failed to remove %s: %s", filepath, e)


def purge_all_outputs(config: Config) -> None:
    """
    Delete all files in output directories.
    
    Used when FORCE_REENCODE is enabled.
    """
    if safety_guard_active(config):
        logger.warning("Force re-encode requested but safety guard is active.")
        return
    
    for output in config.outputs:
        try:
            os.makedirs(output.path, exist_ok=True)
            with os.scandir(output.path) as entries:
                for entry in entries:
                    if entry.is_file():
                        try:
                            logger.info("✘ purge %s", entry.path)
                            os.remove(entry.path)
                        except Exception as e:
                            logger.error("Failed to purge %s: %s", entry.path, e)
        except Exception as e:
            logger.error("Failed to purge folder %s: %s", output.path, e)


def initial_sync(config: Config) -> None:
    """
    Perform initial synchronization of source to all outputs.
    
    - Creates output directories
    - Encodes missing files
    - Removes orphaned files from outputs
    """
    # Create output directories
    for output in config.outputs:
        os.makedirs(output.path, exist_ok=True)
    
    # Purge if force_reencode is enabled
    if config.force_reencode:
        logger.info("FORCE_REENCODE enabled. Purging all outputs.")
        purge_all_outputs(config)
    
    if safety_guard_active(config):
        logger.info("Initial sync skipped due to safety guard.")
        return
    
    logger.info("Initial sync …")
    
    # Collect source files
    try:
        source_files = []
        with os.scandir(config.source_path) as entries:
            for entry in entries:
                if entry.is_file() and is_audio_file(entry.path):
                    source_files.append(entry.path)
    except Exception as e:
        logger.error("Failed to scan source: %s", e)
        return
    
    # Get normalized stems for orphan detection
    source_stems = {nfc(Path(f).stem) for f in source_files}
    
    # Process all source files in parallel (skip stability check - files are on disk)
    workers = getattr(config, 'parallel_workers', DEFAULT_PARALLEL_WORKERS)
    logger.info("Processing %d source files with %d workers…", len(source_files), workers)
    
    def process_one(src_file: str) -> None:
        try:
            process_source_file(src_file, config, force=False, check_stable=False)
        except Exception as e:
            logger.error("Error processing %s: %s", src_file, e)
    
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(process_one, f): f for f in source_files}
        for future in as_completed(futures):
            # Just wait for completion, errors are logged in process_one
            pass
    
    # Remove orphans
    if safety_guard_active(config):
        logger.info("Skipping orphan cleanup due to safety guard.")
        logger.info("Initial sync complete (partial).")
        return
    
    _cleanup_orphans(config, source_stems)
    logger.info("Initial sync complete.")


def _cleanup_orphans(config: Config, source_stems: set[str]) -> None:
    """Remove output files that no longer have a source."""
    for output in config.outputs:
        # Valid extensions for this output
        # ALAC folder may contain both .m4a and .mp3 (copied MP3s)
        if output.codec == "alac":
            valid_extensions = (".m4a", ".mp3")
        else:
            valid_extensions = (output.extension,)
        
        try:
            with os.scandir(output.path) as entries:
                for entry in entries:
                    if not entry.is_file():
                        continue
                    
                    if not entry.name.endswith(valid_extensions):
                        continue
                    
                    stem = nfc(Path(entry.name).stem)
                    if stem not in source_stems:
                        filepath = nfc_path(entry.path)
                        try:
                            logger.info("✘ remove orphan %s", filepath)
                            os.remove(filepath)
                        except Exception as e:
                            logger.error("Failed to remove %s: %s", filepath, e)
        except Exception as e:
            logger.error("Failed to scan %s for orphans: %s", output.path, e)
