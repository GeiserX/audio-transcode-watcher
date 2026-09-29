# How it works

1. **Initial sync** -- On startup, scans the source folder and encodes any missing files to all configured outputs.
2. **Watch mode** -- Continuously monitors the source folder for changes:
   - **New files** are encoded to all configured outputs
   - **Modified files** are re-encoded to all outputs
   - **Renamed files** trigger deletion of old outputs and creation of new ones
   - **Deleted files** have their corresponding outputs removed
3. **Orphan cleanup** -- Removes output files that no longer have a matching source.
4. **Lyrics sync** -- Fetches synced `.lrc` lyrics from online databases; falls back to Whisper transcription when no lyrics are found.

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
- All writes are atomic -- encoding happens to a temporary file that is moved into place only on success

## Performance

- **Parallel processing** -- Multiple output formats are encoded concurrently
- **Incremental sync** -- Only missing or changed files are processed; unchanged files are skipped
- **Stability detection** -- Files are not processed until they have been stable on disk for a configurable period, avoiding partial reads during large copies or network transfers
- **Low idle footprint** -- Uses inotify/FSEvents-based watching with minimal CPU usage when idle

To check that every output matches the source, see [Usage](usage.md).
