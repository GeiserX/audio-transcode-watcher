"""Main entry point for audio-transcode-watcher."""

from __future__ import annotations

import logging
import os
import sys
import time

from .config import load_config
from .sync import initial_sync
from .utils import nfc_path
from .watcher import start_watcher


def setup_logging() -> None:
    """Configure logging for the application."""
    # Ensure Unicode output works
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
        datefmt="%H:%M:%S",
    )


def main() -> int:
    """Main entry point."""
    setup_logging()
    logger = logging.getLogger(__name__)
    
    # Load configuration
    try:
        config = load_config()
    except ValueError as e:
        logger.error("Configuration error: %s", e)
        return 1
    
    # Normalize paths
    config.source_path = nfc_path(config.source_path)
    for output in config.outputs:
        output.path = nfc_path(output.path)
    
    # Validate source exists
    if not os.path.isdir(config.source_path):
        logger.error("Source directory does not exist: %s", config.source_path)
        return 1
    
    # Log configuration
    logger.info("Source: %s", config.source_path)
    for output in config.outputs:
        logger.info(
            "Output: %s (%s%s) -> %s",
            output.name,
            output.codec,
            f" {output.bitrate}" if output.bitrate else "",
            output.path,
        )
    
    # Perform initial sync
    initial_sync(config)
    
    # Start watcher
    observer = start_watcher(config)
    logger.info("Watching %s …", config.source_path)
    
    # Periodic sync interval (check for missing outputs every 5 minutes)
    sync_interval = 300  # 5 minutes
    last_sync = time.time()
    
    try:
        while True:
            time.sleep(10)
            
            # Periodic sync to catch deleted outputs
            if time.time() - last_sync >= sync_interval:
                logger.info("Periodic sync check…")
                initial_sync(config)
                last_sync = time.time()
    except KeyboardInterrupt:
        observer.stop()
    
    observer.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
