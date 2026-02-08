"""File system watcher for audio-transcode-watcher."""

from __future__ import annotations

import time

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .config import Config
from .sync import delete_outputs, process_source_file, safety_guard_active
from .utils import has_audio_extension, is_audio_file


class AudioSyncHandler(FileSystemEventHandler):
    """
    Watchdog event handler for audio file changes.
    
    Handles file creation, modification, deletion, and rename events.
    """
    
    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
    
    def _process_later(self, path: str, force: bool = False) -> None:
        """Process a file after a brief delay to coalesce rapid events."""
        if not is_audio_file(path):
            return
        
        # Small delay to coalesce burst events
        time.sleep(0.2)
        
        if safety_guard_active(self.config):
            return
        
        process_source_file(path, self.config, force=force, check_stable=True)
    
    def on_created(self, event) -> None:
        """Handle file creation."""
        if event.is_directory:
            return
        self._process_later(event.src_path, force=False)
    
    def on_modified(self, event) -> None:
        """Handle file modification."""
        if event.is_directory:
            return
        
        if safety_guard_active(self.config):
            return
        
        # Delete old outputs first, then re-encode
        delete_outputs(event.src_path, self.config)
        self._process_later(event.src_path, force=True)
    
    def on_moved(self, event) -> None:
        """Handle file rename/move."""
        if event.is_directory:
            return
        
        # Delete outputs for old path (file no longer exists at src_path)
        if has_audio_extension(event.src_path):
            delete_outputs(event.src_path, self.config)
        
        # Create outputs for new path
        if is_audio_file(event.dest_path):
            self._process_later(event.dest_path, force=True)
    
    def on_deleted(self, event) -> None:
        """Handle file deletion."""
        if event.is_directory:
            return
        
        # File no longer exists, so check extension only
        if has_audio_extension(event.src_path):
            delete_outputs(event.src_path, self.config)


def start_watcher(config: Config) -> Observer:
    """
    Start the file system watcher.
    
    Returns the observer instance (call observer.stop() to stop).
    """
    observer = Observer()
    handler = AudioSyncHandler(config)
    observer.schedule(handler, config.source_path, recursive=False)
    observer.start()
    return observer
