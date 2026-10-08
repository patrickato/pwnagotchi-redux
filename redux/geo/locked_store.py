"""Thread-safe wrapper around SightingStore (stdlib threading.Lock)."""
from __future__ import annotations

import sqlite3
import threading
from typing import List, Optional, Union

from redux.geo.db import Sighting, SightingStore

PathLike = Union[str, "os.PathLike[str]"]


class LockedSightingStore:
    """Serialize insert/query/get/count.

    Opens (or wraps) a SightingStore whose sqlite connection allows
    cross-thread use; all public methods take an RLock so callers never
    race the connection.
    """

    def __init__(self, store: Optional[SightingStore] = None, path: str = ":memory:") -> None:
        if store is not None:
            self._store = store
            # Force cross-thread use on the existing connection
            conn = self._store._conn
            conn.isolation_level = conn.isolation_level  # touch
            try:
                # sqlite3 Connection has check_same_thread as init-only on some versions;
                # replace connection if needed.
                self._store._conn = sqlite3.connect(
                    self._store.path if getattr(self._store, "path", None) else path,
                    check_same_thread=False,
                )
                self._store._conn.row_factory = sqlite3.Row
                # re-apply schema
                from redux.geo.db import _SCHEMA

                self._store._conn.executescript(_SCHEMA)
                self._store._conn.commit()
            except Exception:
                pass
        else:
            self._store = SightingStore(path)
            self._store._conn.close()
            self._store._conn = sqlite3.connect(path, check_same_thread=False)
            self._store._conn.row_factory = sqlite3.Row
            from redux.geo.db import _SCHEMA

            self._store._conn.executescript(_SCHEMA)
            self._store._conn.commit()
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
