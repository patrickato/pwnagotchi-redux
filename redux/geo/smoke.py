"""Lightweight smoke checks for a sighting store (no hardware)."""
from __future__ import annotations

from redux.geo.db import Sighting, SightingStore


def smoke_roundtrip(store: SightingStore | None = None) -> dict:
    """Insert/query/dedup smoke; uses :memory: if store is None."""
    own = store is None
    db = store if store is not None else SightingStore(":memory:")
    try:
        db.insert(
            Sighting(
                kind="wifi",
                mac="de:ad:be:ef:00:01",
                ssid="smoke",
                rssi=-55,
                ts=1.0,
                provenance="smoke test",
            )
        )
        db.insert(
            Sighting(
                kind="wifi",
                mac="de:ad:be:ef:00:01",
                ssid="smoke",
                rssi=-40,
                ts=2.0,
                provenance="smoke test stronger",
            )
        )
        row = db.get("wifi", "de:ad:be:ef:00:01")
        ok = row is not None and row.rssi == -40 and db.count() >= 1
        return {
            "ok": ok,
            "count": db.count(),
            "rssi": row.rssi if row else None,
            "reason": (
                "smoke ok: insert/dedup/query" if ok else "smoke failed: unexpected store state"
            ),
        }
    finally:
        if own and hasattr(db, "close"):
            db.close()
