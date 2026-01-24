# Audio Transcode Watcher

[![Tests](https://github.com/GeiserX/audio-transcode-watcher/actions/workflows/tests.yml/badge.svg)](https://github.com/GeiserX/audio-transcode-watcher/actions/workflows/tests.yml)
[![Docker Pulls](https://img.shields.io/docker/pulls/drumsergio/audio-transcoder)](https://hub.docker.com/r/drumsergio/audio-transcoder)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

Watch a source folder and automatically transcode audio files to multiple formats. Perfect for maintaining a music library in multiple formats (lossless for archival, lossy for portable devices).

## Features

- **Watch mode**: Automatically detects new, modified, renamed, or deleted files
- **Multiple outputs**: Transcode to any number of formats simultaneously
- **Flexible codecs**: ALAC, AAC, MP3, Opus, FLAC, WAV
- **Configurable bitrates**: Set custom bitrates for lossy codecs
- **Artwork preservation**: Optionally embed cover art in output files
- **Unicode safe**: Properly handles special characters in filenames
- **Atomic writes**: No partial/corrupted files on failure
- **Safety guards**: Prevents accidental data loss if folders appear empty

## Quick Start

### Docker Compose (Recommended)

1. Create a `config.yaml` file:

```yaml
source:
  path: /music/flac

outputs:
  - name: alac
    codec: alac
    path: /music/alac

  - name: mp3-256
    codec: mp3
    bitrate: 256k
    path: /music/mp3

  - name: aac-256
    codec: aac
    bitrate: 256k
    path: /music/aac
```

2. Create `docker-compose.yml`:

```yaml
services:
  audio-transcoder:
    image: drumsergio/audio-transcoder:0.1.0
    container_name: audio_transcoder
    environment:
      - TZ=Europe/Madrid
      - CONFIG_FILE=/app/config.yaml
    volumes:
      - ./config.yaml:/app/config.yaml:ro
      - /path/to/flac:/music/flac:ro
      - /path/to/alac:/music/alac
      - /path/to/mp3:/music/mp3
      - /path/to/aac:/music/aac
    restart: unless-stopped
```

3. Start the service:

```bash
docker compose up -d
```

## Configuration

Configuration is provided via YAML file. Set the `CONFIG_FILE` environment variable to the path of your config file.

### Full Configuration Example

```yaml
# Source folder containing original audio files
source:
  path: /music/flac

# Output destinations - define as many as you need
outputs:
  # Lossless ALAC for Apple devices
  - name: alac
    codec: alac
    path: /music/alac
    include_artwork: true
  
  # High quality MP3 for broad compatibility
  - name: mp3-320
    codec: mp3
    bitrate: 320k
    path: /music/mp3-320
    include_artwork: true
  
  # Balanced MP3 for portable devices
  - name: mp3-192
    codec: mp3
    bitrate: 192k
    path: /music/mp3-192
    include_artwork: true
  
  # AAC for modern devices
  - name: aac-256
    codec: aac
    bitrate: 256k
    path: /music/aac
    include_artwork: true
  
  # Opus for streaming (best quality/size ratio)
  - name: opus-128
    codec: opus
    bitrate: 128k
    path: /music/opus

# Optional settings
settings:
  # Delete all outputs and re-encode on startup
  force_reencode: false
  
  # Maximum time to wait for a file to become stable (seconds)
  stability_timeout: 60
  
  # Minimum time a file must be unchanged before processing (seconds)
  min_stable_seconds: 1.0
```

### Supported Codecs

| Codec | Extension | Bitrate | Artwork | Description |
|-------|-----------|---------|---------|-------------|
| `alac` | .m4a | N/A | Yes | Lossless, Apple compatible |
| `aac` | .m4a | 64k-320k | Yes | Lossy, excellent quality |
| `mp3` | .mp3 | 64k-320k | Yes | Lossy, universal support |
| `opus` | .opus | 32k-256k | No | Lossy, best quality/size |
| `flac` | .flac | N/A | Yes | Lossless, open format |
| `wav` | .wav | N/A | No | Lossless, uncompressed |

### JSON Configuration

Alternatively, you can provide configuration as a JSON string via the `CONFIG_JSON` environment variable:

```yaml
environment:
  - CONFIG_JSON={"source":{"path":"/music/flac"},"outputs":[{"name":"mp3","codec":"mp3","bitrate":"256k","path":"/music/mp3"}]}
```

## Verification Tool

A verification tool is included to check sync status:

```bash
# Using config file
docker exec audio_transcoder python /app/tools/verify_sync.py --config /app/config.yaml

# With duration checking (slower but thorough)
docker exec audio_transcoder python /app/tools/verify_sync.py --config /app/config.yaml --check-duration -v
```

## How It Works

1. **Initial Sync**: On startup, scans the source folder and encodes any missing files
2. **Watch Mode**: Monitors the source folder for changes:
   - **New files**: Encoded to all configured outputs
   - **Modified files**: Re-encoded to all outputs
   - **Renamed files**: Old outputs deleted, new outputs created
   - **Deleted files**: Corresponding outputs deleted
3. **Orphan Cleanup**: Removes output files that no longer have a source

### Safety Guards

The service includes safety guards to prevent data loss:
- If the source folder appears empty, no deletions occur
- If any output folder appears empty, no deletions occur
- Atomic writes ensure no partial files on encoding failure

## Development

### Running Tests

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run tests with coverage
pytest --cov --cov-report=html
```

### Building Docker Image

```bash
docker build -t audio-transcoder:dev .
```

## Requirements

- Docker (recommended) or Python 3.11+
- FFmpeg (included in Docker image)

## License

This project is licensed under the GNU General Public License v3.0 - see the [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feat/amazing-feature`)
3. Run tests (`pytest`)
4. Commit your changes (`git commit -m 'feat: add amazing feature'`)
5. Push to the branch (`git push origin feat/amazing-feature`)
6. Open a Pull Request
