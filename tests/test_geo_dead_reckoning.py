"""Hardware-free tests for dead-reckoning (Grok G4)."""
from redux.geo.dead_reckoning import Fix, fill_gaps


def test_fills_short_gap():
    fixes = [
        Fix(ts=0.0, lat=0.0, lon=0.0, speed_mps=10.0, heading_deg=90.0),
        Fix(ts=5.0, lat=0.0, lon=0.001, speed_mps=10.0, heading_deg=90.0),
    ]
    out = fill_gaps(fixes, max_gap_s=30.0, step_s=1.0)
    assert len(out) > 2
    assert any(not f.valid for f in out)


def test_skips_long_gap():
    fixes = [
        Fix(ts=0.0, lat=0.0, lon=0.0, speed_mps=10.0, heading_deg=0.0),
        Fix(ts=100.0, lat=1.0, lon=0.0, speed_mps=10.0, heading_deg=0.0),
    ]
    out = fill_gaps(fixes, max_gap_s=30.0, step_s=1.0)
    assert len(out) == 2
