# Roadmap

Future improvements and features for audio-transcode-watcher.

## v0.2.0 - Quality of Life

- [ ] **Progress reporting during initial sync**
  - Show progress bar or percentage during bulk encoding
  - Estimated time remaining

- [ ] **Health check endpoint**
  - HTTP endpoint for container health monitoring
  - Prometheus metrics (files processed, errors, queue size)

- [ ] **Graceful shutdown**
  - Complete in-progress encodes before stopping
  - Resume interrupted syncs

## v0.3.0 - Advanced Features

- [ ] **Folder structure preservation**
  - Option to mirror source folder hierarchy in outputs
  - Support for Artist/Album/Track structure

- [ ] **Quality profiles**
  - Pre-defined quality presets (high, medium, low)
  - Per-folder quality overrides

- [ ] **Smart re-encoding**
  - Detect source file changes (checksum-based)
  - Only re-encode when source actually changed, not just touched

- [ ] **Replay gain support**
  - Calculate and embed replay gain tags
  - Album and track gain modes

## v0.4.0 - Performance & Scalability

- [ ] **FFmpeg multi-output**
  - Single decode, multiple encodes (FFmpeg tee muxer)
  - Reduces CPU usage for multiple outputs from same source

- [ ] **Distributed encoding**
  - Queue-based architecture (Redis/RabbitMQ)
  - Multiple worker nodes for large libraries

- [ ] **GPU acceleration**
  - NVENC/VAAPI support for compatible codecs
  - Hardware-accelerated AAC encoding

## v0.5.0 - User Experience

- [ ] **Web UI dashboard**
  - Real-time encoding status
  - Queue management
  - Configuration editor

- [ ] **Notifications**
  - Webhook support for encoding events
  - Discord/Telegram/Email notifications
  - Error alerts

- [ ] **Dry-run mode**
  - Preview what would be encoded without doing it
  - Useful for testing configuration changes

## Future Ideas (Backlog)

### Format Support
- [ ] Ogg Vorbis output codec
- [ ] WavPack input support
- [ ] DSD/DSF input support (convert to PCM first)
- [ ] Multi-channel audio handling (5.1 → stereo downmix option)

### Metadata
- [ ] Lyrics preservation (embedded and .lrc sidecar)
- [ ] MusicBrainz integration for missing metadata
- [ ] Custom metadata mapping rules
- [ ] Chapter markers for audiobooks

### Integration
- [ ] Plex/Jellyfin library refresh triggers
- [ ] Lidarr/Beets integration
- [ ] S3/MinIO source and destination support
- [ ] SFTP/SMB remote sources

### Analysis
- [ ] Audio quality analysis (clipping detection)
- [ ] Loudness normalization (EBU R128)
- [ ] Silence trimming option
- [ ] Duplicate detection

## Contributing

Have an idea not listed here? Open an issue on GitHub to discuss!

Priority is given to:
1. Bug fixes
2. Performance improvements
3. Features with clear use cases
4. Backwards-compatible changes

## Versioning

This project follows [Semantic Versioning](https://semver.org/):
- **MAJOR**: Breaking changes to config format or behavior
- **MINOR**: New features, backwards compatible
- **PATCH**: Bug fixes, performance improvements
