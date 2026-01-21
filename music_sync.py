#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Flat-folder music mirror (robust version)

- MP3 source:     copy -> ALAC folder (.mp3)
                  encode 256 kb/s MP3 + AAC

- Loss-less src:  encode ALAC (.m4a) + MP3-256 + AAC-256

Additions:
- Safety guard: if the source folder OR any destination folder appears empty,
  do nothing (skip deletes and encodes) to avoid accidental data loss.
- Unicode-safe filenames: normalize to NFC to keep exact visible characters
  (e.g., á, é, í, ó, ú, ñ, ü). Attributes are preserved on direct copies.
- FORCE_REENCODE environment variable:
    - If truthy, delete all existing mirrors and re-encode everything from source.
    - Still respects the safety guard (won’t delete if source appears empty).
- File-change handling:
    - On rename or any modification (including attribute-only changes),
      delete old encoded files and force re-encode.
- More robustness:
    - Wait until files are stable before processing.
    - Atomic output writes via temporary files to avoid partial files on failure.
    - Better error logging.
"""

import os
import sys
import time
import shutil
import logging
import subprocess
import threading
import unicodedata
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ── configuration (override through env-vars) ───────────────────────
SRC_DIR   = os.getenv("SRC_DIR",   "/music/flac")    # master (flat folder)
DEST_ALAC = os.getenv("DEST_ALAC", "/music/alac")    # “ALAC” mirror
DEST_MP3  = os.getenv("DEST_MP3",  "/music/mp3256")  # MP3-256
DEST_AAC  = os.getenv("DEST_AAC",  "/music/aac256")  # AAC-256

FORCE_REENCODE = os.getenv("FORCE_REENCODE", "false")

# Maintain original behavior regarding which extensions are considered
LOSSLESS_EXT = {
    ".flac", ".alac", ".wav", ".ape", ".aiff",
    ".wv", ".tta", ".ogg", ".opus"
}

# ── globals / synchronization ───────────────────────────────────────
IN_PROGRESS = set()
IN_PROGRESS_LOCK = threading.Lock()

# To avoid log spam from safety guard
_last_safety_log_ts = 0
SAFETY_LOG_INTERVAL = 10.0  # seconds

# ── helpers: environment and unicode handling ───────────────────────
def truthy_env(val: str) -> bool:
    return str(val).strip().lower() in {"1", "true", "yes", "y", "on"}

def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)

def nfc_path(p: str) -> str:
    # Normalize every path component to NFC
    parts = list(Path(p).parts)
    if not parts:
        return p
    if parts[0] == os.sep:
        # Absolute path handling on POSIX
        normalized = os.sep + os.path.join(*[nfc(x) for x in parts[1:]])
    else:
        normalized = os.path.join(*[nfc(x) for x in parts])
    return normalized

def is_audio(p: str) -> bool:
    pn = nfc_path(p)
    return os.path.isfile(pn) and Path(pn).suffix.lower() in LOSSLESS_EXT.union({".mp3"})

def is_mp3(p: str) -> bool:
    return Path(p).suffix.lower() == ".mp3"

def lossless(p: str) -> bool:
    return is_audio(p) and not is_mp3(p)

def appears_empty_dir(d: str) -> bool:
    try:
        if not os.path.isdir(d):
            return True
        # Consider only non-hidden files to avoid false positives from dotfiles
        with os.scandir(d) as it:
            for entry in it:
                if not entry.name.startswith("."):
                    return False
        return True
    except Exception:
        # On any error accessing directory, treat as empty to be safe
        return True

def safety_guard_active() -> bool:
    # If source OR any destination appears empty, engage safety.
    src_empty = appears_empty_dir(SRC_DIR)
    dest_alac_empty = appears_empty_dir(DEST_ALAC)
    dest_mp3_empty  = appears_empty_dir(DEST_MP3)
    dest_aac_empty  = appears_empty_dir(DEST_AAC)
    active = src_empty or dest_alac_empty or dest_mp3_empty or dest_aac_empty

    global _last_safety_log_ts
    now = time.time()
    if active and (now - _last_safety_log_ts >= SAFETY_LOG_INTERVAL):
        logging.warning(
            "Safety guard active: SRC or one of DESTs appears empty. "
            "Skipping actions to avoid accidental deletions/rewrites. "
            "(SRC empty=%s, ALAC empty=%s, MP3 empty=%s, AAC empty=%s)",
            src_empty, dest_alac_empty, dest_mp3_empty, dest_aac_empty
        )
        _last_safety_log_ts = now
    return active

def wait_for_stable(path: str, min_stable_secs: float = 1.0, timeout: float = 60.0) -> bool:
    """
    Wait until file size stays unchanged for min_stable_secs
    or until timeout. Returns True if stable, False if timed out/nonexistent.
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
        else:
            if time.time() - last_change >= min_stable_secs:
                return True
        time.sleep(0.2)
    return False

