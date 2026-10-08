from redux.geo import Sighting
from redux.geo.clamp_coords import clamp_lat, clamp_lon, clamp_sighting


def test_clamp():
    assert clamp_lat(100.0) == 90.0
    assert clamp_lat(-100.0) == -90.0
    assert abs(clamp_lon(190.0) - (-170.0)) < 1e-9
    s = Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", lat=95.0, lon=200.0, ts=1.0, provenance="t")
    c = clamp_sighting(s)
    assert c.lat == 90.0
