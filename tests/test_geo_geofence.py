"""Hardware-free tests for geofence (Grok G2)."""
from redux.geo.geofence import inside_authorized_area, point_in_geojson_polygon

SQUARE = {
    "type": "Polygon",
    "coordinates": [
        [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]  # lon,lat
    ],
}


def test_inside_square():
    assert point_in_geojson_polygon(0.5, 0.5, SQUARE) is True


def test_outside_square():
    assert point_in_geojson_polygon(2.0, 2.0, SQUARE) is False


def test_glass_box_result():
    r = inside_authorized_area(0.5, 0.5, SQUARE)
    assert r["inside"] is True
    assert "inside" in r["reason"]
