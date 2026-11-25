# Configuration

Configuration is provided via a YAML file. Set the `CONFIG_FILE` environment variable to its path inside the container.

## Full Configuration Example

```yaml
# Source folder containing original audio files
source:
  path: /music/flac
  # Source formats to process (default: all of them). See below.
  # formats: [flac, alac, wav]

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

# Even volume: write ReplayGain 2.0 track tags (default: false)
replaygain: false

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

  # Paths inside the source folder to skip, with * ? [..] and ** patterns
  # (default: none). See below.
  ignored_files:
    - "**/.stversions"
  # ignored_files_in_outputs: false   # see below

  # Seconds between periodic full syncs (default: 300)
  sync_interval_seconds: 300

  # What to do with a source that does not decode cleanly: skip | encode_anyway
  # (default: skip). An output can set its own corrupt_source.
  corrupt_source: skip
```

### Ignored paths

`ignored_files` lists folders and files that the watcher treats as if they were not in the source folder. They get no encode, no copy, no ReplayGain tag, no lyrics and no sidecar copy. One use is the version folder of a sync tool, such as Syncthing's `.stversions/`, which keeps old copies of files inside the library.

```yaml
settings:
  ignored_files:
    - ".stversions/"          # this folder, at the top of the source
    - "**/.stversions"        # a folder with this name at any depth
    - "**/*.wav"              # every WAV file
    - "*/Live"                # a Live folder one level down, like Artist/Live
    - "Artist/Album/03.flac"  # one file
```

- Each entry is a path relative to `source.path`. It is compared with each path one folder or file name at a time, and it matches whole names: `.stversions` means that folder and everything in it, but not `.stversions-old/`.
- A name in an entry can be a pattern: `*` matches any run of characters, `?` matches one character and `[...]` matches one character from the set, as in Python's `fnmatch`. Matching is case-sensitive, so `*.wav` does not match `TAKE.WAV`. A name that is exactly equal always matches, so `Live [1999]` still means that folder.
- `**` matches any number of folders, or none. An entry that starts with `**/` matches at any depth. Any other entry starts at the top of the source, so `.stversions` does not match `Artist/.stversions/`.
- A sync does not read ignored folders at all. A file moved into an ignored folder counts as deleted, and a file moved out of one counts as new.
- An ignored lossless file no longer wins over a lossy file with the same name. With `**/*.flac`, `Song.mp3` next to `Song.flac` gets its own outputs. An output that `Song.flac` made under the shared name, such as `Song.m4a` in an AAC output, is made again from `Song.mp3`. Remove the entry, and `Song.flac` wins again.
- Outputs are derived data. The next full sync removes outputs and `.lrc` copies whose only source is ignored, like any other orphan. This also cleans up files that got into the outputs before you added the entry. Remove the entry, and the next sync makes them again. Files inside an output folder that `ignored_files_in_outputs` protects stay (see below).
- If no audio file in the source is left after the entries apply, the source counts as empty. The safety guard stops the sync, logs a WARNING that names `ignored_files`, and removes nothing.
- Each full sync logs one INFO line with the number of ignored folders it did not read and the number of other ignored audio files it skipped.
- The value must be a list. A plain string, an empty entry, `/`, `**` or an entry with a `..` part stops the watcher at startup with an error.

#### Leave them alone in the outputs too

By default, `ignored_files` acts on the source only. The orphan cleanup still removes audio and `.lrc` files in an output that no source accounts for, and it removes empty folders there. Set `ignored_files_in_outputs: true` to leave the same paths alone in every output folder too. The default is `false`.

One use is a second Syncthing connection that syncs an output folder. That connection keeps its own `.stfolder`, `.stignore` and `.stversions` in the output, and Syncthing stops the folder when its `.stfolder` is missing.

```yaml
settings:
  ignored_files:
    - ".stfolder"     # each side's own Syncthing marker
    - ".stignore"
    - ".stversions"   # each side's own versions folder; use "**/.stversions" if they appear deeper
  ignored_files_in_outputs: true
```

