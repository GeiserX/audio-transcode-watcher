<p align="center">
  <img src="https://raw.githubusercontent.com/GeiserX/audio-transcode-watcher/main/docs/images/banner.svg" alt="audio-transcode-watcher banner" width="900" />
</p>

<p align="center">
  <strong>A containerized service that watches a source folder and automatically transcodes audio files to multiple formats simultaneously.</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/audio-transcode-watcher/"><img src="https://img.shields.io/pypi/v/audio-transcode-watcher?style=flat-square" alt="PyPI" /></a>
  <a href="https://github.com/GeiserX/audio-transcode-watcher/actions/workflows/tests.yml"><img src="https://github.com/GeiserX/audio-transcode-watcher/actions/workflows/tests.yml/badge.svg" alt="Tests" /></a>
  <a href="https://hub.docker.com/r/drumsergio/audio-transcoder"><img src="https://img.shields.io/docker/pulls/drumsergio/audio-transcoder" alt="Docker Pulls" /></a>
  <a href="https://hub.docker.com/r/drumsergio/audio-transcoder"><img src="https://img.shields.io/docker/image-size/drumsergio/audio-transcoder/latest" alt="Docker Image Size" /></a>
  <a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="License: GPL v3" /></a>
  <a href="https://github.com/GeiserX/audio-transcode-watcher/releases"><img src="https://img.shields.io/github/v/release/GeiserX/audio-transcode-watcher" alt="GitHub Release" /></a>
</p>

---

