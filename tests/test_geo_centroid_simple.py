from redux.geo.centroid_simple import mean_centroid


def test_mean():
    c = mean_centroid([(0.0, 0.0), (2.0, 4.0)])
    assert c is not None
    assert c.lat == 1.0 and c.lon == 2.0
    assert mean_centroid([]) is None
