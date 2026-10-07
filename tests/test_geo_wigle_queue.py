"""Hardware-free tests for WiGLE incremental export queue (Grok geo #9)."""
from __future__ import annotations

import io

from redux.geo import Sighting, SightingStore, WigleExportQueue


def _s(mac: str, *, ts: float = 1000.0, rssi: int = -60) -> Sighting:
    return Sighting(
        kind="wifi",
        mac=mac,
        ssid="N",
        lat=1.0,
        lon=2.0,
        rssi=rssi,
        channel=6,
        ts=ts,
        provenance="queue test",
    )


def test_pending_starts_as_all_rows():
    with SightingStore(":memory:") as db, WigleExportQueue(":memory:") as q:
        db.insert(_s("aa:aa:aa:aa:aa:01"))
        db.insert(_s("aa:aa:aa:aa:aa:02"))
        assert q.pending_count(db) == 2


def test_package_marks_exported_and_second_is_empty():
    with SightingStore(":memory:") as db, WigleExportQueue(":memory:") as q:
        db.insert(_s("aa:aa:aa:aa:aa:01"))
        db.insert(_s("bb:bb:bb:bb:bb:01"))
        buf = io.StringIO()
        n = q.package(db, buf)
        assert n == 2
        assert "AA:AA:AA:AA:AA:01" in buf.getvalue()
        assert q.pending_count(db) == 0
        assert q.exported_count() == 2

        buf2 = io.StringIO()
        n2 = q.package(db, buf2)
        assert n2 == 0
        # headers still present
        assert "WigleWifi-1.6" in buf2.getvalue()


def test_new_sighting_only_in_next_package():
    with SightingStore(":memory:") as db, WigleExportQueue(":memory:") as q:
        db.insert(_s("aa:aa:aa:aa:aa:01"))
        q.package(db, io.StringIO())
        db.insert(_s("cc:cc:cc:cc:cc:01"))
        pending = q.pending(db)
        assert len(pending) == 1
        assert pending[0].mac == "cc:cc:cc:cc:cc:01"
        buf = io.StringIO()
        n = q.package(db, buf)
        assert n == 1
        assert "CC:CC:CC:CC:CC:01" in buf.getvalue()
        assert "AA:AA:AA:AA:AA:01" not in buf.getvalue().splitlines()[2:] if buf.getvalue() else True


def test_package_without_mark_leaves_pending():
    with SightingStore(":memory:") as db, WigleExportQueue(":memory:") as q:
        db.insert(_s("aa:aa:aa:aa:aa:01"))
        q.package(db, io.StringIO(), mark=False)
        assert q.pending_count(db) == 1
        assert q.exported_count() == 0


def test_reset_exported_requeues():
    with SightingStore(":memory:") as db, WigleExportQueue(":memory:") as q:
        db.insert(_s("aa:aa:aa:aa:aa:01"))
        q.package(db, io.StringIO())
        assert q.pending_count(db) == 0
        q.reset_exported()
        assert q.pending_count(db) == 1
