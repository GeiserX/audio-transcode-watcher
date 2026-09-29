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
  <a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="License: GPL v3" /></a>
</p>

Perfect for maintaining a music library in multiple formats -- lossless for archival, lossy for portable devices -- without lifting a finger. Drop a FLAC into your source folder and get ALAC, MP3, AAC, and Opus copies instantly.

## Features

- Watches the source folder and reacts to new, changed, renamed and deleted files.
- Writes any number of outputs in one pass, in ALAC, AAC, MP3, Opus, FLAC or WAV at the bitrate you pick.
- Mirrors the Artist/Album folder tree in every output.
- Fetches synced `.lrc` lyrics, with Whisper speech-to-text when none are found online.
- Embeds cover art if you want it.
- Writes atomically and removes orphaned outputs, but never deletes anything when a folder looks empty.
- Handles Unicode file names.
- Ships as a small container on Python 3.14-slim with FFmpeg.

## Quick start

Write a `config.yaml` with a source and your outputs ([full example](https://github.com/GeiserX/audio-transcode-watcher/blob/main/docs/configuration.md)), then:

```bash
docker run -d --name audio_transcoder -e CONFIG_FILE=/app/config.yaml \
  -v ./config.yaml:/app/config.yaml:ro -v /path/to/flac:/music/flac:ro -v /path/to/mp3:/music/mp3 \
  drumsergio/audio-transcoder:0.5.1
```

## Documentation

- [Installation](https://github.com/GeiserX/audio-transcode-watcher/blob/main/docs/installation.md): Docker Compose and Docker CLI
- [Configuration](https://github.com/GeiserX/audio-transcode-watcher/blob/main/docs/configuration.md): the full YAML example, supported codecs, `CONFIG_JSON`
- [How it works](https://github.com/GeiserX/audio-transcode-watcher/blob/main/docs/how-it-works.md): sync and watch mode, folder mirroring, safety guards, performance, the verification tool
- [Development](https://github.com/GeiserX/audio-transcode-watcher/blob/main/docs/development.md): tests, building the image, contributing
- [Roadmap](https://github.com/GeiserX/audio-transcode-watcher/blob/main/docs/ROADMAP.md)

## Related Music Tools

| Project | Description |
|---------|-------------|
| [slskd-transform](https://github.com/GeiserX/slskd-transform) | Bulk upgrade your music library from lossy to lossless via Soulseek |
| [telegram-slskd-local-bot](https://github.com/GeiserX/telegram-slskd-local-bot) | Automated music discovery and download via Telegram |
| [quality-gate-encoder](https://github.com/GeiserX/quality-gate-encoder) (formerly jellyfin-encoder) | Automatic 720p HEVC/AV1 transcoding for Jellyfin |

## License

GPL-3.0, see [LICENSE](https://github.com/GeiserX/audio-transcode-watcher/blob/main/LICENSE).
