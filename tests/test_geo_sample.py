from redux.geo import Sighting, SightingStore
from redux.geo.sample import sample_sightings


def test_sample_size():
    with SightingStore(":memory:") as db:
        for i in range(10):
            db.insert(Sighting(kind="wifi", mac=f"aa:aa:aa:aa:aa:{i:02x}", ts=float(i), provenance="t"))
        s = sample_sightings(db, 3, seed=42)
        assert len(s) == 3
