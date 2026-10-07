"""Hardware-free tests for geohash (Grok G5)."""
from redux.geo.geohash import encode, group_by_cell


def test_encode_stable():
    h1 = encode(37.77, -122.42, 7)
    h2 = encode(37.77, -122.42, 7)
    assert h1 == h2
    assert len(h1) == 7


def test_nearby_share_prefix():
    a = encode(37.770, -122.420, 6)
    b = encode(37.771, -122.421, 6)
    assert a[:4] == b[:4]


def test_group_by_cell():
    g = group_by_cell([(0.0, 0.0), (0.0, 0.0), (10.0, 10.0)], precision=5)
    assert sum(g.values()) == 3
