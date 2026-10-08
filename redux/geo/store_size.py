"""Report on-disk size of a file-backed sighting store."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from redux.geo.db import SightingStore


def store_file_size(store: SightingStore) -> Optional[int]:
    """Return bytes on disk, or None for :memory: / missing path."""
    path = getattr(store, "path", None)
    if not path or path == ":memory:":
        return None
    p = Path(path)
    if not p.exists():
        return None
    return p.stat().st_size


def store_size_summary(store: SightingStore) -> dict:
    size = store_file_size(store)
    count = store.count()
    if size is None:
        reason = f"store size: in-memory or missing path, rows={count}"
    else:
        reason = f"store size: {size} bytes on disk, rows={count}"
    return {"bytes": size, "rows": count, "reason": reason}
