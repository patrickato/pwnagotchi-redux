"""Sighting-store maintenance: indexes, vacuum, integrity check."""
from __future__ import annotations

import sqlite3
from typing import List

from redux.geo.db import SightingStore

_EXTRA_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_sightings_mac ON sightings(mac)",
    "CREATE INDEX IF NOT EXISTS idx_sightings_ssid ON sightings(ssid)",
    "CREATE INDEX IF NOT EXISTS idx_sightings_first_seen ON sightings(first_seen)",
]


def ensure_indexes(store: SightingStore) -> List[str]:
    """Create helpful secondary indexes. Returns SQL applied."""
    applied: List[str] = []
    conn: sqlite3.Connection = store._conn  # intentional: maintenance helper
    for sql in _EXTRA_INDEXES:
        conn.execute(sql)
        applied.append(sql)
    conn.commit()
    return applied


def integrity_check(store: SightingStore) -> str:
    """Run PRAGMA integrity_check; returns 'ok' or error detail."""
    row = store._conn.execute("PRAGMA integrity_check").fetchone()
    return str(row[0]) if row else "unknown"


def vacuum(store: SightingStore) -> None:
    """Rebuild the database file, reclaiming free pages."""
    store._conn.execute("VACUUM")


def maintain(store: SightingStore) -> dict:
    """Run indexes + integrity + vacuum; glass-box summary."""
    idxs = ensure_indexes(store)
    integrity = integrity_check(store)
    vacuum(store)
    return {
        "indexes_applied": len(idxs),
        "integrity": integrity,
        "count": store.count(),
        "reason": (
            f"maintenance: {len(idxs)} indexes, integrity={integrity}, "
            f"rows={store.count()}"
        ),
    }
