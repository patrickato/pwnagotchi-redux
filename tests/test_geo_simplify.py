"""Hardware-free tests for Douglas-Peucker (Grok GG2)."""
from redux.geo.simplify import douglas_peucker


def test_short_track_unchanged():
    pts = [(0.0, 0.0), (1.0, 1.0)]
    assert douglas_peucker(pts, 0.1) == pts


def test_collinear_collapses():
    pts = [(0.0, 0.0), (0.5, 0.0), (1.0, 0.0)]
    out = douglas_peucker(pts, 0.01)
    assert len(out) == 2
    assert out[0] == pts[0] and out[-1] == pts[-1]


def test_keeps_corner():
    pts = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
    out = douglas_peucker(pts, 0.01)
    assert len(out) == 3
