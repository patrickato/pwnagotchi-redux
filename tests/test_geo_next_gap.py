from redux.geo.next_gap import next_gap


def test_suggests_nearby_gap():
    covered = [(0.0, 0.0)]
    g = next_gap(covered, (0.0, 0.0), cell_deg=0.01, search_radius_cells=3)
    assert g is not None
    assert g.distance_m > 0
    assert "gap" in g.reason
