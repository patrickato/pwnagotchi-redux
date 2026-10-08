from redux.geo.track_length import track_length_m


def test_empty_and_single():
    assert track_length_m([]) == 0.0
    assert track_length_m([(0.0, 0.0)]) == 0.0


def test_one_degree_lat():
    d = track_length_m([(0.0, 0.0), (1.0, 0.0)])
    assert 110_000 < d < 112_000
