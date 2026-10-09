from redux.geo import Sighting, SightingStore
from redux.geo.latest_n import latest_n


def test_latest():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", ts=9.0, provenance="t"))
        assert latest_n(db, 1)[0].mac.startswith("bb")

def test_latest_n_is_sql_limited_and_handles_large_cache(monkeypatch):
    with SightingStore(":memory:") as db:
        db.insert_many([
            Sighting(kind="wifi" if i % 2 else "ble", mac=f"{i:012x}",
                     ts=float(i), provenance="test")
            for i in range(2000)
        ])
        original = db.query
        limits = []
        def bounded_query(*args, **kwargs):
            limits.append(kwargs.get("limit"))
            assert kwargs.get("limit") is not None
            return original(*args, **kwargs)
        monkeypatch.setattr(db, "query", bounded_query)
        assert [int(row.mac, 16) for row in latest_n(db, 3)] == [1999, 1998, 1997]
        assert [int(row.mac, 16) for row in latest_n(db, 2, kind="wifi")] == [1999, 1997]
        assert latest_n(db, 0) == []
        assert limits == [3, 2]
