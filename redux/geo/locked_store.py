"""Thread-safe wrapper around SightingStore (stdlib threading.Lock)."""
from __future__ import annotations

import threading
from typing import List, Optional

from redux.geo.db import Sighting, SightingStore


class LockedSightingStore:
    """Serialize insert/query/get/count on an underlying SightingStore."""

    def __init__(self, store: SightingStore) -> None:
        self._store = store
        self._lock = threading.RLock()

    def insert(self, sighting: Sighting) -> None:
        with self._lock:
            self._store.insert(sighting)

    def get(self, kind: str, mac: str) -> Optional[Sighting]:
        with self._lock:
            return self._store.get(kind, mac)

    def query(self, **kwargs) -> List[Sighting]:
        with self._lock:
            return self._store.query(**kwargs)

    def count(self) -> int:
        with self._lock:
            return self._store.count()

    def close(self) -> None:
        with self._lock:
            if hasattr(self._store, "close"):
                self._store.close()

    def __enter__(self) -> "LockedSightingStore":
        return self

    def __exit__(self, *args) -> None:
        self.close()
