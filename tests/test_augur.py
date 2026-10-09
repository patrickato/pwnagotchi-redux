import pytest
from redux.core import Augur, Signal
from redux.radio import Radio, Intent
from redux.detect import DetectEngine, RogueAPDetector, TrustedNetwork
from redux.engine import normalize_event

ONBOARD = Radio("wlan0", bands=frozenset({"2.4"}), monitor=True, inject=False, driver="brcmfmac", onboard=True)
ALFA = Radio("wlan1", bands=frozenset({"2.4", "5"}), monitor=True, inject=True, driver="mt76x2u", usb_gen=3, high_draw=True)


class FakeDriver:
    def __init__(self, events): self._e = events
    def set_interface(self, iface): pass
    def recon(self, on=True): pass
    def poll_events(self, clear=True): return list(self._e)


def _evs(specs):
    return [normalize_event(s) for s in specs]


def test_assembles_and_reports_status():
    bc = Augur([ONBOARD, ALFA], intent=Intent.HUNT)
    st = bc.status()
    assert st["intent"] == "hunt"
    assert st["capture_iface"] == "wlan1"
    assert "reason" in st["recommendation"]     # brain always explains
    assert st["creature"]                        # narrator voiced the boot reasons


def test_pump_geotags_events_into_store():
    evs = _evs([
        {"tag": "wifi.ap.new", "time": 1.0, "data": {"mac": "aa:bb:cc:dd:ee:01", "essid": "Home", "channel": 6, "rssi": -40}},
        {"tag": "wifi.ap.new", "time": 2.0, "data": {"mac": "aa:bb:cc:dd:ee:02", "essid": "Cafe", "channel": 11, "rssi": -70}},
        {"tag": "wifi.client.handshake", "time": 3.0, "data": {"ap": "aa:bb:cc:dd:ee:01"}},
    ])
    bc = Augur([ONBOARD, ALFA], intent=Intent.HUNT, driver=FakeDriver(evs),
                   position_provider=lambda: (40.1, -82.9))
    bc.pump()
    assert bc.store.count() == 2                 # two APs stored; handshake isn't a sighting
    rows = bc.store.query()
    assert all(r.lat == 40.1 and r.lon == -82.9 for r in rows)   # geo-tagged from the provider


def test_detects_rogue_ap_through_the_pipeline_and_narrates():
    engine = DetectEngine(rogue=RogueAPDetector([TrustedNetwork(ssid="Home", bssids=frozenset({"11:11:11:11:11:11"}))]))
    evs = _evs([
        {"tag": "wifi.ap.new", "time": 1.0, "data": {"mac": "11:11:11:11:11:11", "essid": "Home", "channel": 6}},
        {"tag": "wifi.ap.new", "time": 2.0, "data": {"mac": "99:99:99:99:99:99", "essid": "Home", "channel": 6}},
    ])
    bc = Augur([ONBOARD, ALFA], intent=Intent.HUNT, driver=FakeDriver(evs), detect_engine=engine)
    alerts = bc.pump()
    assert any("rogue" in a.kind.value for a in alerts)
    assert bc.bus.history(Signal.ALERT)                 # alert flowed on the bus
    assert any("rogue" in l.text.lower() for l in bc.narrator.lines())  # and was narrated


def test_no_gps_still_records_sighting_without_position():
    evs = _evs([{"tag": "wifi.ap.new", "time": 1.0, "data": {"mac": "de:ad:be:ef:00:01", "essid": "X"}}])
    bc = Augur([ONBOARD], intent=Intent.RECON, driver=FakeDriver(evs))  # no position_provider
    bc.pump()
    row = bc.store.query()[0]
    assert row.lat is None and row.lon is None          # honest: recorded, no invented fix


def test_ble_flood_detected_and_ble_devices_stored_through_pump():
    # BLE device events flow through the (newly wired) bridge into the BLE detectors
    # AND get geo-tagged as 'ble' sightings — one realistic flow, default 13 detectors.
    evs = _evs([
        {"tag": "ble.device.new", "time": 2000.0 + i * 0.01, "data": {"mac": f"aa:bb:cc:00:00:{i:02x}"}}
        for i in range(21)  # > ble_flood threshold (20) inside the 5s window
    ])
    bc = Augur([ONBOARD], intent=Intent.RECON, driver=FakeDriver(evs))
    alerts = bc.pump()
    assert any(a.kind.value == "ble_flood" for a in alerts)       # bridge -> BLE detector fired
    assert bc.bus.history(Signal.ALERT)                            # alert reached the bus
    assert bc.store.count(kind="ble") == 21                        # every BLE device persisted


