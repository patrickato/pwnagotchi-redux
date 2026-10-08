from redux.geo import Sighting, SightingStore
from redux.geo.prune_helper import prune_older_than


def test_prune():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=10.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", ts=100.0, provenance="t"))
        # now=100, age_s=50 → cutoff=50 → deletes ts=10
        r = prune_older_than(db, age_s=50.0, now=100.0)
        assert r["deleted"] == 1
        assert db.count() == 1
