"""Per-output provenance: which source, and how, produced each output file.

Each output folder holds ``.atw-manifest.json``, mapping an output file's
path (relative to the output folder) to the source that produced it::

    {"Artist/X.mp3": {"source": "Artist/X.ogg", "size": 4123456,
                      "mtime": 1790000000.0, "kind": "transcode"}}

``kind`` is ``"encode"`` (from a lossless source), ``"copy"`` (a lossy source
copied unchanged) or ``"transcode"`` (a lossy source re-encoded to another
lossy codec). The manifest is advisory: a missing or unreadable one means
"unknown", and every caller then behaves as if it did not exist.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time

from .utils import nfc_path

logger = logging.getLogger(__name__)

MANIFEST_NAME = ".atw-manifest.json"

# Seconds between writes of a manifest that keeps changing. Rows recorded
# since the last write are lost if the process dies; that only means
# "unknown" for those files, never a wrong answer.
FLUSH_INTERVAL = 5.0

_lock = threading.Lock()
_rows: dict[str, dict[str, dict]] = {}  # output root -> rows
_dirty: set[str] = set()
_last_write: dict[str, float] = {}


def _key(root: str, out_path: str) -> str:
    return nfc_path(os.path.relpath(out_path, root))


def _load(root: str) -> dict[str, dict]:
    """Rows for *root*, read once per process. Call with _lock held."""
    rows = _rows.get(root)
    if rows is not None:
        return rows
    rows = {}
    path = os.path.join(root, MANIFEST_NAME)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            rows = {k: v for k, v in data.items() if isinstance(v, dict)}
        else:
            logger.warning("Ignoring manifest %s: not a JSON object", path)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        logger.warning("Ignoring unreadable manifest %s: %s", path, e)
    _rows[root] = rows
    return rows


def lookup(root: str, out_path: str) -> dict | None:
    """The row for *out_path* in the output folder *root*, or None if unknown."""
    with _lock:
        return _load(root).get(_key(root, out_path))


def record(
    root: str, out_path: str, source_root: str, source_path: str, kind: str
) -> None:
    """Remember that *source_path* produced *out_path* (written on the next flush)."""
    try:
        st = os.stat(source_path)
        size, mtime = st.st_size, st.st_mtime
    except OSError:
        size, mtime = None, None
    row = {
        "source": nfc_path(os.path.relpath(source_path, source_root)),
        "size": size,
        "mtime": mtime,
        "kind": kind,
    }
    with _lock:
        _load(root)[_key(root, out_path)] = row
        _dirty.add(root)


def prune(root: str) -> int:
    """Drop rows whose output file is gone. Returns how many were dropped."""
    with _lock:
        rows = _load(root)
        gone = [k for k in rows if not os.path.exists(os.path.join(root, k))]
        for k in gone:
            del rows[k]
        if gone:
            _dirty.add(root)
    return len(gone)


def flush(root: str, force: bool = False) -> None:
    """Write *root*'s manifest if it changed (at most every FLUSH_INTERVAL s)."""
    with _lock:
        if root not in _dirty:
            return
        now = time.monotonic()
        if not force and now - _last_write.get(root, 0.0) < FLUSH_INTERVAL:
            return
        path = os.path.join(root, MANIFEST_NAME)
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(_rows.get(root, {}), f, ensure_ascii=False, sort_keys=True)
            os.replace(tmp, path)
        except OSError as e:
            logger.warning("Could not write manifest %s: %s", path, e)
            return
        _dirty.discard(root)
        _last_write[root] = now


def flush_all(force: bool = False) -> None:
    """Flush every manifest that changed."""
    with _lock:
        roots = list(_dirty)
    for root in roots:
        flush(root, force=force)


def forget(root: str) -> None:
    """Drop what is cached for *root* (after its files were purged)."""
    with _lock:
        _rows.pop(root, None)
        _dirty.discard(root)
        _last_write.pop(root, None)
