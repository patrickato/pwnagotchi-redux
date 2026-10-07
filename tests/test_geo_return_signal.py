"""Hardware-free tests for return-to-signal (Grok G1)."""
from redux.geo import Sighting, SightingStore
from redux.geo.return_signal import return_to_signal


def test_return_to_signal():
    with SightingStore(":memory:") as db:
        db.insert(
            Sighting(
                kind="wifi",
                mac="aa:aa:aa:aa:aa:01",
                lat=37.0,
                lon=-122.0,
                rssi=-40,
                ts=1.0,
                provenance="fixture",
            )
        )
        r = return_to_signal(db, "aa:aa:aa:aa:aa:01", 37.0, -122.01)
        assert r is not None
        assert r.distance_m > 0
        assert 0 <= r.bearing_deg < 360
        assert "return-to-signal" in r.reason


def test_missing_returns_none():
    with SightingStore(":memory:") as db:
        assert return_to_signal(db, "ff:ff:ff:ff:ff:ff", 0.0, 0.0) is None
