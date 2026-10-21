"""Hardware-free tests for SpatialDB sighting store (Grok geo #1)."""
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


def test_weaker_rssi_backfills_missing_location():
    # regression: the weaker-RSSI merge branch discarded a real GPS fix when the
    # stored row had NULL coords. It must backfill gaps without clobbering a
    # stronger sample's existing values.
    store = SightingStore(":memory:")
    store.insert(Sighting(kind="wifi", mac="aa:bb:cc:dd:ee:ff", rssi=-40, lat=None, lon=None, provenance="t"))
    store.insert(Sighting(kind="wifi", mac="aa:bb:cc:dd:ee:ff", rssi=-70, lat=51.5, lon=-0.1, provenance="t"))
    row = store.get("wifi", "aa:bb:cc:dd:ee:ff")
    assert row.lat == 51.5 and row.lon == -0.1
    store.insert(Sighting(kind="wifi", mac="aa:bb:cc:dd:ee:ff", rssi=-90, lat=10.0, lon=10.0, provenance="t"))
    row2 = store.get("wifi", "aa:bb:cc:dd:ee:ff")
    assert row2.lat == 51.5 and row2.lon == -0.1


# --- data lifecycle: bound the growth on a long-running field device -------- #

def test_prune_older_than_drops_aged_rows():
    st = SightingStore(":memory:")
    for i, t in enumerate((0.0, 100.0, 200.0)):
        st.insert(_s(mac=f"aa:bb:cc:00:00:0{i}", ts=t))
    # now=200, older_than=50 -> cutoff 150 -> ts 0 and 100 go, 200 stays
    assert st.prune(older_than=50, now=200.0) == 2
    assert st.count() == 1


def test_prune_max_rows_keeps_newest():
    st = SightingStore(":memory:")
    for i, t in enumerate((10.0, 20.0, 30.0, 40.0, 50.0)):
        st.insert(_s(mac=f"aa:bb:cc:00:00:1{i}", ts=t))
    assert st.prune(max_rows=2) == 3
    assert {s.ts for s in st.query()} == {40.0, 50.0}


def test_export_jsonl_and_csv(tmp_path):
    import json
    st = SightingStore(":memory:")
    st.insert(_s(mac="aa:bb:cc:00:00:aa", ssid="A", ts=1.0))
    st.insert(_s(mac="aa:bb:cc:00:00:bb", ssid="B", ts=2.0))
    jl = tmp_path / "out.jsonl"
    assert st.export(jl, fmt="jsonl") == 2
    lines = jl.read_text().strip().splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["mac"]
    cv = tmp_path / "out.csv"
    assert st.export(cv, fmt="csv") == 2
    assert "mac" in cv.read_text().splitlines()[0]


def test_export_rejects_unknown_format(tmp_path):
    st = SightingStore(":memory:")
    with pytest.raises(ValueError):
        st.export(tmp_path / "x", fmt="xml")


def test_stats_reports_count_kind_and_span():
    st = SightingStore(":memory:")
    st.insert(_s(mac="aa:bb:cc:00:00:c1", kind="wifi", ts=100.0))
    st.insert(_s(mac="aa:bb:cc:00:00:c2", kind="ble", ssid="", ts=300.0))
    s = st.stats()
    assert s["count"] == 2 and s["by_kind"]["wifi"] == 1 and s["by_kind"]["ble"] == 1
    assert s["oldest_ts"] == 100.0 and s["newest_ts"] == 300.0

def test_prune_rejects_unsafe_negative_or_invalid_limits():
    with SightingStore(":memory:") as db:
        db.insert(_s())
        for kwargs in ({"older_than": -1}, {"older_than": float("nan")},
                       {"older_than": float("inf")}, {"max_rows": -1},
                       {"max_rows": 3.5}):
            with pytest.raises(ValueError):
                db.prune(**kwargs)
        assert db.count() == 1


def test_prune_rolls_back_first_delete_if_second_delete_fails():
    import sqlite3
    with SightingStore(":memory:") as db:
        for i, ts in enumerate((10.0, 20.0, 30.0)):
            db.insert(_s(mac=f"de:ad:be:ef:00:0{i}", ts=ts))
        # First retention filter removes ts=10. The second would remove ts=20,
        # but a synthetic SQLite trigger aborts that deletion. All three rows
        # must remain, including the one the first filter would have removed.
        db._conn.execute("""
            CREATE TRIGGER deny_test_delete BEFORE DELETE ON sightings
            WHEN OLD.ts = 20
            BEGIN SELECT RAISE(ABORT, 'synthetic retention IO failure'); END
        """)
        db._conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            db.prune(older_than=15, max_rows=1, now=30)
        assert db.count() == 3
        db._conn.execute("DROP TRIGGER deny_test_delete")
        db._conn.commit()
        assert db.prune(older_than=15, max_rows=1, now=30) == 2
        assert [row.ts for row in db.query()] == [30.0]
