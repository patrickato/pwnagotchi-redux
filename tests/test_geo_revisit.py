"""Hardware-free tests for revisit analysis (Grok GG7)."""
from redux.geo import Sighting, SightingStore
from redux.geo.revisit import hour_histogram_from_sightings, revisit_for_mac


def test_revisit_span():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=1000.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=2000.0, rssi=-40, provenance="t"))
        info = revisit_for_mac(db, "aa:aa:aa:aa:aa:01")
        assert info is not None
        assert info.span_s == 1000.0
        assert "revisit" in info.reason


def test_hour_histogram():
    # 1970-01-01 00:00 UTC and +1h
    h = hour_histogram_from_sightings(
        [
            Sighting(kind="wifi", mac="a", ts=0.0, provenance="t"),
            Sighting(kind="wifi", mac="b", ts=3600.0, provenance="t"),
        ]
    )
    assert sum(h.values()) == 2
