# Configuration

Configuration is provided via a YAML file. Set the `CONFIG_FILE` environment variable to its path inside the container.

## Full Configuration Example

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

  # Auto-fetch synced .lrc lyrics (default: true)
  fetch_lyrics: true

  # Use Whisper local transcription as fallback when no lyrics found online (default: true)
  whisper_fallback: true

  # Whisper model size: tiny, base, small, medium, large (default: base)
  whisper_model: base
```

## Supported Codecs

| Codec  | Extension | Bitrate   | Artwork | Description                |
|--------|-----------|-----------|---------|----------------------------|
| `alac` | `.m4a`    | N/A       | Yes     | Lossless, Apple compatible |
| `aac`  | `.m4a`    | 64k--320k | Yes     | Lossy, excellent quality   |
| `mp3`  | `.mp3`    | 64k--320k | Yes     | Lossy, universal support   |
| `opus` | `.opus`   | 32k--256k | No      | Lossy, best quality/size   |
| `flac` | `.flac`   | N/A       | Yes     | Lossless, open format      |
| `wav`  | `.wav`    | N/A       | No      | Lossless, uncompressed     |

## JSON Configuration

You can alternatively provide configuration as a JSON string via the `CONFIG_JSON` environment variable:

```yaml
environment:
  - CONFIG_JSON={"source":{"path":"/music/flac"},"outputs":[{"name":"mp3","codec":"mp3","bitrate":"256k","path":"/music/mp3"}]}
```

