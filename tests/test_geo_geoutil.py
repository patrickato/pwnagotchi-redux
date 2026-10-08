"""Hardware-free tests for geoutil (Grok GG1)."""
from redux.geo.geoutil import bearing_deg, destination, haversine_m


def test_haversine_zero():
    assert haversine_m(37.0, -122.0, 37.0, -122.0) == 0.0


def test_haversine_known_order():
    # ~111 km per degree lat
    d = haversine_m(0.0, 0.0, 1.0, 0.0)
    assert 110_000 < d < 112_000


def test_bearing_north():
    b = bearing_deg(0.0, 0.0, 1.0, 0.0)
    assert abs(b - 0.0) < 1.0


def test_destination_roundtrip():
    lat2, lon2 = destination(37.0, -122.0, 1000.0, 90.0)
    d = haversine_m(37.0, -122.0, lat2, lon2)
    assert abs(d - 1000.0) < 2.0
