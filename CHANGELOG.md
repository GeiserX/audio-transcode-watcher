# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [0.6.1] - 2026-10-04

### Added

- Per-output `channels` and `max_sample_rate`. An `aac` output now defaults to `channels: 2` and `max_sample_rate: 48000`, so the AAC copies play everywhere a phone, AirPods or CarPlay can take them: anything with more than two channels is downmixed to stereo, 88.2 and 176.4 kHz sources become 44.1 kHz, and 96 and 192 kHz sources become 48 kHz. Nothing is ever upsampled or upmixed. Other codecs have no limit unless they set one, and `0` turns a limit off. ALAC is unchanged.

### Fixed

- A lossless file that arrives after a lossy one of the same name now replaces the lossy file's transcode too, not only its copy. An Ogg transcoded into an MP3 output as `X.mp3` used to block the later `X.flac` from ever being encoded there. Each output folder now keeps `.atw-manifest.json`, recording which source made each file and how (encode, copy or transcode). A missing or unreadable manifest means "unknown", which behaves exactly as before, and the orphan pass drops rows whose file is gone.

### Upgrading

- Existing AAC files are not rebuilt by the upgrade. They are re-encoded only when their source changes, or with `force_reencode: true` on startup. To rebuild just the hi-res and multichannel ones, delete the AAC files above 48 kHz or 2 channels and the next periodic sync encodes them again: `docker exec audio_transcoder find /music/aac -name '*.m4a' -exec sh -c 'ffprobe -v error -select_streams a:0 -show_entries stream=sample_rate,channels -of default=nw=1 "$1" | awk -F= "/^sample_rate/{r=\$2} /^channels/{c=\$2} END{exit !(r>48000||c>2)}" && rm -v "$1"' _ {} \;`

### Security

- `urllib3` is now required at 2.8.0 or newer, for GHSA-vxq7-64xx-v4gw, GHSA-8988-9cw3-xx77 and GHSA-gh4c-6fx4-qh6g. It comes in through syncedlyrics and requests; the image installs from `pyproject.toml`, so the floor is declared there and `uv.lock` resolves 2.8.0.

## [0.6.0] - 2026-10-04

### Changed

- Lossy sources are copied, not encoded. An MP3, M4A, AAC, Ogg, Opus or WMA file in the source folder lands in a lossless output (ALAC, FLAC, WAV) as an unchanged copy with its own extension. Before, everything except MP3 was encoded to ALAC, which turned a lossy file into a lossless-sized one that only looked lossless.
- Into a lossy output, a lossy source is copied unchanged when it already has that output's codec (an MP3 into an MP3 output, an M4A into an AAC output). It is transcoded only when the codecs differ. That is the one lossy-to-lossy encode left.
- Ogg and Opus are now treated as lossy. AIF and TAK are now recognised as lossless sources, and WMA as a lossy one. Files with these extensions used to be ignored.
- When a lossless and a lossy file share a name in the source folder, the lossless file wins in every output. If the lossy file arrived first, its copy is removed and the lossless file is encoded when it is processed, not left behind until an orphan pass.
- The periodic full sync logs as "Periodic sync" instead of "Initial sync", so the logs no longer read like restarts. Its interval is the new setting `settings.sync_interval_seconds` (default 300).
- A lyrics file written next to a source now gets the source file's owner and group and mode 0664, instead of root and 0644.

### Added

- ALAC and AAC outputs keep the tags the standard MP4 atoms have no room for: ReplayGain track and album gain and peak, the MusicBrainz track, album, artist, album artist and release group ids, ISRC, label, catalog number, and the artist, album artist and album sort names. They use the atom names MusicBrainz Picard writes (`----:com.apple.iTunes:replaygain_track_gain`, `----:com.apple.iTunes:MusicBrainz Track Id`, `ISRC`, `LABEL`, `CATALOGNUMBER`), so Picard, TagLib, foobar2000 and iTunes all read them, and the sort names go to the standard `soar`, `soaa` and `soal` atoms. FLAC, Ogg, APE, WavPack, TAK, WAV and AIFF sources are read.
- `CHANGELOG.md`.

### Removed

- The Whisper lyrics fallback, and with it the `openai-whisper` dependency (and PyTorch), which makes the image much smaller. Old configs that still set `whisper_fallback` or `whisper_model` load fine; the first load logs one warning that the keys are ignored.

### Fixed

- A source that does not decode cleanly now fails instead of producing a short copy. FFmpeg runs with `-xerror`, and a decode error on its stderr counts as a failure even when it exits 0. A corrupt FLAC used to come out two seconds short with nothing in the logs. Now the error is logged with the file name, no output is written, and the file is not tried again until its modification time changes.
- FFmpeg also checks every frame checksum of the input (`-err_detect crccheck+explode`). Without it, the FFmpeg 7.1 in the image decoded a FLAC with a single flipped bit without a word.
- An ffmpeg run that takes longer than 30 minutes is killed and counts as a failure, so one hung encode can no longer stall every later sync.
- The retry without cover art only runs when the error is about the picture stream. It used to run on any error that mentioned "decode".
- The periodic sync could delete an output the watcher had just written, because it judged orphans from a source list built before the file arrived. The orphan pass now reads the source folder again, and it leaves alone any file younger than 120 seconds or whose source is being processed.
- Startup and periodic cleanup no longer delete `.tmp__ff` files younger than 10 minutes, which could belong to an encode still running.
- Lyrics: when the providers return nothing, nothing is written. Results made of one repeated token, with fewer than 4 timed lines, or with only an advertisement line are rejected, and the reason is logged.
- Deleting one of two sources that share a name (for example the MP3 next to a FLAC) no longer deletes the `.lrc` copies the other one still needs.
- Lossy copies are written through a temp file and renamed, like encodes, so a half-written copy is never visible.
- `force_reencode: true` purges the outputs once at startup. It used to purge them again on every periodic sync, which re-encoded the whole library every five minutes.

[0.6.1]: https://github.com/GeiserX/audio-transcode-watcher/releases/tag/v0.6.1
[0.6.0]: https://github.com/GeiserX/audio-transcode-watcher/releases/tag/v0.6.0
