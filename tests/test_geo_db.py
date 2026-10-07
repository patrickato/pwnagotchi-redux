"""Hardware-free tests for BeastSpatialDB sighting store (Grok geo #1)."""
from __future__ import annotations

import pytest

from redux.geo import Sighting, SightingStore


def _s(
    *,
    kind: str = "wifi",
    mac: str = "aa:bb:cc:dd:ee:01",
    ssid: str = "TestNet",
    lat: float | None = 37.77,
    lon: float | None = -122.42,
    rssi: int | None = -60,
    channel: int | None = 6,
    source_radio: str = "wlan0",
    ts: float = 1000.0,
    provenance: str = "unit-test synthetic sighting",
) -> Sighting:
    return Sighting(
        kind=kind,
        mac=mac,
        ssid=ssid,
        lat=lat,
        lon=lon,
        rssi=rssi,
        channel=channel,
        source_radio=source_radio,
        ts=ts,
        provenance=provenance,
    )


def test_insert_and_get_round_trip():
    with SightingStore(":memory:") as db:
        stored = db.insert(_s())
        assert stored.mac == "aa:bb:cc:dd:ee:01"
        assert stored.kind == "wifi"
        assert stored.rssi == -60
        assert stored.provenance == "unit-test synthetic sighting"
        assert stored.first_seen == 1000.0
        got = db.get("wifi", "AA:BB:CC:DD:EE:01")  # case-normalized
        assert got is not None
        assert got.ssid == "TestNet"
        assert got.lat == pytest.approx(37.77)


def test_provenance_required():
    with SightingStore(":memory:") as db:
        with pytest.raises(ValueError, match="provenance"):
            db.insert(_s(provenance=""))


def test_mac_required():
    with SightingStore(":memory:") as db:
        with pytest.raises(ValueError, match="mac"):
            db.insert(_s(mac=""))


def test_dedup_keeps_best_rssi():
    with SightingStore(":memory:") as db:
        db.insert(_s(rssi=-70, ts=1000.0, provenance="first weak"))
        db.insert(_s(rssi=-50, ts=1001.0, lat=38.0, provenance="stronger sample"))
        row = db.get("wifi", "aa:bb:cc:dd:ee:01")
        assert row is not None
        assert row.rssi == -50
        assert row.lat == pytest.approx(38.0)
        assert row.provenance == "stronger sample"
        assert row.first_seen == 1000.0  # earliest preserved


def test_dedup_ignores_weaker_rssi_but_keeps_first_seen():
    with SightingStore(":memory:") as db:
        db.insert(_s(rssi=-40, ts=2000.0, provenance="strong first"))
        db.insert(_s(rssi=-80, ts=1990.0, provenance="weaker earlier"))
        row = db.get("wifi", "aa:bb:cc:dd:ee:01")
        assert row is not None
        assert row.rssi == -40
        assert row.provenance == "strong first"
        assert row.first_seen == 1990.0  # earlier ts recorded


def test_query_by_kind_and_time():
    with SightingStore(":memory:") as db:
        db.insert(_s(mac="11:11:11:11:11:11", kind="wifi", ts=10.0))
        db.insert(_s(mac="22:22:22:22:22:22", kind="ble", ts=20.0, ssid=""))
        db.insert(_s(mac="33:33:33:33:33:33", kind="wifi", ts=30.0))
        wifi = db.query(kind="wifi")
        assert len(wifi) == 2
        assert all(s.kind == "wifi" for s in wifi)
        recent = db.query(since=15.0)
        assert len(recent) == 2
        limited = db.query(kind="wifi", limit=1)
        assert len(limited) == 1


def test_count():
    with SightingStore(":memory:") as db:
        db.insert(_s(mac="01:01:01:01:01:01", kind="wifi"))
        db.insert(_s(mac="02:02:02:02:02:02", kind="ble", ssid=""))
        assert db.count() == 2
        assert db.count("wifi") == 1
        assert db.count("ble") == 1


def test_kinds_are_independent_keys():
    with SightingStore(":memory:") as db:
        db.insert(_s(kind="wifi", mac="aa:aa:aa:aa:aa:aa", provenance="wifi row"))
        db.insert(_s(kind="ble", mac="aa:aa:aa:aa:aa:aa", provenance="ble row", ssid=""))
        assert db.count() == 2
        assert db.get("wifi", "aa:aa:aa:aa:aa:aa").provenance == "wifi row"
        assert db.get("ble", "aa:aa:aa:aa:aa:aa").provenance == "ble row"
