"""Synchronization logic for audio-transcode-watcher."""

from __future__ import annotations

import filecmp
import logging
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import manifest
from .config import Config, OutputConfig
from .encoder import (
    DECODE_ERROR_RC,
    atomic_ffmpeg_encode,
    build_ffmpeg_command,
    copy_mp4_tags,
    portable_limit_args,
    tolerant_command,
)
from .lyrics import fetch_lyrics_for_file
from .utils import (
    AUDIO_EXTENSIONS,
    LOSSLESS_EXTENSIONS,
    LOSSY_EXTENSIONS,
    SIDECAR_EXTENSIONS,
    appears_empty_dir,
    get_output_file_path,
    get_output_filename,
    get_rel_stem,
    has_audio_extension,
    is_audio_file,
    is_lossless,
    lossy_source_codec,
    nfc,
    nfc_path,
    remove_empty_dirs,
    wait_for_stable,
    walk_audio_files,
)

# Default number of parallel encoding workers (can be overridden in config)
DEFAULT_PARALLEL_WORKERS = 4

logger = logging.getLogger(__name__)

# Global state for tracking in-progress files
_in_progress: set[str] = set()
_in_progress_lock = threading.Lock()

# Sources whose encode failed, with the mtime they had then. A source in
# here is skipped until its mtime changes (lives for the process lifetime).
_failed_sources: dict[str, float] = {}
_failed_lock = threading.Lock()

# The orphan pass leaves alone anything younger than this (seconds), so a
# file the watcher just encoded is never removed by a sync that started
# before it arrived.
ORPHAN_MIN_AGE = 120.0

# A .tmp__ff file younger than this (seconds) may be an encode in progress.
TEMP_MIN_AGE = 600.0

# Output codecs whose files are MP4 and take the extra MP4 tags.
_MP4_CODECS = {"alac", "aac"}

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

    if _is_known_failure(source_path):
        logger.debug("Skipping %s: it failed before and has not changed", source_path)
        return

    # Prevent duplicate concurrent processing
    with _in_progress_lock:
        if source_path in _in_progress:
            return
        _in_progress.add(source_path)

    try:
        if not _process_outputs(source_path, config, force):
            _remember_failure(source_path)
        # Auto-fetch lyrics if enabled and no .lrc sidecar exists
        if config.fetch_lyrics:
            try:
                fetch_lyrics_for_file(source_path)
            except Exception:
                logger.debug("Lyrics fetch failed for %s", source_path, exc_info=True)
        sync_sidecars(source_path, config)
    finally:
        with _in_progress_lock:
            _in_progress.discard(source_path)
        manifest.flush_all()


def _is_known_failure(source_path: str) -> bool:
    """True if *source_path* failed before and its mtime has not changed since."""
    try:
        mtime = os.path.getmtime(source_path)
    except OSError:
        return False
    with _failed_lock:
        recorded = _failed_sources.get(source_path)
        if recorded is None:
            return False
        if recorded == mtime:
            return True
        del _failed_sources[source_path]
        return False


def _remember_failure(source_path: str) -> None:
    """Remember that *source_path* failed, until its mtime changes."""
    try:
        mtime = os.path.getmtime(source_path)
    except OSError:
        return
    with _failed_lock:
        _failed_sources[source_path] = mtime


def _has_lossless_source(source_path: str, config: Config) -> bool:
    """Check if a lossless source with the same stem exists."""
    stem = Path(source_path).stem
    source_dir = os.path.dirname(source_path)

    for ext in LOSSLESS_EXTENSIONS:
        lossless_path = os.path.join(source_dir, f"{stem}{ext}")
        if os.path.exists(lossless_path) and lossless_path != source_path:
            return True
    return False


def _lossy_siblings(source_path: str) -> list[str]:
    """Lossy sources with the same stem as *source_path*, in its folder."""
    stem = Path(source_path).stem
    source_dir = os.path.dirname(source_path)
    siblings = []
    for ext in sorted(LOSSY_EXTENSIONS):
        other = nfc_path(os.path.join(source_dir, f"{stem}{ext}"))
        if other != source_path and os.path.isfile(other):
            siblings.append(other)
    return siblings


