#!/usr/bin/env python3
"""
Simple flat-folder music mirroring with watchdog.

Behaviour
---------
lossless → copy     to ALAC folder (unchanged)
mp3      → re-encode to MP3-256 & AAC-256 folders
deletes  → remove corresponding targets
"""

import os, shutil, subprocess, time, logging, sys
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events    import FileSystemEventHandler

# ── configuration (via env, defaults for Docker layout) ────────────
SRC_DIR     = os.getenv("SRC_DIR",     "/music/flac")     # watch this
DEST_ALAC   = os.getenv("DEST_ALAC",   "/music/alac")     # copy lossless
DEST_MP3    = os.getenv("DEST_MP3",    "/music/mp3256")   # mp3 256k
DEST_AAC    = os.getenv("DEST_AAC",    "/music/aac256")   # aac 256k

LOSSLESS_EXT = {".flac", ".alac", ".wav", ".ape", ".aiff", ".wv", ".tta"}

# ── helpers ────────────────────────────────────────────────────────
def is_audio(path: str) -> bool:
    return Path(path).suffix.lower() in LOSSLESS_EXT.union({".mp3", ".aac", "m4a"})

def lossless(path: str) -> bool:
    return Path(path).suffix.lower() in LOSSLESS_EXT

def ffmpeg_cmd(src, dest, codec, bitrate="256k"):
    if codec == "mp3":
        return ["ffmpeg", "-loglevel", "error", "-y",
                "-i", src, "-map_metadata", "0",
                "-c:a", "libmp3lame", "-b:a", bitrate, dest]
    if codec == "aac":
        return ["ffmpeg", "-loglevel", "error", "-y",
                "-i", src, "-map_metadata", "0",
                "-c:a", "aac", "-b:a", bitrate,
                "-movflags", "+faststart", dest]

def run(cmd):
    logging.info("► %s", " ".join(cmd))
    res = subprocess.run(cmd)
    if res.returncode:
        logging.error("FFmpeg failed (%s)", res.returncode)

def ensure_mp3_and_aac(src):
    stem = Path(src).stem
    mp3_out = os.path.join(DEST_MP3,  f"{stem}.mp3")
    aac_out = os.path.join(DEST_AAC,  f"{stem}.m4a")
    if not os.path.exists(mp3_out):
        run(ffmpeg_cmd(src, mp3_out, "mp3"))
    if not os.path.exists(aac_out):
        run(ffmpeg_cmd(src, aac_out, "aac"))

def ensure_alac_copy(src):
    dest = os.path.join(DEST_ALAC, os.path.basename(src))
    if not os.path.exists(dest):
        logging.info("► copy %s → %s", src, dest)
        shutil.copy2(src, dest)

def delete_targets(src):
    stem = Path(src).stem
    if lossless(src):
        target = os.path.join(DEST_ALAC, os.path.basename(src))
        if os.path.exists(target):
            logging.info("✘ remove %s", target)
            os.remove(target)
    else:
        for folder, ext in ((DEST_MP3, ".mp3"), (DEST_AAC, ".m4a")):
            t = os.path.join(folder, f"{stem}{ext}")
            if os.path.exists(t):
                logging.info("✘ remove %s", t)
                os.remove(t)

# ── initial full synchronisation ───────────────────────────────────
def initial_sync():
    logging.info("Initial sync …")
    for d in (DEST_ALAC, DEST_MP3, DEST_AAC):
        os.makedirs(d, exist_ok=True)

    src_files = [f for f in os.listdir(SRC_DIR) if is_audio(os.path.join(SRC_DIR,f))]
    mp3_stems     = {Path(f).stem for f in src_files if f.lower().endswith(".mp3")}
    lossless_base = {f for f in src_files if lossless(f)}

    # build / update targets
    for f in src_files:
        src_path = os.path.join(SRC_DIR, f)
        if lossless(src_path):
            ensure_alac_copy(src_path)
        else:
            ensure_mp3_and_aac(src_path)

    # delete orphaned targets
    for fname in os.listdir(DEST_ALAC):
        if fname not in lossless_base:
            os.remove(os.path.join(DEST_ALAC, fname))
    for folder, ext in ((DEST_MP3, ".mp3"), (DEST_AAC, ".m4a")):
        for fname in os.listdir(folder):
            if fname.endswith(ext) and Path(fname).stem not in mp3_stems:
                os.remove(os.path.join(folder, fname))
    logging.info("Initial sync complete.")

# ── inotify / watchdog handler ─────────────────────────────────────
class SyncHandler(FileSystemEventHandler):
    def _process(self, path):
        if not is_audio(path):
            return
        time.sleep(0.5)                        # give writer a moment
        if os.path.exists(path):
            if lossless(path):
                ensure_alac_copy(path)
            else:
                ensure_mp3_and_aac(path)

    def on_created(self, e):  self._process(e.src_path)
    def on_modified(self, e): self._process(e.src_path)
    def on_moved(self, e):
        if is_audio(e.src_path):
            delete_targets(e.src_path)
        if is_audio(e.dest_path):
            self._process(e.dest_path)
    def on_deleted(self, e):
        if is_audio(e.src_path):
            delete_targets(e.src_path)

# ── main ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s: %(message)s",
                        datefmt="%H:%M:%S")

    if not os.path.isdir(SRC_DIR):
        logging.error("SRC_DIR %s does not exist", SRC_DIR)
        sys.exit(1)

    initial_sync()

    observer = Observer()
    observer.schedule(SyncHandler(), SRC_DIR, recursive=False)
    observer.start()
    logging.info("Watching %s …", SRC_DIR)
    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()