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

  # Seconds between periodic full syncs (default: 300)
  sync_interval_seconds: 300
```

`whisper_fallback` and `whisper_model` were removed in 0.6.0 along with the Whisper lyrics fallback. A config that still sets them loads, and the first load logs one warning that they are ignored.

## Source formats

| Kind     | Extensions                                                         |
|----------|--------------------------------------------------------------------|
| Lossless | `.flac` `.alac` `.wav` `.aiff` `.aif` `.ape` `.wv` `.tta` `.tak`   |
| Lossy    | `.mp3` `.aac` `.m4a` `.ogg` `.opus` `.wma`                         |

A lossless source is encoded to every output.

A lossy source is never encoded into a lossless output, because that only makes a big file that looks lossless. It is copied there unchanged, with its own extension, so an ALAC folder can hold `.m4a` encodes next to `.mp3` or `.ogg` copies. Into a lossy output it is copied unchanged when it already has that output's codec (`.mp3` into `mp3`, `.m4a` or `.aac` into `aac`, `.opus` into `opus`) and transcoded otherwise. Copies keep their tags and cover as they are.

When a lossless and a lossy file share a name in the source folder, the lossless one is used for every output.

## Tags in ALAC and AAC outputs

FFmpeg writes the standard MP4 atoms (title, artist, album and so on). After an ALAC or AAC encode, the tags those atoms cannot hold are copied from the source as iTunes freeform atoms named `----:com.apple.iTunes:<NAME>`: `REPLAYGAIN_TRACK_GAIN`, `REPLAYGAIN_TRACK_PEAK`, `REPLAYGAIN_ALBUM_GAIN`, `REPLAYGAIN_ALBUM_PEAK`, `MUSICBRAINZ_TRACKID`, `MUSICBRAINZ_ALBUMID`, `MUSICBRAINZ_ARTISTID`, `MUSICBRAINZ_ALBUMARTISTID`, `MUSICBRAINZ_RELEASEGROUPID`, `ISRC`, `LABEL`, `CATALOGNUMBER`, `ARTISTSORT`, `ALBUMARTISTSORT` and `ALBUMSORT`.

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

