"""Hardware-free tests for the online detector + opt-in upload-when-online."""
from __future__ import annotations

from redux.connectivity.online import check_online, OnlineStatus
from redux.geo import SightingStore, Sighting, WigleExportQueue
from redux.geo.wigle_upload import upload_when_online


# --- online detector --------------------------------------------------------- #

def test_online_true_when_probe_reaches():
    s = check_online(probe=lambda h, p, t: True)
    assert s.online is True and "online" in s.reason and s.via


def test_online_false_when_probe_fails():
    s = check_online(probe=lambda h, p, t: False)
    assert s.online is False and "offline" in s.reason


def test_online_fails_closed_on_probe_error():
    def boom(h, p, t):
        raise OSError("no route to host")
    s = check_online(probe=boom)
    assert s.online is False and s.via == "probe-error"


# --- upload-when-online ------------------------------------------------------- #

def _seed(store):
    store.insert(Sighting(kind="wifi", mac="aa:bb:cc:dd:ee:01", ssid="A",
                          lat=37.0, lon=-122.0, rssi=-50, ts=1.0, provenance="t"))
    store.insert(Sighting(kind="wifi", mac="aa:bb:cc:dd:ee:02", ssid="B",
                          lat=37.1, lon=-122.1, rssi=-60, ts=2.0, provenance="t"))


def test_upload_refused_when_disabled():
    with SightingStore(":memory:") as store, WigleExportQueue(":memory:") as q:
        _seed(store)
        sent = []
        r = upload_when_online(store, q, uploader=lambda b: sent.append(b) or True,
                               enabled=False, online_status=OnlineStatus(True, "online"))
        assert r.uploaded == 0 and r.attempted is False and "opt-in" in r.reason
        assert sent == []  # nothing transmitted


def test_upload_deferred_when_offline():
    with SightingStore(":memory:") as store, WigleExportQueue(":memory:") as q:
        _seed(store)
        sent = []
        r = upload_when_online(store, q, uploader=lambda b: sent.append(b) or True,
                               enabled=True, online_status=OnlineStatus(False, "offline: no link"))
        assert r.uploaded == 0 and r.attempted is False and "deferred" in r.reason
        assert "2 row" in r.reason and sent == []


def test_upload_happy_path_marks_exported_and_is_incremental():
    with SightingStore(":memory:") as store, WigleExportQueue(":memory:") as q:
        _seed(store)
        captured = {}
        def up(payload):
            captured["bytes"] = payload
            return True
        r = upload_when_online(store, q, uploader=up, enabled=True,
                               online_status=OnlineStatus(True, "online: reached x"))
        assert r.uploaded == 2 and r.attempted and r.online
        assert b"WigleWifi" in captured["bytes"] or b"aa:bb:cc:dd:ee:01" in captured["bytes"]
        # second run: all exported -> nothing to upload
        r2 = upload_when_online(store, q, uploader=up, enabled=True,
                                online_status=OnlineStatus(True, "online"))
        assert r2.uploaded == 0 and "nothing to upload" in r2.reason
        # a NEW sighting becomes the only pending row next time
        store.insert(Sighting(kind="wifi", mac="aa:bb:cc:dd:ee:03", ssid="C",
                              lat=37.2, lon=-122.2, rssi=-55, ts=3.0, provenance="t"))
        r3 = upload_when_online(store, q, uploader=up, enabled=True,
                                online_status=OnlineStatus(True, "online"))
        assert r3.uploaded == 1


def test_upload_failure_leaves_rows_queued_for_retry():
    with SightingStore(":memory:") as store, WigleExportQueue(":memory:") as q:
        _seed(store)
        def failing(payload):
            raise RuntimeError("503 service unavailable")
        r = upload_when_online(store, q, uploader=failing, enabled=True,
                               online_status=OnlineStatus(True, "online"))
        assert r.uploaded == 0 and r.attempted and "failed" in r.reason
        assert q.exported_count() == 0            # nothing marked -> will retry
        # a later successful run sends them
        r2 = upload_when_online(store, q, uploader=lambda b: True, enabled=True,
                                online_status=OnlineStatus(True, "online"))
        assert r2.uploaded == 2


def test_upload_rejected_by_service_does_not_mark():
    with SightingStore(":memory:") as store, WigleExportQueue(":memory:") as q:
        _seed(store)
        r = upload_when_online(store, q, uploader=lambda b: False, enabled=True,
                               online_status=OnlineStatus(True, "online"))
        assert r.uploaded == 0 and r.attempted and "rejected" in r.reason
        assert q.exported_count() == 0


def test_upload_uses_injected_online_check_when_no_status():
    with SightingStore(":memory:") as store, WigleExportQueue(":memory:") as q:
        _seed(store)
        calls = {"n": 0}
        def check():
            calls["n"] += 1
            return OnlineStatus(True, "online via injected check")
        r = upload_when_online(store, q, uploader=lambda b: True, enabled=True,
                               online_check=check)
        assert r.uploaded == 2 and calls["n"] == 1
