#!/usr/bin/env python3
"""
Flat-folder music mirror

• MP3 source:     copy -> ALAC folder (.mp3)
                  encode 256 kb/s MP3 + AAC

• Loss-less src:  encode ALAC (.m4a) + MP3-256 + AAC-256
"""

import os, shutil, subprocess, time, logging, sys
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events    import FileSystemEventHandler

# ── folders (override through env-vars) ────────────────────────────
SRC_DIR   = os.getenv("SRC_DIR",   "/music/flac")     # master
DEST_ALAC = os.getenv("DEST_ALAC", "/music/alac")     # “ALAC” mirror
DEST_MP3  = os.getenv("DEST_MP3",  "/music/mp3256")   # MP3-256
DEST_AAC  = os.getenv("DEST_AAC",  "/music/aac256")   # AAC-256

LOSSLESS_EXT = {".flac", ".alac", ".wav", ".ape", ".aiff",
                ".wv", ".tta", ".ogg", ".opus"}

# ── helpers ────────────────────────────────────────────────────────
def is_audio(p: str) -> bool:
    return os.path.isfile(p) and Path(p).suffix.lower() in \
           LOSSLESS_EXT.union({".mp3"})
def is_mp3(p):   return Path(p).suffix.lower() == ".mp3"
def lossless(p): return is_audio(p) and not is_mp3(p)

def ffmpeg_cmd(src, dest, codec):
    """
    Build an FFmpeg command that
      • keeps first audio stream         (-map 0:a:0)
      • keeps first picture stream, if any (-map 0:v:0?)
      • copies global tags               (-map_metadata 0)
      • copies picture stream            (-c:v copy)
    """
    common = ["ffmpeg", "-loglevel", "error", "-y",
              "-i", src,
              "-map", "0:a:0",
              "-map", "0:v:0?",
              "-map_metadata", "0",
              "-c:v", "copy"]                 # keep cover art as is

    if codec == "alac":                       # .m4a (MP4) container
        return common + ["-c:a", "alac",
                         "-f", "mp4",
                         dest]

    if codec == "aac":                        # .m4a (MP4) container
        return common + ["-c:a", "aac", "-b:a", "256k",
                         "-movflags", "+faststart",
                         "-f", "mp4",
                         dest]

    if codec == "mp3":                        # ID3 container
        return common + ["-c:a", "libmp3lame", "-b:a", "256k",
                         "-id3v2_version", "3",
                         dest]

def run(cmd):
    logging.info("► %s", " ".join(cmd))
    if subprocess.run(cmd).returncode:
        logging.error("FFmpeg failed!")

def ensure_targets(src):
    stem = Path(src).stem

    # ----- ALAC mirror ------------------------------------------------
    if is_mp3(src):                                   # copy unchanged .mp3
        alac_dest = os.path.join(DEST_ALAC, os.path.basename(src))
        if not os.path.exists(alac_dest):
            logging.info("► copy %s → %s", src, alac_dest)
            shutil.copy2(src, alac_dest)
    else:                                             # encode to ALAC.m4a
        alac_dest = os.path.join(DEST_ALAC, f"{stem}.m4a")
        if not os.path.exists(alac_dest):
            run(ffmpeg_cmd(src, alac_dest, "alac"))

    # ----- MP3-256 ----------------------------------------------------
    mp3_dest = os.path.join(DEST_MP3, f"{stem}.mp3")
    if not os.path.exists(mp3_dest):
        run(ffmpeg_cmd(src, mp3_dest, "mp3"))

    # ----- AAC-256 ----------------------------------------------------
    aac_dest = os.path.join(DEST_AAC, f"{stem}.m4a")
    if not os.path.exists(aac_dest):
        run(ffmpeg_cmd(src, aac_dest, "aac"))

def delete_targets(src):
    stem = Path(src).stem
    # remove possible ALAC mirror
    for fname in (os.path.basename(src), f"{stem}.m4a"):
        p = os.path.join(DEST_ALAC, fname)
        if os.path.exists(p):
            logging.info("✘ remove %s", p)
            os.remove(p)
    # remove lossy mirrors
    for folder, ext in ((DEST_MP3, ".mp3"), (DEST_AAC, ".m4a")):
        p = os.path.join(folder, f"{stem}{ext}")
        if os.path.exists(p):
            logging.info("✘ remove %s", p)
            os.remove(p)

# ── initial full sync ──────────────────────────────────────────────
def initial_sync():
    logging.info("Initial sync …")
    for d in (DEST_ALAC, DEST_MP3, DEST_AAC):
        os.makedirs(d, exist_ok=True)

    src_files = [f for f in os.listdir(SRC_DIR)
                 if is_audio(os.path.join(SRC_DIR, f))]
    stems = {Path(f).stem for f in src_files}

    # build / update mirrors
    for f in src_files:
        ensure_targets(os.path.join(SRC_DIR, f))

    # remove orphans in every mirror
    for folder, keep_ext in (
        (DEST_ALAC, (".mp3", ".m4a")),
        (DEST_MP3,  (".mp3",)),
        (DEST_AAC,  (".m4a",))):
        for fname in os.listdir(folder):
            if fname.endswith(keep_ext) and Path(fname).stem not in stems:
                logging.info("✘ remove %s/%s", folder, fname)
                os.remove(os.path.join(folder, fname))

    logging.info("Initial sync complete.")

# ── live watcher ───────────────────────────────────────────────────
class SyncHandler(FileSystemEventHandler):
    def _later(self, path):
        if not is_audio(path):
            return
        time.sleep(0.5)
        ensure_targets(path)

    # every handler gets the same “directory?” guard
    def on_created(self, e):
        if e.is_directory:
            return
        self._later(e.src_path)

    def on_modified(self, e):
        if e.is_directory:
            return
        self._later(e.src_path)

    def on_moved(self, e):
        if not e.is_directory and is_audio(e.src_path):
            delete_targets(e.src_path)
        if not e.is_directory and is_audio(e.dest_path):
            self._later(e.dest_path)

    def on_deleted(self, e):
        if not e.is_directory and is_audio(e.src_path):
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