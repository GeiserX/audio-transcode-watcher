# Usage

Once the container runs there is nothing to start by hand: it syncs on startup, then follows the source folder (see [How it works](how-it-works.md)). Put new files in the source folder, and each output gets its copy in the same Artist/Album path.

## Checking that the outputs are in sync

A built-in verification tool checks that all outputs are in sync with the source:

```bash
# Basic sync check
docker exec audio_transcoder python /app/tools/verify_sync.py --config /app/config.yaml

# Thorough check including duration comparison
docker exec audio_transcoder python /app/tools/verify_sync.py --config /app/config.yaml --check-duration -v
```

