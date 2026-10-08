from redux.geo.path_loss import rssi_to_distance_m


def test_stronger_rssi_closer():
    near = rssi_to_distance_m(-40)
    far = rssi_to_distance_m(-80)
    assert near.distance_m < far.distance_m
    assert "path-loss" in near.reason


def test_positive_distance():
    e = rssi_to_distance_m(-55)
    assert e.distance_m > 0