def atomic_ffmpeg_run(cmd: list[str], final_dest: str, retry_without_video: bool = True) -> int:
    """
    Run ffmpeg to a temp file alongside the destination, then atomically replace.
    Ensures we never leave partial/corrupted outputs on failure.
    
    If retry_without_video is True and the command fails, retry without cover art
    mapping (removes -map 0:v:0? and video codec options).
    """
    final_dest = nfc_path(final_dest)
    dest_dir = os.path.dirname(final_dest)
    os.makedirs(dest_dir, exist_ok=True)

    tmp_dest = final_dest + ".tmp__ff"
    # Ensure no stale tmp
    try:
        if os.path.exists(tmp_dest):
            os.remove(tmp_dest)
    except Exception:
        pass

    # Replace the destination in cmd with tmp_dest (assumes last arg is output)
    cmd = list(cmd)
    cmd[-1] = tmp_dest

    logging.info("► %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True)
    rc = proc.returncode

    if rc == 0:
        try:
            # Replace existing file atomically
            os.replace(tmp_dest, final_dest)
        except Exception as e:
            logging.error("Atomic replace failed for %s: %s", final_dest, e)
            # Cleanup temp file
            try:
                if os.path.exists(tmp_dest):
                    os.remove(tmp_dest)
            except Exception:
                pass
            return 1
    else:
        logging.error("FFmpeg failed (rc=%s) for %s", rc, final_dest)
        stderr_output = proc.stderr.decode('utf-8', errors='ignore') if proc.stderr else ''
        
        # Cleanup temp file
        try:
            if os.path.exists(tmp_dest):
                os.remove(tmp_dest)
        except Exception:
            pass
        
        # Retry without cover art if the error seems related to video/image processing
        if retry_without_video and ('vf#' in stderr_output or 'vist#' in stderr_output or 
                                     'VipsJpeg' in stderr_output or 'png' in stderr_output.lower() or
                                     'mjpeg' in stderr_output.lower() or 'decode' in stderr_output.lower()):
            logging.warning("Retrying without cover art for %s", final_dest)
            # Build command without video mapping
            cmd_no_video = [c for c in cmd if c not in ['-map', '0:v:0?', '-c:v', 'mjpeg', 'copy'] 
                           and not c.startswith('-vf')]
            # Remove -map 0:v:0? pair
            try:
                idx = cmd_no_video.index('0:v:0?')
                cmd_no_video.pop(idx)  # Remove 0:v:0?
                if idx > 0 and cmd_no_video[idx-1] == '-map':
                    cmd_no_video.pop(idx-1)  # Remove -map
            except ValueError:
                pass
            # Remove video codec options
            filtered_cmd = []
            skip_next = False
            for i, c in enumerate(cmd_no_video):
                if skip_next:
                    skip_next = False
                    continue
                if c == '-c:v':
                    skip_next = True
                    continue
                filtered_cmd.append(c)
            
            logging.info("► (retry) %s", " ".join(filtered_cmd))
            proc2 = subprocess.run(filtered_cmd)
            rc = proc2.returncode
            
            if rc == 0:
                try:
                    os.replace(tmp_dest, final_dest)
                except Exception as e:
                    logging.error("Atomic replace failed for %s: %s", final_dest, e)
                    try:
                        if os.path.exists(tmp_dest):
                            os.remove(tmp_dest)
                    except Exception:
                        pass
                    return 1
            else:
                logging.error("FFmpeg retry also failed (rc=%s) for %s", rc, final_dest)
                try:
                    if os.path.exists(tmp_dest):
                        os.remove(tmp_dest)
                except Exception:
                    pass

    return rc

# ── ffmpeg command construction ─────────────────────────────────────
def ffmpeg_cmd(src: str, dest: str, codec: str) -> list[str]:
    """
    Build an FFmpeg command that:
      - keeps first audio stream (-map 0:a:0)
      - keeps first picture stream, if any (-map 0:v:0?)
      - copies global tags (-map_metadata 0)
      - copies picture stream where supported

    Note:
    - For MP3: to preserve embedded album art, ffmpeg needs `-map 0:v:0?` and
      `-c:v mjpeg` to write as ID3 APIC. Copying (-c:v copy) is not valid for mp3.
    - For MP4/M4A: copying the image stream is acceptable.
    """
    src = nfc_path(src)
    dest = nfc_path(dest)

    common = [
        "ffmpeg", "-loglevel", "error", "-y",
        "-i", src,
        "-map", "0:a:0",
        "-map", "0:v:0?",
        "-map_metadata", "0",
    ]

    if codec == "alac":  # .m4a (MP4) container
        return common + [
            "-c:a", "alac",
            "-c:v", "copy",           # keep cover art as is
            "-f", "mp4",
            dest
        ]

    if codec == "aac":   # .m4a (MP4) container
        return common + [
            "-c:a", "aac", "-b:a", "256k",
            "-c:v", "copy",           # keep cover art as is
            "-movflags", "+faststart",
            "-f", "mp4",
            dest
        ]

    if codec == "mp3":   # ID3 container
        return common + [
            "-c:a", "libmp3lame", "-b:a", "256k",
            "-c:v", "mjpeg",                  # embed cover art as ID3 APIC
            "-id3v2_version", "3",
            "-write_id3v2", "1",
            "-f", "mp3",
            dest
        ]

    raise ValueError(f"Unknown codec: {codec}")

# ── core actions ────────────────────────────────────────────────────
def ensure_targets(src: str, force: bool = False) -> None:
    """
    Create or update mirror files for a given source file.
    - If force is True, re-encode/copy even if outputs exist.
    - Respects safety guard.
    - Waits for file to be stable before processing.
    - Uses atomic writes for encoded outputs.
    """
    src = nfc_path(src)
    if not is_audio(src):
        return
    if safety_guard_active():
        return

    # Ensure source file is stable (avoid partial processing)
    if not wait_for_stable(src):
        logging.warning("Source not stable or disappeared: %s", src)
        return

    # Avoid duplicate concurrent work per source
    with IN_PROGRESS_LOCK:
        if src in IN_PROGRESS:
            return
        IN_PROGRESS.add(src)

    try:
        stem = Path(src).stem
        base_name = os.path.basename(src)

        # ----- ALAC mirror ------------------------------------------------
        if is_mp3(src):
            # Copy unchanged .mp3 to ALAC mirror folder (exact filename preserved)
            alac_dest = nfc_path(os.path.join(DEST_ALAC, base_name))
            if force or not os.path.exists(alac_dest):
                if safety_guard_active():
                    return
                logging.info("► copy %s → %s", src, alac_dest)
                os.makedirs(DEST_ALAC, exist_ok=True)
                try:
                    shutil.copy2(src, alac_dest)
                except Exception as e:
                    logging.error("Copy failed %s → %s: %s", src, alac_dest, e)
        else:
            # Encode to ALAC .m4a
            alac_dest = nfc_path(os.path.join(DEST_ALAC, f"{nfc(stem)}.m4a"))
            if force or not os.path.exists(alac_dest):
                if safety_guard_active():
                    return
                rc = atomic_ffmpeg_run(ffmpeg_cmd(src, alac_dest, "alac"), alac_dest)
                if rc != 0:
                    logging.error("ALAC encode failed for %s", src)

        # ----- MP3-256 ----------------------------------------------------
        mp3_dest = nfc_path(os.path.join(DEST_MP3, f"{nfc(stem)}.mp3"))
        if force or not os.path.exists(mp3_dest):
            if safety_guard_active():
                return
            rc = atomic_ffmpeg_run(ffmpeg_cmd(src, mp3_dest, "mp3"), mp3_dest)
            if rc != 0:
                logging.error("MP3 encode failed for %s", src)

        # ----- AAC-256 ----------------------------------------------------
        aac_dest = nfc_path(os.path.join(DEST_AAC, f"{nfc(stem)}.m4a"))
        if force or not os.path.exists(aac_dest):
            if safety_guard_active():
                return
            rc = atomic_ffmpeg_run(ffmpeg_cmd(src, aac_dest, "aac"), aac_dest)
            if rc != 0:
                logging.error("AAC encode failed for %s", src)

    finally:
        with IN_PROGRESS_LOCK:
            IN_PROGRESS.discard(src)

def delete_targets(src: str) -> None:
    """
    Delete corresponding mirror files for a given source file.
    Respects safety guard.
    """
    src = nfc_path(src)
    if safety_guard_active():
        return

    stem = Path(src).stem

    # remove possible ALAC mirror
    for fname in (os.path.basename(src), f"{stem}.m4a"):
        p = nfc_path(os.path.join(DEST_ALAC, fname))
        if os.path.exists(p):
            try:
                logging.info("✘ remove %s", p)
                os.remove(p)
            except Exception as e:
                logging.error("Failed to remove %s: %s", p, e)

    # remove lossy mirrors
    for folder, ext in ((DEST_MP3, ".mp3"), (DEST_AAC, ".m4a")):
        p = nfc_path(os.path.join(folder, f"{stem}{ext}"))
        if os.path.exists(p):
            try:
                logging.info("✘ remove %s", p)
                os.remove(p)
            except Exception as e:
                logging.error("Failed to remove %s: %s", p, e)

def purge_all_outputs() -> None:
    """
    Delete all files in the destination folders to force a clean re-encode.
    Respects safety guard and will not run if source appears empty.
    """
    if safety_guard_active():
        logging.warning("Force re-encode requested but safety guard is active. Skipping purge.")
        return

    for folder in (DEST_ALAC, DEST_MP3, DEST_AAC):
        try:
            os.makedirs(folder, exist_ok=True)
            with os.scandir(folder) as it:
                for entry in it:
                    if entry.is_file():
                        try:
                            logging.info("✘ purge %s", entry.path)
                            os.remove(entry.path)
                        except Exception as e:
                            logging.error("Failed to purge %s: %s", entry.path, e)
        except Exception as e:
            logging.error("Failed to purge folder %s: %s", folder, e)

# ── initial full sync ──────────────────────────────────────────────
def initial_sync(force_reencode: bool = False) -> None:
    # Create destination directories
    for d in (DEST_ALAC, DEST_MP3, DEST_AAC):
        os.makedirs(d, exist_ok=True)

    if force_reencode:
        purge_all_outputs()

    if safety_guard_active():
        logging.info("Initial sync skipped due to safety guard.")
        return

    logging.info("Initial sync …")

    # Collect source audio files
    try:
        src_files = []
        with os.scandir(SRC_DIR) as it:
            for entry in it:
                if entry.is_file():
                    full = entry.path
                    if is_audio(full):
                        src_files.append(full)
    except Exception as e:
        logging.error("Failed to scan SRC_DIR %s: %s", SRC_DIR, e)
        return

    stems = {Path(f).stem for f in src_files}

    # Build / update mirrors
    for f in src_files:
        ensure_targets(f, force=False)

    # Remove orphans in every mirror (only if safety allows)
    if safety_guard_active():
        logging.info("Skipping orphan cleanup due to safety guard.")
        logging.info("Initial sync complete (partial).")
        return

    for folder, keep_ext in (
        (DEST_ALAC, (".mp3", ".m4a")),
        (DEST_MP3,  (".mp3",)),
        (DEST_AAC,  (".m4a",))
    ):
        try:
            with os.scandir(folder) as it:
                for entry in it:
                    if entry.is_file():
                        fname = entry.name
                        if fname.endswith(keep_ext) and Path(fname).stem not in stems:
                            p = nfc_path(os.path.join(folder, fname))
                            try:
                                logging.info("✘ remove %s", p)
                                os.remove(p)
                            except Exception as e:
                                logging.error("Failed to remove %s: %s", p, e)
        except Exception as e:
            logging.error("Failed to scan mirror folder %s: %s", folder, e)

    logging.info("Initial sync complete.")

# ── live watcher ───────────────────────────────────────────────────
class SyncHandler(FileSystemEventHandler):
    def _later_process(self, path: str, force: bool = False) -> None:
        if not is_audio(path):
            return
        # Slight delay to coalesce bursts
        time.sleep(0.2)
        if safety_guard_active():
            return
        ensure_targets(path, force=force)

    # every handler gets the same “directory?” guard
    def on_created(self, e):
        if e.is_directory:
            return
        self._later_process(e.src_path, force=False)

    def on_modified(self, e):
        if e.is_directory:
            return
        # Any attribute/content change should force a re-encode
        # Delete old targets first, then re-encode
        if safety_guard_active():
            return
        delete_targets(e.src_path)
        self._later_process(e.src_path, force=True)

    def on_moved(self, e):
        if e.is_directory:
            return
        # Rename: delete old encoded file(s), then re-encode with new name
        if is_audio(e.src_path):
            delete_targets(e.src_path)
        if is_audio(e.dest_path):
            self._later_process(e.dest_path, force=True)

    def on_deleted(self, e):
        if e.is_directory:
            return
        if is_audio(e.src_path):
            delete_targets(e.src_path)

# ── main ───────────────────────────────────────────────────────────
def main():
    # Make sure logs display Unicode fine
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S"
    )

    # Normalize dir paths to NFC once
    global SRC_DIR, DEST_ALAC, DEST_MP3, DEST_AAC
    SRC_DIR   = nfc_path(SRC_DIR)
    DEST_ALAC = nfc_path(DEST_ALAC)
    DEST_MP3  = nfc_path(DEST_MP3)
    DEST_AAC  = nfc_path(DEST_AAC)

    if not os.path.isdir(SRC_DIR):
        logging.error("SRC_DIR %s does not exist", SRC_DIR)
        sys.exit(1)

    force_reencode = truthy_env(FORCE_REENCODE)
    if force_reencode:
        logging.info("FORCE_REENCODE is enabled. Purging mirrors and re-encoding.")

    initial_sync(force_reencode=force_reencode)

    obs = Observer()
    obs.schedule(SyncHandler(), SRC_DIR, recursive=False)
    obs.start()
    logging.info("Watching %s …", SRC_DIR)

    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        obs.stop()
    obs.join()

if __name__ == "__main__":
    main()
