from redux.geo import Sighting, SightingStore
from redux.geo.densest_cells import densest_cells


def test_densest():
    with SightingStore(":memory:") as db:
        for i in range(5):
            db.insert(Sighting(kind="wifi", mac=f"aa:aa:aa:aa:aa:{i:02x}", lat=1.001, lon=2.001, ts=float(i), provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", lat=9.0, lon=9.0, ts=9.0, provenance="t"))
        cells = densest_cells(db, cell_deg=0.01, limit=2)
        assert cells and cells[0].count == 5
