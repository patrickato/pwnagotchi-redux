from redux.geo import Sighting, SightingStore
from redux.geo.filter_provenance import missing_provenance, with_provenance


def test_prov_filter():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=1.0, provenance="ok"))
        # empty provenance may be stored as "" depending on schema
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", ts=1.0, provenance=""))
        assert len(with_provenance(db)) >= 1
        assert all(s.provenance for s in with_provenance(db))
