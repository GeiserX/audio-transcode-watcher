<p align="center">
  <img src="https://raw.githubusercontent.com/GeiserX/audio-transcode-watcher/main/docs/images/banner.svg" alt="audio-transcode-watcher" width="900" />
</p>

<p align="center">
  <strong>A containerized service that watches a source folder and automatically transcodes audio files to multiple formats simultaneously.</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/audio-transcode-watcher/"><img src="https://img.shields.io/pypi/v/audio-transcode-watcher?style=flat-square" alt="PyPI" /></a>
  <a href="https://github.com/GeiserX/audio-transcode-watcher/actions/workflows/tests.yml"><img src="https://github.com/GeiserX/audio-transcode-watcher/actions/workflows/tests.yml/badge.svg" alt="Tests" /></a>
  <a href="https://github.com/GeiserX/audio-transcode-watcher/blob/main/LICENSE"><img src="https://img.shields.io/github/license/GeiserX/audio-transcode-watcher" alt="License" /></a>
  <a href="https://hub.docker.com/r/drumsergio/audio-transcoder"><img src="https://img.shields.io/docker/pulls/drumsergio/audio-transcoder" alt="Docker Pulls" /></a>
</p>

Keep one library in several formats: lossless for the archive, lossy for phones and cars. Drop a FLAC into the source folder and the ALAC, MP3, AAC and Opus copies appear in their own trees.

## Features

- Watches the source folder and reacts to new, changed, renamed and deleted files.
- Writes any number of outputs in one pass, in ALAC, AAC, MP3, Opus, FLAC or WAV at the bitrate you pick.
- Mirrors the Artist/Album folder tree in every output.
- Copies lossy sources (MP3, M4A, Ogg, Opus, WMA) unchanged instead of inflating them into a lossless output.
- Fails loudly on a source that does not decode cleanly, and writes no output for it.
- Keeps ReplayGain, MusicBrainz ids, ISRC, label and sort tags in ALAC and AAC copies.
- Fetches synced `.lrc` lyrics, and writes nothing when no real lyrics are found.
- Embeds cover art if you want it.
- Writes atomically and removes orphaned outputs, but never deletes anything when a folder looks empty.
- Handles Unicode file names.
- Ships as a small container on Python 3.14-slim with FFmpeg.

## Quick start

```bash
curl -fsSL -o config.yaml https://raw.githubusercontent.com/GeiserX/audio-transcode-watcher/main/config.example.yaml
docker run -d --name audio_transcoder -e CONFIG_FILE=/app/config.yaml \
  -v ./config.yaml:/app/config.yaml:ro -v /path/to/flac:/music/flac:ro -v /path/to/mp3:/music/mp3 \
  drumsergio/audio-transcoder:0.6.2
```

Edit `config.yaml` first so its outputs match the folders you mount; the example writes ALAC, MP3 and AAC. [Getting started](https://github.com/GeiserX/audio-transcode-watcher/blob/main/docs/getting-started.md) has Docker Compose and the full `docker run`.

## Documentation

- [Getting started](https://github.com/GeiserX/audio-transcode-watcher/blob/main/docs/getting-started.md): Docker Compose and Docker CLI
- [Configuration](https://github.com/GeiserX/audio-transcode-watcher/blob/main/docs/configuration.md): the full YAML example, supported codecs, `CONFIG_JSON`
- [Usage](https://github.com/GeiserX/audio-transcode-watcher/blob/main/docs/usage.md): what happens once it runs, and the verification tool
- [How it works](https://github.com/GeiserX/audio-transcode-watcher/blob/main/docs/how-it-works.md): sync and watch mode, folder mirroring, safety guards, performance
- [Development](https://github.com/GeiserX/audio-transcode-watcher/blob/main/docs/development.md): tests, building the image, contributing
- [Roadmap](https://github.com/GeiserX/audio-transcode-watcher/blob/main/docs/ROADMAP.md)
- [Changelog](https://github.com/GeiserX/audio-transcode-watcher/blob/main/CHANGELOG.md)

## Related projects

| Project | Description |
|---------|-------------|
| [slskd-transform](https://github.com/GeiserX/slskd-transform) | Bulk upgrade your music library from lossy to lossless via Soulseek |
| [telegram-slskd-local-bot](https://github.com/GeiserX/telegram-slskd-local-bot) | Automated music discovery and download via Telegram |
| [quality-gate-encoder](https://github.com/GeiserX/quality-gate-encoder) (formerly jellyfin-encoder) | Automatic 720p HEVC, H.264 or AV1 copies for Jellyfin |

## License

[GPL-3.0-or-later](https://github.com/GeiserX/audio-transcode-watcher/blob/main/LICENSE)
