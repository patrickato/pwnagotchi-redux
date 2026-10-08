from redux.geo import Sighting, SightingStore
from redux.geo.iter_chunks import iter_chunks


def test_chunks():
    with SightingStore(":memory:") as db:
        for i in range(5):
            db.insert(Sighting(kind="wifi", mac=f"aa:aa:aa:aa:aa:{i:02x}", ts=float(i), provenance="t"))
        chunks = list(iter_chunks(db, chunk_size=2))
        assert len(chunks) == 3
        assert len(chunks[0]) == 2 and len(chunks[-1]) == 1
