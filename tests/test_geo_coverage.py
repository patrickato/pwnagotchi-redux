"""Hardware-free tests for coverage / gap grid (Grok geo #4)."""
from __future__ import annotations

import pytest

from redux.geo import CoverageGrid, Sighting, coverage_from_track


def test_mark_and_covered():
    g = CoverageGrid(lat_min=0.0, lat_max=0.01, lon_min=0.0, lon_max=0.01, cell_deg=0.005)
    assert g.mark(0.001, 0.001) is True
    assert g.mark(0.001, 0.001) is True  # second hit same cell
    covered = g.covered_cells()
    assert len(covered) == 1
    assert covered[0].hit_count == 2
    assert covered[0].covered is True


def test_outside_bbox_ignored():
    g = CoverageGrid(lat_min=0.0, lat_max=1.0, lon_min=0.0, lon_max=1.0, cell_deg=0.5)
    assert g.mark(2.0, 2.0) is False
    assert g.covered_cells() == []


def test_gaps_reported():
    g = CoverageGrid(lat_min=0.0, lat_max=0.02, lon_min=0.0, lon_max=0.02, cell_deg=0.01)
    g.mark(0.005, 0.005)  # one cell
    summary = g.summary()
    assert summary["covered_cells"] == 1
    assert summary["gap_cells"] == summary["total_cells"] - 1
    assert summary["total_cells"] >= 4
    gaps = g.gap_cells()
    assert all(not c.covered for c in gaps)
    assert "covered" in summary["reason"]


def test_coverage_from_track():
    track = [(37.0, -122.0), (37.0, -122.001), (37.001, -122.0)]
    g = coverage_from_track(track, cell_deg=0.001)
    assert len(g.covered_cells()) >= 1
    s = g.summary()
    assert s["covered_cells"] >= 1
    assert s["coverage_ratio"] > 0


def test_mark_samples_from_sightings():
    g = CoverageGrid(lat_min=10.0, lat_max=11.0, lon_min=20.0, lon_max=21.0, cell_deg=0.5)
    samples = [
        Sighting(
            kind="wifi",
            mac="aa:aa:aa:aa:aa:01",
            lat=10.1,
            lon=20.1,
            rssi=-50,
            ts=1.0,
            provenance="a",
        ),
        Sighting(
            kind="wifi",
            mac="aa:aa:aa:aa:aa:02",
            lat=None,
            lon=20.2,
            rssi=-50,
            ts=2.0,
            provenance="skip",
        ),
    ]
    n = g.mark_samples(samples)
    assert n == 1


def test_invalid_cell_deg():
    with pytest.raises(ValueError):
        CoverageGrid(0, 1, 0, 1, cell_deg=0)


def test_empty_track_raises():
    with pytest.raises(ValueError):
        coverage_from_track([])


def test_coverage_edge_point_does_not_overcount():
    # regression: a point exactly on the max edge floored one cell past the grid,
    # pushing covered>total (ratio>1, negative gaps).
    g = CoverageGrid(lat_min=0.0, lat_max=0.001, lon_min=0.0, lon_max=0.001, cell_deg=0.001)
    g.mark(0.0, 0.0)
    g.mark(0.001, 0.001)
    s = g.summary()
    assert s["covered_cells"] <= s["total_cells"]
    assert s["gap_cells"] >= 0
    assert s["coverage_ratio"] <= 1.0
    assert len(g.gap_cells()) == s["gap_cells"]
