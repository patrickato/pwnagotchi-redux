"""Hardware-free tests for RSSI weighted-centroid estimate (Grok geo #3)."""
from __future__ import annotations

import pytest

from redux.geo import LocationEstimate, Sighting, estimate_from_coords, estimate_location


def test_centroid_pulls_toward_stronger_rssi():
    # Two points east-west; stronger sample on the east side
    est = estimate_from_coords(
        [
            (37.0, -122.0, -80),  # west, weak
            (37.0, -121.0, -40),  # east, strong
        ]
    )
    assert est is not None
    assert est.observation_count == 2
    # Centroid lon should be closer to -121 than midpoint -121.5
    assert est.lon > -121.5
    assert est.lat == pytest.approx(37.0, abs=1e-6)
    assert est.error_radius_m >= 5.0
    assert "weighted" in est.reason.lower()


def test_identical_points_small_radius():
    est = estimate_from_coords(
        [
            (40.0, -74.0, -50),
            (40.0, -74.0, -55),
            (40.0, -74.0, -60),
        ]
    )
    assert est is not None
    assert est.lat == pytest.approx(40.0)
    assert est.lon == pytest.approx(-74.0)
    assert est.observation_count == 3
    assert est.error_radius_m == pytest.approx(5.0)  # floor


def test_too_few_observations_returns_none():
    assert estimate_from_coords([(37.0, -122.0, -50)], min_observations=2) is None
    assert estimate_from_coords([], min_observations=1) is None


def test_skips_incomplete_samples():
    samples = [
        Sighting(
            kind="wifi",
            mac="aa:aa:aa:aa:aa:01",
            lat=37.0,
            lon=-122.0,
            rssi=-50,
            ts=1.0,
            provenance="complete",
        ),
        Sighting(
            kind="wifi",
            mac="aa:aa:aa:aa:aa:01",
            lat=None,
            lon=-122.1,
            rssi=-40,
            ts=2.0,
            provenance="missing lat",
        ),
        Sighting(
            kind="wifi",
            mac="aa:aa:aa:aa:aa:01",
            lat=37.1,
            lon=-122.1,
            rssi=-45,
            ts=3.0,
            provenance="complete 2",
        ),
    ]
    est = estimate_location(samples)
    assert est is not None
    assert est.observation_count == 2


def test_single_observation_when_allowed():
    est = estimate_from_coords([(51.5, -0.12, -60)], min_observations=1)
    assert est is not None
    assert est.observation_count == 1
    assert est.error_radius_m == pytest.approx(50.0)
    assert isinstance(est, LocationEstimate)
