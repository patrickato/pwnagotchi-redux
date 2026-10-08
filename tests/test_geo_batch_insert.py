from redux.geo import Sighting, SightingStore
from redux.geo.batch_insert import batch_insert


def test_batch():
    with SightingStore(":memory:") as db:
        n = batch_insert(
            db,
            [
                Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=1.0, provenance="t"),
                Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", ts=2.0, provenance="t"),
            ],
        )
        assert n == 2 and db.count() == 2
