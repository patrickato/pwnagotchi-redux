from redux.geo import Sighting, SightingStore
from redux.geo.csv_export import export_store_csv, sightings_to_csv


def test_csv_header_and_row():
    s = Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ssid="X", lat=1.0, lon=2.0, ts=1.0, provenance="t")
    text = sightings_to_csv([s])
    assert "kind,mac,ssid" in text
    assert "aa:aa:aa:aa:aa:01" in text


def test_store_export():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=1.0, provenance="t"))
        assert "aa:aa:aa:aa:aa:01" in export_store_csv(db)
