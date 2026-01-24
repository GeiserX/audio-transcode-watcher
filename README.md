# audio-transcode-watcher

Watch a source folder (FLAC library) and automatically transcode new/modified files into multiple formats: ALAC, MP3-256, and AAC-256.

## Features

- **Automatic sync**: Watches source folder for new/modified files and encodes them
- **Multiple output formats**: ALAC (.m4a), MP3 256kbps, AAC 256kbps
- **Metadata preservation**: Copies tags and cover art to encoded files
- **Safety guard**: Prevents accidental deletions if source appears empty
- **Unicode safe**: Properly handles special characters in filenames
- **Atomic writes**: Ensures no partial/corrupted files on encoding failure

## Docker Usage

```yaml
services:
  audio-transcoder:
    image: drumsergio/audio-transcoder:0.0.6
    container_name: audio_transcoder
    environment:
      - TZ=Europe/Madrid
      - SRC_DIR=/music/flac
      - DEST_ALAC=/music/alac
      - DEST_MP3=/music/mp3256
      - DEST_AAC=/music/aac256
      - FORCE_REENCODE=false  # Set to true to re-encode everything
    volumes:
      - /path/to/flac:/music/flac:ro
      - /path/to/alac:/music/alac
      - /path/to/mp3:/music/mp3256
      - /path/to/aac:/music/aac256
    restart: unless-stopped
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SRC_DIR` | `/music/flac` | Source folder with lossless files |
| `DEST_ALAC` | `/music/alac` | ALAC output folder |
| `DEST_MP3` | `/music/mp3256` | MP3 256kbps output folder |
| `DEST_AAC` | `/music/aac256` | AAC 256kbps output folder |
| `FORCE_REENCODE` | `false` | If true, purge outputs and re-encode everything |

## Supported Source Formats

FLAC, ALAC, WAV, APE, AIFF, WV, TTA, OGG, OPUS, and MP3 (copied to ALAC folder unchanged)

## Verification Script

Use `verify_sync.py` to check for missing files or duration mismatches between source and encoded folders:

```bash
# Run inside the container
docker exec audio_transcoder python3 /app/verify_sync.py --check-duration --verbose

# Or standalone with custom paths
python verify_sync.py --src /path/to/flac --aac /path/to/aac --check-duration
```

### Options

- `--check-duration`: Compare durations between source and encoded files (slower)
- `--duration-threshold SECONDS`: Max acceptable duration difference (default: 2.0)
- `--verbose`: Show detailed file lists
- `--json`: Output results as JSON

## Building

```bash
docker build -t audio-transcoder:latest .
```

## License

MIT
