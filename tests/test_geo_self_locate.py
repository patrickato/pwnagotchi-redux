"""Hardware-free tests for offline self-geolocation (Grok geo #10)."""
from __future__ import annotations

import pytest

from redux.geo import Sighting, SightingStore, VisibleAP, self_locate, self_locate_from_scan


def _anchor(mac: str, lat: float, lon: float, rssi: int = -50) -> Sighting:
    return Sighting(
        kind="wifi",
        mac=mac,
        ssid="anchor",
        lat=lat,
        lon=lon,
        rssi=rssi,
        channel=6,
        ts=1000.0,
        provenance="self-locate fixture",
    )


def test_self_locate_centroid_near_strong_ap():
    with SightingStore(":memory:") as db:
        db.insert(_anchor("aa:aa:aa:aa:aa:01", 37.0, -122.0))
        db.insert(_anchor("bb:bb:bb:bb:bb:01", 37.0, -121.0))
        # Hear west AP strongly, east weakly → estimate closer to -122
        est = self_locate(
            db,
            [
                VisibleAP("aa:aa:aa:aa:aa:01", rssi=-40),
                VisibleAP("bb:bb:bb:bb:bb:01", rssi=-80),
            ],
        )
        assert est is not None
        assert est.observation_count == 2
        assert est.lon < -121.5
        assert "self-locate" in est.reason.lower()
        assert "known AP" in est.reason


def test_self_locate_requires_min_matches():
    with SightingStore(":memory:") as db:
        db.insert(_anchor("aa:aa:aa:aa:aa:01", 37.0, -122.0))
        est = self_locate(
            db,
            [VisibleAP("aa:aa:aa:aa:aa:01", rssi=-50)],
            min_matches=2,
        )
        assert est is None


def test_unknown_bssids_ignored():
    with SightingStore(":memory:") as db:
        db.insert(_anchor("aa:aa:aa:aa:aa:01", 37.0, -122.0))
        db.insert(_anchor("bb:bb:bb:bb:bb:01", 37.1, -122.1))
        est = self_locate_from_scan(
            db,
            [
                ("aa:aa:aa:aa:aa:01", -50),
                ("ff:ff:ff:ff:ff:ff", -30),  # not in store
                ("bb:bb:bb:bb:bb:01", -55),
            ],
        )
        assert est is not None
        assert est.observation_count == 2


def test_store_row_without_coords_skipped():
    with SightingStore(":memory:") as db:
        db.insert(
            Sighting(
                kind="wifi",
                mac="aa:aa:aa:aa:aa:01",
                lat=None,
                lon=None,
                rssi=-50,
                ts=1.0,
                provenance="no gps",
            )
        )
        db.insert(_anchor("bb:bb:bb:bb:bb:01", 40.0, -74.0))
        db.insert(_anchor("cc:cc:cc:cc:cc:01", 40.01, -74.01))
        est = self_locate_from_scan(
            db,
            [
                ("aa:aa:aa:aa:aa:01", -20),
                ("bb:bb:bb:bb:bb:01", -50),
                ("cc:cc:cc:cc:cc:01", -50),
            ],
        )
        assert est is not None
        assert est.observation_count == 2
        assert est.lat == pytest.approx(40.005, abs=0.01)