def _remove_lossy_copies(
    source_path: str, siblings: list[str], output: OutputConfig, config: Config
) -> None:
    """
    Remove copies of *siblings* from *output*: the lossless *source_path* wins.

    A copy under a different name (``X.mp3`` beside the ``X.m4a`` encode)
    always goes. A copy under the encode's own name (``X.m4a`` copied before
    ``X.flac`` arrived) goes only while it is still byte-identical to the
    lossy source, so a finished encode is never thrown away.
    """
    own_name, _ = plan_output(source_path, output)
    own_path = get_output_file_path(
        source_path, config.source_path, output.path, own_name
    )
    for sibling in siblings:
        name, action = plan_output(sibling, output)
        if action != "copy":
            continue
        copy_path = get_output_file_path(sibling, config.source_path, output.path, name)
        try:
            if not os.path.exists(copy_path):
                continue
            if copy_path == own_path and not filecmp.cmp(
                sibling, copy_path, shallow=True
            ):
                continue
            logger.info(
                "✘ remove lossy copy %s (lossless %s wins)", copy_path, source_path
            )
            os.remove(copy_path)
        except OSError as e:
            logger.error("Failed to remove lossy copy %s: %s", copy_path, e)


def _has_other_source(source_path: str) -> bool:
    """Check if another audio source with the same stem exists."""
    stem = Path(source_path).stem
    source_dir = os.path.dirname(source_path)
    for ext in AUDIO_EXTENSIONS:
        other = nfc_path(os.path.join(source_dir, f"{stem}{ext}"))
        if other != source_path and os.path.exists(other):
            return True
    return False


def plan_output(source_path: str, output: OutputConfig) -> tuple[str, str]:
    """
    Decide how *source_path* lands in *output*.

    Returns ``(filename, action)`` where action is ``"copy"`` or ``"encode"``:

    - a lossless source is encoded to the output codec;
    - a lossy source is copied unchanged (same extension) into a lossless
      output, since encoding it would only inflate it;
    - a lossy source is copied unchanged into a lossy output of the same
      codec, unless it exceeds the output's channel or sample-rate limit,
      and transcoded into a lossy output of another codec.
    """
    if not is_lossless(source_path):
        if output.is_lossless:
            return nfc(os.path.basename(source_path)), "copy"
        if lossy_source_codec(source_path) == output.codec and not portable_limit_args(
            source_path, output
        ):
            return nfc(os.path.basename(source_path)), "copy"
    return get_output_filename(source_path, output.extension), "encode"


def _atomic_copy(source_path: str, out_path: str) -> bool:
    """Copy a file through a temp name, so a partial copy is never visible."""
    tmp = out_path + ".tmp__ff"
    logger.info("► copy %s → %s", source_path, out_path)
    try:
        shutil.copy2(source_path, tmp)
        os.replace(tmp, out_path)
        return True
    except Exception as e:
        logger.error("Copy failed %s → %s: %s", source_path, out_path, e)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def _made_from_lossy(output: OutputConfig, out_path: str) -> bool:
    """True if the manifest says *out_path* came from a lossy source.

    Unknown (no manifest, no row) is False, which keeps today's behaviour.
    """
    row = manifest.lookup(output.path, out_path)
    if not row or row.get("kind") not in ("copy", "transcode"):
        return False
    # Trust the row only while the file is still the one it describes.
    if not manifest.output_matches(row, out_path):
        logger.debug("Manifest row for %s is stale; leaving the file alone", out_path)
        return False
    return True


def _tolerant_source_changed(
    output: OutputConfig, out_path: str, source_path: str
) -> bool:
    """True if *out_path* is a tolerant copy and its source has since changed.

    A damaged source replaced by a clean copy gets a strict encode again.
    """
    row = manifest.lookup(output.path, out_path)
    if not row or row.get("kind") != "tolerant":
        return False
    try:
        st = os.stat(source_path)
    except OSError:
        return False
    return (st.st_size, st.st_mtime) != (row.get("size"), row.get("mtime"))


