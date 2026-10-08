from redux.geo import Sighting, SightingStore
from redux.geo.batch_insert import batch_insert


def test_batch():
    with SightingStore(":memory:") as db:
        rows = [
            Sighting(kind="wifi", mac=f"aa:aa:aa:aa:aa:{i:02x}", ts=float(i), provenance="t")
            for i in range(5)
        ]
        r = batch_insert(db, rows)
        assert r["inserted"] == 5 and db.count() == 5