- Each entry is also a path relative to each output's folder, with the same rules as on the source side. `.stversions` means that folder at the top of the output, and `**/.stversions` means a folder with that name at any depth.
- A matching folder protects everything in it, audio files included. A protected folder stays even when it is empty.
- An entry that names a file protects only a file that the watcher does not write itself. Audio, `.lrc`, `.tmp__ff` and `.atw-manifest.json` files are protected only by a matching folder. So `.stignore` protects the output's `.stignore`, and `**/*.wav` never freezes the files of a WAV output.
- The orphan cleanup, the empty-folder cleanup, the cleanup after a source folder is deleted or moved away, `force_reencode` and the stale temp cleanup all leave protected paths alone.
- Files that the watcher made earlier inside a protected folder stay. Delete the ones you do not want by hand. Files that an earlier sync already removed do not come back.
- Outputs of ignored sources outside a protected folder are still removed as orphans, for example `Live.m4a` made from an ignored `Live.wav`.
- If you turn the key off again, the next full sync removes the audio and `.lrc` files under those paths that no source accounts for. That includes the sync tool's own versions.
- The key changes nothing on the source side. The safety guard and `source.formats` work as before.

### Damaged sources

A source that fails the strict decode (see [Corrupt sources](how-it-works.md#corrupt-sources)) is handled by `corrupt_source`:

- `skip` (the default) writes nothing for it.
- `encode_anyway` logs the same ERROR, then encodes it once more without `-xerror` and `-err_detect`, letting FFmpeg conceal the damaged frames. The copy exists, one WARNING line says it was made from a damaged source, and its manifest row has kind `tolerant`. A source that fails even that run stays refused.

Set it under `settings` for every output, or on one output to override, for example only on the AAC output that a phone plays from:

```yaml
  - name: aac-256
    codec: aac
    path: /music/aac
    corrupt_source: encode_anyway
```

Either way the source is remembered as failed, so the strict attempt is not repeated on every scan. Replacing the file with a clean copy (a new size or modification time) clears that, and the next scan rebuilds the tolerant copy with the strict encode.

### ReplayGain

With `replaygain: true` at the top level of the config, every track plays at the same loudness in any player that reads ReplayGain tags, such as Navidrome and foobar2000:

```yaml
replaygain: true
```

For each source that has no `REPLAYGAIN_TRACK_GAIN` tag yet, the watcher measures its loudness with FFmpeg's EBU R128 filter and writes two tags into the source file:

- `REPLAYGAIN_TRACK_GAIN`: -18 LUFS minus the measured integrated loudness, for example `-6.32 dB`.
- `REPLAYGAIN_TRACK_PEAK`: the true peak as a linear value, for example `0.988553`.

They are written in the file's own tag format: Vorbis comments in FLAC, Ogg and Opus, `TXXX` frames in MP3, WAV and AIFF, freeform atoms in M4A, and APEv2 in APE, WavPack and TAK. An ID3v2.3 file stays at 2.3. The M4A atoms use the names Picard writes, such as `----:com.apple.iTunes:replaygain_track_gain`. Raw `.aac` and `.wma` sources are left alone. A source that already has a track gain from any tool is never measured again, and a silent file gets no tag.

Every output gets the same two tags. New encodes carry them from the source. The watcher writes them into outputs that already exist without re-encoding them, and copies lossy sources again so each copy stays identical to its source.

This applies to the whole library on the first sync after you turn it on, and then to every file that arrives. The first pass decodes each untagged source once, through the same `parallel_workers` as encoding, so a large library takes a while. The watcher logs and skips a file it cannot measure, and tries it again after the next restart.

The watcher writes no album gain. Album gain needs to know which files form an album, and a folder does not say that reliably. Many libraries are one flat folder.

Writing the tags changes the source file's size and modification time. The watcher recognises its own writes, so a tagged source is not treated as a new version and nothing is re-encoded. Other tools that watch the source folder, like a media server's library scan or a sync job, see the change once.

`replaygain` is also accepted under `settings`.

`whisper_fallback` and `whisper_model` were removed in 0.6.0 along with the Whisper lyrics fallback. A config that still sets them loads, and the first load logs one warning that they are ignored.

## Start from any format

The source folder can hold any of these formats, mixed. `source.formats` lists the ones to process. Leave it out and every format is processed.

| Name      | Kind     | Extensions       |
|-----------|----------|------------------|
| `flac`    | Lossless | `.flac`          |
| `alac`    | Lossless | `.m4a` `.alac`   |
| `wav`     | Lossless | `.wav`           |
| `aiff`    | Lossless | `.aiff` `.aif`   |
| `ape`     | Lossless | `.ape`           |
| `wavpack` | Lossless | `.wv`            |
| `tta`     | Lossless | `.tta`           |
| `tak`     | Lossless | `.tak`           |
| `mp3`     | Lossy    | `.mp3`           |
| `aac`     | Lossy    | `.m4a` `.aac`    |
| `vorbis`  | Lossy    | `.ogg`           |
| `opus`    | Lossy    | `.opus`          |
| `wma`     | Lossy    | `.wma`           |

An `.m4a` file holds either ALAC or AAC. The watcher asks `ffprobe` which one. It asks once per file, and again only when the file's size or modification time changes. If `ffprobe` cannot read the file, the watcher logs one WARNING and treats the file as AAC.

For example, to use an ALAC library as the source and leave its few AAC files alone:

```yaml
source:
  path: /music/apple-lossless
  formats: [alac]
```

- A file whose format is not in the list is treated like a path in [`ignored_files`](#ignored-paths). It gets no encode, no copy, no ReplayGain tag, no lyrics and no sidecar copy, and file events for it do nothing. The next full sync removes outputs whose only source it was, like any other orphan.
- A lossless file left out of the list no longer wins over a lossy file with the same name. With `formats: [mp3]`, `Song.mp3` next to `Song.flac` gets its own outputs.
- If no file in a listed format is left, the source counts as empty. The safety guard stops the sync, logs a WARNING that names `source.formats`, and removes nothing.
- Each full sync logs one INFO line with the number of audio files it skipped. When `source.formats` leaves something out, the startup log shows the list.
- Names are not case-sensitive. An unknown name, an empty list or a value that is not a list stops the watcher at startup with an error that lists the valid names.

### Lossless and lossy sources

A lossless source is encoded to every output. That includes an ALAC source: it is encoded into an AAC, MP3 or Opus output, and into a FLAC or WAV output. Into an ALAC output it is encoded too, the same way a FLAC source is encoded into a FLAC output. The audio comes out bit for bit the same, and the encode checks that the source decodes cleanly. The ReplayGain, MusicBrainz and other tags in the source's M4A atoms are carried into ALAC and AAC outputs (see [Tags in ALAC and AAC outputs](#tags-in-alac-and-aac-outputs)).

A lossy source is never encoded into a lossless output, because that only makes a big file that looks lossless. It is copied there unchanged, with its own extension, so an ALAC folder can hold `.m4a` encodes next to `.mp3` or `.ogg` copies. Into a lossy output it is copied unchanged when it already has that output's codec (`.mp3` into `mp3`, an AAC `.m4a` or an `.aac` into `aac`, `.opus` into `opus`) and transcoded otherwise. Copies keep their tags and cover as they are.

When a lossless and a lossy file share a name in the source folder, the lossless one is used for every output. If the lossy file came first and was already copied or transcoded, that file is replaced by an encode of the lossless one as soon as it is processed. When an ALAC `.m4a` shares a name with a FLAC or another lossless file that has its own extension, that other file is used, because the ALAC file is usually a copy of it. This also holds when the FLAC arrives after the ALAC file was encoded: its encode replaces the ALAC one. To tell those files apart, each output folder keeps a hidden `.atw-manifest.json` recording which source made each file and whether it was an encode, a copy or a transcode. Deleting it is safe: files it does not know about are treated as they were before 0.6.1.

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

FFmpeg writes the standard MP4 atoms (title, artist, album and so on). After an ALAC or AAC encode, the tags FFmpeg drops are copied from the source under the names MusicBrainz Picard uses, also when the source is an M4A file that already has them, so Picard, TagLib (Navidrome, Jellyfin), foobar2000 and iTunes all read them:

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