def _process_outputs(source_path: str, config: Config, force: bool) -> bool:
    """
    Process all outputs for a source file.

    Returns False if an encode failed, True otherwise.
    """
    if not is_lossless(source_path) and _has_lossless_source(source_path, config):
        logger.debug("Skipping lossy %s - lossless source exists", source_path)
        return True

    siblings = _lossy_siblings(source_path) if is_lossless(source_path) else []

    ok = True
    for output in config.outputs:
        if safety_guard_active(config):
            return ok

        out_filename, action = plan_output(source_path, output)
        out_path = get_output_file_path(
            source_path,
            config.source_path,
            output.path,
            out_filename,
        )
        if siblings:
            _remove_lossy_copies(source_path, siblings, output, config)
        if not force and os.path.exists(out_path):
            if _tolerant_source_changed(output, out_path, source_path):
                logger.info(
                    "Rebuilding %s: it was made from a damaged source that has changed",
                    out_path,
                )
            elif is_lossless(source_path) and _made_from_lossy(output, out_path):
                logger.info(
                    "Replacing %s: it was made from a lossy source, %s wins",
                    out_path,
                    source_path,
                )
            else:
                continue

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        if action == "copy":
            if _atomic_copy(source_path, out_path):
                manifest.record(
                    output.path, out_path, config.source_path, source_path, "copy"
                )
            continue

        finalize = None
        if output.codec in _MP4_CODECS:

            def finalize(tmp: str, src: str = source_path) -> None:
                copy_mp4_tags(src, tmp)

        cmd = build_ffmpeg_command(source_path, out_path, output)
        rc = atomic_ffmpeg_encode(cmd, out_path, finalize=finalize)
        kind = "encode" if is_lossless(source_path) else "transcode"
        if rc != 0:
            logger.error(
                "%s encode failed for %s",
                output.name.upper(),
                source_path,
            )
            # Remembered even if the tolerant run below succeeds, so the
            # strict attempt is not repeated on every scan.
            ok = False
            if (
                rc == DECODE_ERROR_RC
                and config.corrupt_source_for(output) == "encode_anyway"
            ):
                rc = atomic_ffmpeg_encode(
                    tolerant_command(cmd), out_path, finalize=finalize, strict=False
                )
                if rc == 0:
                    kind = "tolerant"
                    logger.warning(
                        "%s copy %s was made from a damaged source %s; "
                        "it may glitch where the source is corrupt",
                        output.name.upper(),
                        out_path,
                        source_path,
                    )
        if rc == 0:
            manifest.record(
                output.path, out_path, config.source_path, source_path, kind
            )
    return ok


def delete_outputs(source_path: str, config: Config) -> None:
    """
    Delete all output files corresponding to a source file.

    Called when source file is deleted or renamed.
    """
    source_path = nfc_path(source_path)

    if safety_guard_active(config):
        return

    lossless_sibling = not is_lossless(source_path) and _has_lossless_source(
        source_path, config
    )

    for output in config.outputs:
        filename, _action = plan_output(source_path, output)
        # With a lossless source of the same stem, a file of the same name
        # in this output belongs to that source; keep it.
        if lossless_sibling and filename == get_output_filename(
            source_path, output.extension
        ):
            continue

        filepath = get_output_file_path(
            source_path,
            config.source_path,
            output.path,
            filename,
        )
        if os.path.exists(filepath):
            try:
                logger.info("✘ remove %s", filepath)
                os.remove(filepath)
            except Exception as e:
                logger.error("Failed to remove %s: %s", filepath, e)

    # Sidecars are shared by every source of the stem; remove them only
    # when no other source of the stem remains.
    if not _has_other_source(source_path):
        delete_sidecars(source_path, config)

    # Clean up empty subdirectories left after deletions
    for output in config.outputs:
        remove_empty_dirs(output.path)


def sync_sidecars(source_path: str, config: Config) -> None:
    """
    Copy sidecar files (e.g. .lrc lyrics) from source to all output directories.

    Copies if the destination is missing or older than the source.
    Directory structure from the source root is preserved.
    """
    source_path = nfc_path(source_path)
    stem = nfc(Path(source_path).stem)
    source_dir = os.path.dirname(source_path)

    for ext in SIDECAR_EXTENSIONS:
        sidecar_src = nfc_path(os.path.join(source_dir, f"{stem}{ext}"))
        if not os.path.isfile(sidecar_src):
            continue

        sidecar_filename = f"{stem}{ext}"
        for output in config.outputs:
            sidecar_dst = get_output_file_path(
                source_path,
                config.source_path,
                output.path,
                sidecar_filename,
            )
            try:
                needs_copy = not os.path.exists(sidecar_dst)
                if not needs_copy:
                    needs_copy = os.path.getmtime(sidecar_src) > os.path.getmtime(
                        sidecar_dst
                    )
                if needs_copy:
                    os.makedirs(os.path.dirname(sidecar_dst), exist_ok=True)
                    shutil.copy2(sidecar_src, sidecar_dst)
                    logger.info("► copy sidecar %s → %s", sidecar_src, sidecar_dst)
            except Exception as e:
                logger.error(
                    "Failed to copy sidecar %s → %s: %s", sidecar_src, sidecar_dst, e
                )