def test_sightings_are_coalesced_into_one_batched_write_per_pump():
    # Prove the SD-write fix: per-event inserts are replaced by one batched
    # insert_many per pump cycle (no per-event commit/fsync).
    from redux.geo import SightingStore

    class CountingStore(SightingStore):
        def __init__(self):
            super().__init__(":memory:")
            self.insert_calls = 0
            self.insert_many_calls = 0
        def insert(self, s):
            self.insert_calls += 1
            return super().insert(s)
        def insert_many(self, items):
            self.insert_many_calls += 1
            return super().insert_many(items)

    store = CountingStore()
    evs = _evs([
        {"tag": "wifi.ap.new", "time": float(i), "data": {"mac": f"aa:bb:cc:00:00:{i:02x}", "essid": "X"}}
        for i in range(10)
    ])
    bc = Augur([ONBOARD], intent=Intent.RECON, driver=FakeDriver(evs), store=store)
    bc.pump()
    assert store.count() == 10               # all persisted
    assert store.insert_calls == 0           # NOT one insert per event
    assert store.insert_many_calls == 1      # one batched write for the cycle
    assert bc._sighting_buffer == []         # buffer flushed
    assert bc.flush_sightings() == 0         # idempotent when empty


def test_governor_decision_surfaces_in_status():
    from redux.core import Reading
    bc = Augur([ONBOARD], intent=Intent.RECON)
    # default (no readings) -> FULL, scale 1.0
    assert bc.status()["governor"]["mode"] == "full"
    assert bc.govern_scale() == 1.0
    # feed a hot reading -> SURVIVAL, cadence stretched
    d = bc.observe_resources(Reading(cpu_temp_c=82.0), now=0.0)
    assert d.mode.value == "survival"
    st = bc.status()
    assert st["governor"]["mode"] == "survival"
    assert st["governor"]["interval_scale"] == 2.5
    assert "cpu_temp" in st["governor"]["reason"]

def test_failed_augur_flush_keeps_uncommitted_sightings_for_retry():
    import sqlite3
    from redux.geo import SightingStore

    class FlakyStore(SightingStore):
        def __init__(self):
            super().__init__(":memory:")
            self.attempts = 0
        def insert_many(self, sightings):
            self.attempts += 1
            if self.attempts == 1:
                raise sqlite3.OperationalError("synthetic transient database lock")
            return super().insert_many(sightings)

    event = _evs([{"tag": "wifi.ap.new", "time": 7.0,
                   "data": {"mac": "12:34:56:78:90:ab", "essid": "Lab"}}])
    store = FlakyStore()
    augur = Augur([ONBOARD], driver=FakeDriver(event), store=store)
    with pytest.raises(sqlite3.OperationalError, match="transient"):
        augur.pump()
    assert store.count() == 0
    assert len(augur._sighting_buffer) == 1
    assert augur._sighting_flush_failures == 1
    assert "OperationalError" in augur._sighting_flush_error

    assert augur.flush_sightings() == 1
    assert augur.flush_sightings() == 0
    assert store.count() == 1
    assert augur._sighting_buffer == []
    assert augur._sighting_flush_error == ""
    assert augur.status()["sighting_queue"]["pending"] == 0
    assert augur.status()["sighting_queue"]["write_failures"] == 1


def test_failed_batch_does_not_commit_first_valid_sighting():
    from redux.geo import Sighting, SightingStore
    db = SightingStore(":memory:")
    good = Sighting("wifi", "aa:aa:aa:aa:aa:aa", provenance="test synthetic", ts=17.0)
    bad = Sighting("wifi", "bb:bb:bb:bb:bb:bb", provenance="", ts=18.0)
    with pytest.raises(ValueError, match="provenance"):
        db.insert_many([good, bad])
    assert db.count() == 0  # rollback first write too
    db.insert_many([good])
    assert db.count() == 1  # connection did not inherit a failed transaction
    db.close()

def test_dashboard_reuses_one_supplied_pipeline_snapshot(monkeypatch):
    from redux.crack import ingest
    from redux.web.status_page import status_payload

    def duplicate_database_read(*args, **kwargs):
        raise AssertionError("live pipeline SQLite must only be queried once")
    monkeypatch.setattr(ingest, "read_summary", duplicate_database_read)
    report = {"available": True, "artifacts": 1, "last_scan": {
        "completed_utc": 100, "scanned": 1, "outcomes": {"ready": 1},
    }}
    augur = Augur([ONBOARD])
    assert augur.status(processing_report=report)["capture_processing"] is report
    assert status_payload(augur, processing_report=report)["capture_processing"] is report


