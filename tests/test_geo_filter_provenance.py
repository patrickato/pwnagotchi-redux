from redux.geo import Sighting, SightingStore
from redux.geo.filter_provenance import with_provenance


def test_prov_filter():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=1.0, provenance="ok"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", ts=1.0, provenance="other"))
        hits = with_provenance(db)
        assert len(hits) == 2
        assert all(s.provenance for s in hits)
