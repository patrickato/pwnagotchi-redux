"""Hardware-free tests for store maintenance (Grok GG10)."""
from redux.geo import Sighting, SightingStore
from redux.geo.maintenance import ensure_indexes, integrity_check, maintain, vacuum


def test_integrity_ok():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=1.0, provenance="t"))
        assert integrity_check(db) == "ok"


def test_ensure_indexes():
    with SightingStore(":memory:") as db:
        applied = ensure_indexes(db)
        assert len(applied) >= 1


def test_maintain_summary():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=1.0, provenance="t"))
        r = maintain(db)
        assert r["integrity"] == "ok"
        assert r["count"] == 1
        assert "maintenance" in r["reason"]


def test_vacuum_runs():
    with SightingStore(":memory:") as db:
        vacuum(db)
