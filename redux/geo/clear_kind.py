"""Delete all sightings of a given kind from the store."""
from __future__ import annotations

from redux.geo.db import SightingStore


def clear_kind(store: SightingStore, kind: str) -> dict:
    kind = (kind or "").lower().strip()
    if not kind:
        return {"deleted": 0, "reason": "clear_kind: empty kind, nothing deleted"}
    before = store.count(kind=kind)
    store._conn.execute("DELETE FROM sightings WHERE kind = ?", (kind,))
    store._conn.commit()
    after = store.count(kind=kind)
    deleted = before - after
    return {
        "deleted": deleted,
        "kind": kind,
        "reason": f"clear_kind: removed {deleted} row(s) of kind={kind}",
    }
