"""Hardware-free tests for schema migrations (Grok G8)."""
import sqlite3

from redux.geo.migrations import SCHEMA_VERSION, ensure_schema, get_user_version, migrate


def test_migrate_sets_version():
    conn = sqlite3.connect(":memory:")
    assert get_user_version(conn) == 0
    applied = migrate(conn)
    assert SCHEMA_VERSION in applied or get_user_version(conn) == SCHEMA_VERSION
    assert ensure_schema(conn) == SCHEMA_VERSION
    conn.close()


def test_idempotent():
    conn = sqlite3.connect(":memory:")
    migrate(conn)
    assert migrate(conn) == []
    conn.close()
