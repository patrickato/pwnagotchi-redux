from redux.geo import Sighting, SightingStore
from redux.geo.rssi_histogram import rssi_histogram


def test_hist():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", rssi=-45, ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", rssi=-42, ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="cc:cc:cc:cc:cc:01", rssi=-88, ts=1.0, provenance="t"))
        h = rssi_histogram(db, bucket=10)
        assert h.get("-50") == 2 or h.get("-40") == 2
        assert sum(h.values()) == 3

def test_sql_histogram_handles_negative_rssi_boundaries_without_python_scan(monkeypatch):
    with SightingStore(":memory:") as db:
        db.insert_many([
            Sighting(kind="wifi", mac="000000000001", rssi=-101, provenance="test"),
            Sighting(kind="wifi", mac="000000000002", rssi=-100, provenance="test"),
            Sighting(kind="wifi", mac="000000000003", rssi=-90, provenance="test"),
            Sighting(kind="wifi", mac="000000000004", rssi=-81, provenance="test"),
            Sighting(kind="wifi", mac="000000000005", rssi=-80, provenance="test"),
            Sighting(kind="ble", mac="000000000006", rssi=-81, provenance="test"),
            Sighting(kind="wifi", mac="000000000007", rssi=None, provenance="test"),
        ])
        monkeypatch.setattr(db, "query", lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("histogram should not fetch all sightings")))
        assert rssi_histogram(db) == {"-110": 1, "-100": 1, "-90": 3, "-80": 1}
        assert rssi_histogram(db, kind="wifi") == {
            "-110": 1, "-100": 1, "-90": 2, "-80": 1,
        }
        assert rssi_histogram(db, bucket=20, kind="wifi") == {
            "-120": 1, "-100": 3, "-80": 1,
        }
