"""File system watcher for audio-transcode-watcher."""

from __future__ import annotations

import os
import threading
import time

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .config import Config
from .sync import (
    delete_outputs,
    delete_sidecars,
    process_source_file,
    safety_guard_active,
    sync_sidecars,
)
from .utils import (
    has_audio_extension,
    has_sidecar_extension,
    is_audio_file,
    nfc_path,
    remove_empty_dirs,
    walk_audio_files,
)


class AudioSyncHandler(FileSystemEventHandler):
    """
    Watchdog event handler for audio file changes.

    Handles file creation, modification, deletion, and rename events.
    """

    # Ignore on_modified events for files processed within this window.
    # Prevents encode-delete loops caused by spurious filesystem events
    # (e.g. directory mtime change after .lrc sidecar creation).
    _EVENT_COOLDOWN = 10.0

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self._processed_at: dict[str, float] = {}
        self._processed_lock = threading.Lock()

    def _mark_processed(self, path: str) -> None:
        """Record that a file was just processed."""
        key = nfc_path(path)
        with self._processed_lock:
            self._processed_at[key] = time.monotonic()

    def _is_cooling_down(self, path: str) -> bool:
        """Check if a file was recently processed and should be skipped."""
        key = nfc_path(path)
        with self._processed_lock:
            ts = self._processed_at.get(key, 0.0)
        return (time.monotonic() - ts) < self._EVENT_COOLDOWN

    def _process_later(self, path: str, force: bool = False) -> None:
        """Process a file after a brief delay to coalesce rapid events."""
        if not is_audio_file(path):
            return

        # Small delay to coalesce burst events
        time.sleep(0.2)

        if safety_guard_active(self.config):
            return

        process_source_file(path, self.config, force=force, check_stable=True)
        self._mark_processed(path)
    
    def on_created(self, event) -> None:
        """Handle file creation (audio or sidecar)."""
        if event.is_directory:
            return

        if has_sidecar_extension(event.src_path):
            sync_sidecars(event.src_path, self.config)
        else:
            self._process_later(event.src_path, force=False)
    
    def on_modified(self, event) -> None:
        """Handle file modification (audio or sidecar)."""
        if event.is_directory:
            return

        if safety_guard_active(self.config):
            return

        if has_sidecar_extension(event.src_path):
            sync_sidecars(event.src_path, self.config)
        elif has_audio_extension(event.src_path):
            # Skip spurious events for files we just finished processing.
            # Common on Docker bind mounts where .lrc creation triggers
            # a directory mtime change that emits modify for siblings.
            if self._is_cooling_down(event.src_path):
                return
            delete_outputs(event.src_path, self.config)
            self._process_later(event.src_path, force=True)
    
    def _handle_directory_delete(self, dir_path: str) -> None:
        """Clean up mirrored output subtrees when a source directory is removed."""
        if safety_guard_active(self.config):
            return
        rel_dir = os.path.relpath(dir_path, self.config.source_path)
        if rel_dir == "." or rel_dir.startswith(".."):
            return
        for output in self.config.outputs:
            mirrored = os.path.join(output.path, rel_dir)
            if os.path.isdir(mirrored):
                for dirpath, _dirnames, filenames in os.walk(mirrored, topdown=False):
                    for fname in filenames:
                        try:
                            os.remove(os.path.join(dirpath, fname))
                        except OSError:
                            pass
                remove_empty_dirs(output.path)

    def _reprocess_directory(self, directory: str) -> None:
        """Re-process all audio files in a directory in a background thread."""
        for f in walk_audio_files(directory):
            self._process_later(f, force=True)

    def on_moved(self, event) -> None:
        """Handle file or directory rename/move."""
        if event.is_directory:
            self._handle_directory_delete(event.src_path)
            threading.Thread(
                target=self._reprocess_directory,
                args=(event.dest_path,),
                daemon=True,
            ).start()
            return

        if has_sidecar_extension(event.src_path) or has_sidecar_extension(event.dest_path):
            if has_sidecar_extension(event.src_path):
                delete_sidecars(event.src_path, self.config)
            if has_sidecar_extension(event.dest_path):
                sync_sidecars(event.dest_path, self.config)
        else:
            if has_audio_extension(event.src_path):
                delete_outputs(event.src_path, self.config)
            if is_audio_file(event.dest_path):
                self._process_later(event.dest_path, force=True)

    def on_deleted(self, event) -> None:
        """Handle file or directory deletion."""
        if event.is_directory:
            self._handle_directory_delete(event.src_path)
            return

        if has_sidecar_extension(event.src_path):
            delete_sidecars(event.src_path, self.config)
        elif has_audio_extension(event.src_path):
            delete_outputs(event.src_path, self.config)


def start_watcher(config: Config) -> Observer:
    """
    Start the file system watcher.
    
    Returns the observer instance (call observer.stop() to stop).
    """
    observer = Observer()
    handler = AudioSyncHandler(config)
    observer.schedule(handler, config.source_path, recursive=True)
    observer.start()
    return observer
