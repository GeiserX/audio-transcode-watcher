# Roadmap

Current version: **0.5.0**

## Completed

- **Parallel encoding** with configurable workers (v0.1.0)
- **Lossless source priority** over MP3 duplicates (v0.1.1)
- **Stale temp file cleanup** on startup (v0.1.2)
- **Synced lyrics sidecar** (.lrc) copying to outputs (v0.2.0)
- **Auto-fetch synced lyrics** via syncedlyrics providers (v0.3.0)
- **Whisper local transcription fallback** for lyrics (v0.4.0)
- **Recursive directory support** -- mirror source folder hierarchy in outputs (v0.5.0)

## v0.6.0 -- Quality of Life

- [ ] **Progress reporting during initial sync**
  - Show file count progress (e.g. 42/500)
  - Log estimated time remaining for large libraries

- [ ] **Graceful shutdown**
  - Complete in-progress encodes before stopping
  - Resume interrupted syncs on next startup

- [ ] **Dry-run mode**
  - Preview what would be encoded without doing it
  - Useful for testing configuration changes

## v0.7.0 -- Observability

- [ ] **Health check endpoint**
  - HTTP endpoint for container health monitoring
  - Prometheus metrics (files processed, errors, queue size)

- [ ] **Notifications**
  - Webhook/Shoutrrr support for encoding events
  - Error alerts for failed encodes

## v0.8.0 -- Smart Encoding

- [ ] **FFmpeg multi-output (tee muxer)**
  - Single decode pass, multiple encodes
  - Significant CPU reduction for multi-output configurations

- [ ] **Checksum-based re-encoding**
  - Detect actual content changes vs. metadata-only touches
  - Skip re-encode when source content is unchanged

- [ ] **Replay gain support**
  - Calculate and embed replay gain tags
  - Album and track gain modes

## Future Ideas (Backlog)

### Format Support
- [ ] Ogg Vorbis output codec
- [ ] DSD/DSF input support (convert to PCM first)
- [ ] Multi-channel audio handling (5.1 to stereo downmix option)

### Metadata
- [ ] MusicBrainz integration for missing metadata
- [ ] Chapter markers for audiobooks

### Integration
- [ ] Plex/Jellyfin library refresh triggers
- [ ] Lidarr/Beets integration

### Analysis
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
