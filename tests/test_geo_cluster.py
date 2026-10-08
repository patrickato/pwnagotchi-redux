"""Hardware-free tests for DBSCAN-lite clustering (Grok GG3)."""
from redux.geo.cluster import dbscan_lite


def test_two_hotspots():
    # Two tight groups ~1km apart
    a = [(37.000, -122.000), (37.0001, -122.0001), (37.0002, -122.0000)]
    b = [(37.010, -122.000), (37.0101, -122.0001), (37.0102, -122.0000)]
    clusters = dbscan_lite(a + b, eps_m=80.0, min_samples=3)
    assert len(clusters) == 2
    assert all(c.size >= 3 for c in clusters)


def test_noise_dropped():
    pts = [(0.0, 0.0), (10.0, 10.0)]  # far apart, below min_samples
    assert dbscan_lite(pts, eps_m=50.0, min_samples=3) == []


def test_empty():
    assert dbscan_lite([]) == []