def delete_sidecars(source_path: str, config: Config) -> None:
    """Delete sidecar files from all output directories for a given source."""
    source_path = nfc_path(source_path)
    stem = nfc(Path(source_path).stem)

    for ext in SIDECAR_EXTENSIONS:
        sidecar_filename = f"{stem}{ext}"
        for output in config.outputs:
            sidecar_path = get_output_file_path(
                source_path,
                config.source_path,
                output.path,
                sidecar_filename,
            )
            if os.path.exists(sidecar_path):
                try:
                    logger.info("✘ remove sidecar %s", sidecar_path)
                    os.remove(sidecar_path)
                except Exception as e:
                    logger.error("Failed to remove sidecar %s: %s", sidecar_path, e)


def cleanup_stale_temp_files(config: Config) -> int:
    """
    Remove stale temporary files from interrupted encodes.

    These files have the .tmp__ff suffix and are left behind when
    ffmpeg is interrupted (e.g., container restart, crash). Files younger
    than TEMP_MIN_AGE are skipped: they may be an encode still running.

    Returns the number of files cleaned up.
    """
    cleaned = 0
    now = time.time()
    for output in config.outputs:
        try:
            if not os.path.exists(output.path):
                continue
            for dirpath, _dirnames, filenames in os.walk(output.path):
                for fname in filenames:
                    if fname.endswith(".tmp__ff"):
                        full = os.path.join(dirpath, fname)
                        try:
                            if now - os.path.getmtime(full) < TEMP_MIN_AGE:
                                logger.debug("Keeping young temp file %s", full)
                                continue
                            logger.info("✘ cleanup stale temp %s", full)
                            os.remove(full)
                            cleaned += 1
                        except Exception as e:
                            logger.error("Failed to remove temp file %s: %s", full, e)
        except Exception as e:
            logger.error("Failed to scan %s for temp files: %s", output.path, e)
    return cleaned


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
            for dirpath, _dirnames, filenames in os.walk(output.path):
                for fname in filenames:
                    full = os.path.join(dirpath, fname)
                    try:
                        logger.info("✘ purge %s", full)
                        os.remove(full)
                    except Exception as e:
                        logger.error("Failed to purge %s: %s", full, e)
            manifest.forget(output.path)
            remove_empty_dirs(output.path)
        except Exception as e:
            logger.error("Failed to purge folder %s: %s", output.path, e)


def initial_sync(config: Config, periodic: bool = False) -> None:
    """
    Synchronize the source to all outputs (at startup and periodically).

    - Cleans up stale temp files from interrupted encodes
    - Creates output directories
    - Encodes missing files (recursively)
    - Removes orphaned files from outputs
    """
    label = "Periodic sync" if periodic else "Initial sync"

    # Create output directories
    for output in config.outputs:
        os.makedirs(output.path, exist_ok=True)

    # Clean up stale temp files from previous interrupted runs
    cleaned = cleanup_stale_temp_files(config)
    if cleaned > 0:
        logger.info("Cleaned up %d stale temp files from interrupted encodes.", cleaned)

    # Purge if force_reencode is enabled (startup only)
    if config.force_reencode and not periodic:
        logger.info("FORCE_REENCODE enabled. Purging all outputs.")
        purge_all_outputs(config)

    if safety_guard_active(config):
        logger.info("%s skipped due to safety guard.", label)
        return

    logger.info("%s …", label)

    # Collect source files recursively
    try:
        source_files = walk_audio_files(config.source_path)
    except Exception as e:
        logger.error("Failed to scan source: %s", e)
        return

    # Process all source files in parallel (skip stability check - files are on disk)
    workers = getattr(config, "parallel_workers", DEFAULT_PARALLEL_WORKERS)
    logger.info(
        "Processing %d source files with %d workers…", len(source_files), workers
    )

    def process_one(src_file: str) -> None:
        try:
            process_source_file(src_file, config, force=False, check_stable=False)
        except Exception as e:
            logger.error("Error processing %s: %s", src_file, e)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(process_one, f) for f in source_files]
        for _ in as_completed(futures):
            pass

    # Remove orphans
    if safety_guard_active(config):
        logger.info("Skipping orphan cleanup due to safety guard.")
        logger.info("%s complete (partial).", label)
        return

    if not source_files:
        logger.warning(
            "No source files found; skipping orphan cleanup to avoid wiping outputs."
        )
    else:
        _cleanup_orphans(config)
    manifest.flush_all(force=True)
    logger.info("%s complete.", label)


