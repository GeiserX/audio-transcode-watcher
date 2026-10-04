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

When a lossless and a lossy file share a name in the source folder, the lossless one is used for every output. If the lossy file came first and was already copied or transcoded, that file is replaced by an encode of the lossless one as soon as it is processed. To tell those files apart, each output folder keeps a hidden `.atw-manifest.json` recording which source made each file and whether it was an encode, a copy or a transcode. Deleting it is safe: files it does not know about are treated as they were before 0.6.1.

## Portable limits (channels and sample rate)

Each output can cap the channel count and the sample rate:

```yaml
  - name: aac-256
    codec: aac
    bitrate: 256k
    path: /music/aac
    channels: 2              # downmix anything with more channels to stereo
    max_sample_rate: 48000   # resample anything above 48 kHz
```

`aac` outputs get `channels: 2` and `max_sample_rate: 48000` when they don't set them, because they are for phones, AirPods and CarPlay. Every other codec has no limit unless you set one, and `0` turns a limit off (also for `aac`).

The sample rate never goes up, and a rate above the cap drops within its own family: 88.2 and 176.4 kHz become 44.1 kHz, 96 and 192 kHz become 48 kHz. A rate from neither family goes to the cap. A mono or stereo source is never upmixed. A lossy source that would normally be copied into the output (an `.m4a` into `aac`) is transcoded instead when it exceeds a limit. ALAC and the other lossless outputs keep the source's rate and channels.

Changing these settings does not rebuild files that already exist. They are re-encoded when their source changes, or on startup with `force_reencode: true`. To rebuild only the files above the limits, delete them and let the next periodic sync encode them again:

```bash
docker exec audio_transcoder find /music/aac -name '*.m4a' -exec sh -c 'ffprobe -v error -select_streams a:0 -show_entries stream=sample_rate,channels -of default=nw=1 "$1" | awk -F= "/^sample_rate/{r=\$2} /^channels/{c=\$2} END{exit !(r>48000||c>2)}" && rm -v "$1"' _ {} \;
```

## Tags in ALAC and AAC outputs

FFmpeg writes the standard MP4 atoms (title, artist, album and so on). After an ALAC or AAC encode, the tags FFmpeg drops are copied from the source under the names MusicBrainz Picard uses, so Picard, TagLib (Navidrome, Jellyfin), foobar2000 and iTunes all read them:

| Source tag                                   | MP4 atom                                              |
|----------------------------------------------|-------------------------------------------------------|
| `REPLAYGAIN_TRACK_GAIN`, `_TRACK_PEAK`, `_ALBUM_GAIN`, `_ALBUM_PEAK` | `----:com.apple.iTunes:replaygain_track_gain` and so on, lowercase |
| `MUSICBRAINZ_TRACKID`                        | `----:com.apple.iTunes:MusicBrainz Track Id`          |
| `MUSICBRAINZ_ALBUMID`                        | `----:com.apple.iTunes:MusicBrainz Album Id`          |
| `MUSICBRAINZ_ARTISTID`                       | `----:com.apple.iTunes:MusicBrainz Artist Id`         |
| `MUSICBRAINZ_ALBUMARTISTID`                  | `----:com.apple.iTunes:MusicBrainz Album Artist Id`   |
| `MUSICBRAINZ_RELEASEGROUPID`                 | `----:com.apple.iTunes:MusicBrainz Release Group Id`  |
| `ISRC`, `LABEL`, `CATALOGNUMBER`             | `----:com.apple.iTunes:ISRC`, `LABEL`, `CATALOGNUMBER` |
| `ARTISTSORT`, `ALBUMARTISTSORT`, `ALBUMSORT` | the standard sort atoms `soar`, `soaa`, `soal`        |

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