Perfect for maintaining a music library in multiple formats -- lossless for archival, lossy for portable devices -- without lifting a finger. Drop a FLAC into your source folder and get ALAC, MP3, AAC, and Opus copies instantly.

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [How It Works](#how-it-works)
- [Performance](#performance)
- [Verification Tool](#verification-tool)
- [Development](#development)
- [License](#license)
- [Contributing](#contributing)

## Features

- **Real-time file watching** -- Detects new, modified, renamed, or deleted files via watchdog
- **Multiple simultaneous outputs** -- Transcode to any number of formats in a single pass
- **Six codecs** -- ALAC, AAC, MP3, Opus, FLAC, WAV with configurable bitrates
- **Synced lyrics** -- Auto-fetches `.lrc` lyrics with Whisper speech-to-text fallback
- **Artwork preservation** -- Optionally embeds cover art in output files
- **Atomic writes** -- No partial or corrupted files on failure
- **Orphan cleanup** -- Automatically removes outputs that no longer have a source
- **Safety guards** -- Prevents accidental mass deletion if folders appear empty
- **Unicode safe** -- Properly handles special characters in filenames
- **Docker-first** -- Ships as a lightweight container built on Python 3.14-slim + FFmpeg

## Quick Start

### Docker Compose (Recommended)

**1.** Create a `config.yaml` file:

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

**2.** Create a `docker-compose.yml`:

```yaml
services:
  audio-transcoder:
    image: drumsergio/audio-transcoder:0.4.1
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

**3.** Start the service:

```bash
docker compose up -d
```

### Docker CLI

```bash
docker run -d \
  --name audio_transcoder \
  -e TZ=Europe/Madrid \
  -e CONFIG_FILE=/app/config.yaml \
  -v ./config.yaml:/app/config.yaml:ro \
  -v /path/to/flac:/music/flac:ro \
  -v /path/to/mp3:/music/mp3 \
  --restart unless-stopped \
  drumsergio/audio-transcoder:0.4.1
```

## Configuration

Configuration is provided via a YAML file. Set the `CONFIG_FILE` environment variable to its path inside the container.

### Full Configuration Example

```yaml
# Source folder containing original audio files
source:
  path: /music/flac

# Output destinations -- define as many as you need
outputs:
  # Lossless ALAC for Apple devices
  - name: alac
    codec: alac
    path: /music/alac
    include_artwork: true

  # High-quality MP3 for broad compatibility
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

  # Opus for streaming (best quality-to-size ratio)
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

| Codec  | Extension | Bitrate   | Artwork | Description                |
|--------|-----------|-----------|---------|----------------------------|
| `alac` | `.m4a`    | N/A       | Yes     | Lossless, Apple compatible |
| `aac`  | `.m4a`    | 64k--320k | Yes     | Lossy, excellent quality   |
| `mp3`  | `.mp3`    | 64k--320k | Yes     | Lossy, universal support   |
| `opus` | `.opus`   | 32k--256k | No      | Lossy, best quality/size   |
| `flac` | `.flac`   | N/A       | Yes     | Lossless, open format      |
| `wav`  | `.wav`    | N/A       | No      | Lossless, uncompressed     |

### JSON Configuration

You can alternatively provide configuration as a JSON string via the `CONFIG_JSON` environment variable:

```yaml
environment:
  - CONFIG_JSON={"source":{"path":"/music/flac"},"outputs":[{"name":"mp3","codec":"mp3","bitrate":"256k","path":"/music/mp3"}]}
```

## How It Works

1. **Initial sync** -- On startup, scans the source folder and encodes any missing files to all configured outputs.
2. **Watch mode** -- Continuously monitors the source folder for changes:
   - **New files** are encoded to all configured outputs
   - **Modified files** are re-encoded to all outputs
   - **Renamed files** trigger deletion of old outputs and creation of new ones
   - **Deleted files** have their corresponding outputs removed
3. **Orphan cleanup** -- Removes output files that no longer have a matching source.
4. **Lyrics sync** -- Fetches synced `.lrc` lyrics from online databases; falls back to Whisper transcription when no lyrics are found.

### Safety Guards

The service includes multiple guards to prevent data loss:

- If the source folder appears empty, no deletions are performed
- If any output folder appears empty, no deletions are performed
- All writes are atomic -- encoding happens to a temporary file that is moved into place only on success

## Performance

- **Parallel processing** -- Multiple output formats are encoded concurrently
- **Incremental sync** -- Only missing or changed files are processed; unchanged files are skipped
- **Stability detection** -- Files are not processed until they have been stable on disk for a configurable period, avoiding partial reads during large copies or network transfers
- **Low idle footprint** -- Uses inotify/FSEvents-based watching with minimal CPU usage when idle

## Verification Tool

A built-in verification tool checks that all outputs are in sync with the source:

```bash
# Basic sync check
docker exec audio_transcoder python /app/tools/verify_sync.py --config /app/config.yaml

# Thorough check including duration comparison
docker exec audio_transcoder python /app/tools/verify_sync.py --config /app/config.yaml --check-duration -v
```

## Development

### Requirements

- Docker (recommended), or Python 3.14+ with FFmpeg installed
- [Hatch](https://hatch.pypa.io/) build system

### Running Tests

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Run tests with coverage
pytest
```

### Building the Docker Image

```bash
docker build -t audio-transcoder:dev .
```

## Related Music Tools

| Project | Description |
|---------|-------------|
| [slskd-transform](https://github.com/GeiserX/slskd-transform) | Bulk upgrade your music library from lossy to lossless via Soulseek |
| [telegram-slskd-local-bot](https://github.com/GeiserX/telegram-slskd-local-bot) | Automated music discovery and download via Telegram |
| [jellyfin-encoder](https://github.com/GeiserX/jellyfin-encoder) | Automatic 720p HEVC/AV1 transcoding for Jellyfin |


## License

This project is licensed under the **GNU General Public License v3.0** -- see the [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome. Please open an issue to discuss significant changes before submitting a pull request.

1. Fork the repository
2. Create a feature branch (`git checkout -b feat/amazing-feature`)
3. Run tests (`pytest`)
4. Commit your changes (`git commit -m 'feat: add amazing feature'`)
5. Push to the branch (`git push origin feat/amazing-feature`)
6. Open a Pull Request
