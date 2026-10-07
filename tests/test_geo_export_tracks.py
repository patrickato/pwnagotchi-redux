"""Hardware-free tests for GPX/KML export (Grok G3)."""
from redux.geo import Sighting, SightingStore
from redux.geo.export_tracks import export_store_gpx, export_store_kml, to_gpx, to_kml


def test_gpx_contains_wpt():
    s = Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", lat=1.0, lon=2.0, ts=1.0, provenance="t")
    xml = to_gpx([s])
    assert "<gpx" in xml and 'lat="1.0"' in xml


def test_kml_contains_placemark():
    s = Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", lat=1.0, lon=2.0, ts=1.0, provenance="t")
    xml = to_kml([s])
    assert "<kml" in xml and "2.0,1.0,0" in xml


def test_store_export():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", lat=3.0, lon=4.0, ts=1.0, provenance="t"))
        assert "aa:aa:aa:aa:aa:01" in export_store_gpx(db)
        assert "4.0,3.0,0" in export_store_kml(db)
