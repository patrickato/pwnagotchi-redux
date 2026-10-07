from redux.core import Beastcore, Signal
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
    bc = Beastcore([ONBOARD, ALFA], intent=Intent.HUNT)
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
    bc = Beastcore([ONBOARD, ALFA], intent=Intent.HUNT, driver=FakeDriver(evs),
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
    bc = Beastcore([ONBOARD, ALFA], intent=Intent.HUNT, driver=FakeDriver(evs), detect_engine=engine)
    alerts = bc.pump()
    assert any("rogue" in a.kind.value for a in alerts)
    assert bc.bus.history(Signal.ALERT)                 # alert flowed on the bus
    assert any("rogue" in l.text.lower() for l in bc.narrator.lines())  # and was narrated


def test_no_gps_still_records_sighting_without_position():
    evs = _evs([{"tag": "wifi.ap.new", "time": 1.0, "data": {"mac": "de:ad:be:ef:00:01", "essid": "X"}}])
    bc = Beastcore([ONBOARD], intent=Intent.RECON, driver=FakeDriver(evs))  # no position_provider
    bc.pump()
    row = bc.store.query()[0]
    assert row.lat is None and row.lon is None          # honest: recorded, no invented fix
