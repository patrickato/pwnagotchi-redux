"""WiGLE incremental export queue.

Remembers which (kind, mac) keys have already been packaged so each export
contains only *new* rows (G-9). The store itself already dedups by best-RSSI /
first-seen; this queue avoids re-uploading the same BSSID on every cycle.

No network I/O — packaging only. Actual WiGLE upload stays opt-in and outside
this module (repo rule: nothing phones home uninvited).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple, Union

from redux.geo.db import Sighting, SightingStore
from redux.geo.wigle import write_wigle_csv

PathLike = Union[str, Path]
Key = Tuple[str, str]  # (kind, mac)


class WigleExportQueue:
    """Pending-vs-exported bookkeeping for incremental WiGLE CSV packages."""

    def __init__(self, state_path: PathLike = ":memory:") -> None:
        self.state_path = str(state_path)
        self._conn = sqlite3.connect(self.state_path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS exported (
                kind TEXT NOT NULL,
                mac  TEXT NOT NULL,
                exported_ts REAL NOT NULL,
                PRIMARY KEY (kind, mac)
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "WigleExportQueue":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _exported_keys(self) -> Set[Key]:
        rows = self._conn.execute("SELECT kind, mac FROM exported").fetchall()
        return {(k, m) for k, m in rows}

    def pending(self, store: SightingStore, *, kind: Optional[str] = None) -> List[Sighting]:
        """Sightings in the store not yet marked exported."""
        done = self._exported_keys()
        rows = store.query(kind=kind) if kind else store.query()
        out = [s for s in rows if (s.kind, s.mac) not in done]
        return sorted(out, key=lambda s: (s.first_seen or s.ts, s.mac))

    def pending_count(self, store: SightingStore, *, kind: Optional[str] = None) -> int:
        return len(self.pending(store, kind=kind))

    def package(
        self,
        store: SightingStore,
        dest: Union[str, object],
        *,
        kind: Optional[str] = None,
        app_release: str = "redux-geo",
        mark: bool = True,
    ) -> int:
        """Write a WiGLE CSV of pending rows only. Optionally mark them exported.

        Returns the number of data rows written. If zero, no file side effects
        beyond an empty-capable write (headers only) when dest is a path — we
        still write headers so the artifact is a valid empty package.
        """
        rows = self.pending(store, kind=kind)
        n = write_wigle_csv(rows, dest, app_release=app_release)  # type: ignore[arg-type]
        if mark and rows:
            self.mark_exported(rows)
        return n

    def mark_exported(
        self,
        sightings: Iterable[Sighting],
        *,
        exported_ts: Optional[float] = None,
    ) -> int:
        """Record keys as exported. Returns number of new keys recorded."""
        import time

        ts = time.time() if exported_ts is None else float(exported_ts)
        n = 0
        for s in sightings:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO exported (kind, mac, exported_ts) VALUES (?, ?, ?)",
                (s.kind, s.mac, ts),
            )
            n += cur.rowcount
        self._conn.commit()
        return n

    def reset_exported(self) -> None:
        """Clear export history (forces full re-package next time)."""
        self._conn.execute("DELETE FROM exported")
        self._conn.commit()

    def exported_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM exported").fetchone()
        return int(row[0]) if row else 0
