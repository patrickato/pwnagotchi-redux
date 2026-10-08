from redux.geo import Sighting, SightingStore
from redux.geo.provenance_index import provenance_prefix_counts, search_provenance


def test_search_and_prefix():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=1.0, provenance="kismetdb import from x"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", ts=1.0, provenance="wigle csv import"))
        hits = search_provenance(db, "kismet")
        assert len(hits) == 1
        prefixes = provenance_prefix_counts(db)
        assert prefixes.get("kismetdb") == 1
