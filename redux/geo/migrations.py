"""Sighting-store schema versioning + migrations."""
from __future__ import annotations

import sqlite3
from typing import Callable, Dict, List

SCHEMA_VERSION = 1


def get_user_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


def set_user_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(f"PRAGMA user_version = {int(version)}")


# version -> migration callable upgrading FROM previous TO this version
MIGRATIONS: Dict[int, Callable[[sqlite3.Connection], None]] = {
    1: lambda conn: None,  # baseline matches db.py schema; created by SightingStore
}


def migrate(conn: sqlite3.Connection, target: int = SCHEMA_VERSION) -> List[int]:
    """Apply migrations up to target. Returns list of versions applied."""
    applied: List[int] = []
    current = get_user_version(conn)
    for v in sorted(MIGRATIONS.keys()):
        if current < v <= target:
            MIGRATIONS[v](conn)
            set_user_version(conn, v)
            conn.commit()
            applied.append(v)
            current = v
    return applied


def ensure_schema(conn: sqlite3.Connection) -> int:
    """Run migrations; return resulting user_version."""
    migrate(conn)
    return get_user_version(conn)
