"""Hardware-free tests for heatmap (Grok GG4)."""
from redux.geo.heatmap import heatmap_dict, heatmap_grid


def test_heatmap_counts():
    pts = [(37.0, -122.0), (37.0, -122.0), (37.01, -122.0)]
    cells = heatmap_grid(pts, cell_deg=0.005)
    assert sum(c.count for c in cells) == 3
    assert any(c.count == 2 for c in cells)


def test_heatmap_dict():
    d = heatmap_dict([(0.0, 0.0)], cell_deg=0.1)
    assert sum(d.values()) == 1
