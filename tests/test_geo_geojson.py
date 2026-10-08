"""Hardware-free tests for GeoJSON export (Grok GG5)."""
from redux.geo import Sighting, SightingStore
from redux.geo.geojson_export import export_store_geojson, to_feature_collection


def test_feature_collection():
    s = Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", lat=1.0, lon=2.0, ts=1.0, provenance="t")
    fc = to_feature_collection([s])
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 1
    assert fc["features"][0]["geometry"]["coordinates"] == [2.0, 1.0]


def test_skips_no_coords():
    s = Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=1.0, provenance="t")
    assert to_feature_collection([s])["features"] == []


def test_store_export():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", lat=3.0, lon=4.0, ts=1.0, provenance="t"))
        fc = export_store_geojson(db)
        assert fc["features"][0]["properties"]["mac"] == "aa:aa:aa:aa:aa:01"
