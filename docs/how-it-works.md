# How it works

1. **Initial sync** -- On startup, scans the source folder and encodes any missing files to all configured outputs. The same pass runs again every `sync_interval_seconds` (default 300) and logs as "Periodic sync".
2. **Watch mode** -- Continuously monitors the source folder for changes:
   - **New files** are encoded to all configured outputs
   - **Modified files** are re-encoded to all outputs
   - **Renamed files** trigger deletion of old outputs and creation of new ones
   - **Deleted files** have their corresponding outputs removed
3. **Orphan cleanup** -- Removes output files that no longer have a matching source. It reads the source folder again just before, and skips any file younger than 120 seconds or whose source is being processed, so a file that arrives during a long sync keeps its output.
4. **Lyrics sync** -- Fetches synced `.lrc` lyrics from online databases (LRCLIB and the other syncedlyrics providers). When nothing usable comes back, no file is written. A result made of one repeated token, with fewer than 4 timed lines, or with only an advertisement line is rejected and the reason is logged. The `.lrc` gets the source file's owner and mode 0664.

Lossy sources are copied rather than encoded where that keeps quality honest; see [Source formats](configuration.md#source-formats).

## Provenance manifest

Each output folder holds `.atw-manifest.json`, a map from each output file to the source that made it (path, size, modification time) and how: `encode`, `copy` or `transcode`. It is written in batches at most every 5 seconds and at the end of each sync. It is only used to replace a file made from a lossy source once a lossless source of the same name appears. A missing or unreadable manifest just means "unknown".

## Corrupt sources

FFmpeg runs with `-xerror` and `-err_detect crccheck+explode`, so a frame whose checksum does not match stops the encode. If a source does not decode cleanly, the encode fails even when FFmpeg exits 0 but printed a decode error. The error is logged with the file name and no output is written, unless `corrupt_source: encode_anyway` is set; then the file is encoded once more with FFmpeg's error concealment and marked `tolerant` in the manifest (see [Damaged sources](configuration.md#damaged-sources)). The strict attempt is not repeated until the file's modification time changes, or the service restarts.

## Recursive Directory Support

Source folder hierarchy is automatically mirrored in all outputs. Both flat and nested structures work out of the box -- no configuration needed.

```text
Source:                          Output (MP3):
/music/flac/                     /music/mp3/
├── Artist A/                    ├── Artist A/
│   ├── Album 1/                 │   ├── Album 1/
│   │   ├── 01 - Track.flac     │   │   ├── 01 - Track.mp3
│   │   └── 02 - Track.flac     │   │   └── 02 - Track.mp3
│   └── Album 2/                 │   └── Album 2/
│       └── 01 - Song.flac      │       └── 01 - Song.mp3
└── Artist B/                    └── Artist B/
    └── Live Album/                  └── Live Album/
        └── 01 - Intro.flac             └── 01 - Intro.mp3
```

When source files or directories are deleted, the corresponding outputs and empty directories are cleaned up automatically.

## Safety Guards

The service includes multiple guards to prevent data loss:

- If the source folder appears empty, no deletions are performed
- If any output folder appears empty, no deletions are performed
- All writes are atomic -- encoding and copying happen to a `.tmp__ff` file that is moved into place only on success
- Cleanup leaves `.tmp__ff` files younger than 10 minutes alone, since they may belong to an encode still running

## Performance

- **Parallel processing** -- Multiple output formats are encoded concurrently
- **Incremental sync** -- Only missing or changed files are processed; unchanged files are skipped
- **Stability detection** -- Files are not processed until they have been stable on disk for a configurable period, avoiding partial reads during large copies or network transfers
- **Low idle footprint** -- Uses inotify/FSEvents-based watching with minimal CPU usage when idle

To check that every output matches the source, see [Usage](usage.md).