def _age_seconds(path: str, now: float) -> float:
    """Seconds since *path* was last written or created (mtime or ctime)."""
    st = os.stat(path)
    return now - max(st.st_mtime, st.st_ctime)


def _cleanup_orphans(config: Config) -> None:
    """Remove output files that no longer have a source.

    The source is walked again here, not reused from the start of the sync,
    so a file that arrived during a long sync is known. Anything younger
    than ORPHAN_MIN_AGE, or whose stem is being processed, is left alone.

    An output may hold several files for one stem (an ALAC output holds
    ``.m4a`` encodes and unchanged lossy copies such as ``.mp3``), so the
    expected files are computed per source with plan_output().
    """
    src_root = config.source_path
    try:
        source_files = walk_audio_files(src_root)
    except Exception as e:
        logger.error("Failed to scan source for orphan cleanup: %s", e)
        return
    if not source_files:
        logger.warning(
            "No source files found; skipping orphan cleanup to avoid wiping outputs."
        )
        return

    now = time.time()
    source_stems: set[str] = set()
    lossless_stems: set[str] = set()
    young_stems: set[str] = set()
    for f in source_files:
        stem = get_rel_stem(f, src_root)
        source_stems.add(stem)
        if is_lossless(f):
            lossless_stems.add(stem)
        try:
            if _age_seconds(f, now) < ORPHAN_MIN_AGE:
                young_stems.add(stem)
        except OSError:
            young_stems.add(stem)
    with _in_progress_lock:
        in_progress = list(_in_progress)
    young_stems.update(get_rel_stem(p, src_root) for p in in_progress)

    def keep_young(full: str, rel_stem: str) -> bool:
        if rel_stem in young_stems:
            return True
        try:
            return _age_seconds(full, now) < ORPHAN_MIN_AGE
        except OSError:
            return True

    def remove(full: str, kind: str) -> None:
        try:
            logger.info("✘ remove %s %s", kind, full)
            os.remove(full)
        except Exception as e:
            logger.error("Failed to remove %s: %s", full, e)

    for output in config.outputs:
        expected: set[str] = set()
        for f in source_files:
            if not is_lossless(f) and get_rel_stem(f, src_root) in lossless_stems:
                continue  # the lossless source of this stem wins
            filename, _action = plan_output(f, output)
            expected.add(get_output_file_path(f, src_root, output.path, filename))

        try:
            for dirpath, _dirnames, filenames in os.walk(output.path):
                for fname in filenames:
                    full = os.path.join(dirpath, fname)
                    if has_audio_extension(full):
                        if nfc_path(full) in expected:
                            continue
                        kind = "orphan"
                    elif fname.lower().endswith(tuple(SIDECAR_EXTENSIONS)):
                        if get_rel_stem(full, output.path) in source_stems:
                            continue
                        kind = "orphan sidecar"
                    else:
                        continue

                    if keep_young(full, get_rel_stem(full, output.path)):
                        logger.debug("Keeping young or in-progress %s", full)
                        continue
                    remove(full, kind)
        except Exception as e:
            logger.error("Failed to scan %s for orphans: %s", output.path, e)

    # Remove empty subdirectories left after orphan cleanup, and forget
    # manifest rows whose output is gone
    for output in config.outputs:
        remove_empty_dirs(output.path)
        manifest.prune(output.path)
        manifest.flush(output.path, force=True)
