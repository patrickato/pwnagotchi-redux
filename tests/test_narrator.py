from redux.core import Narrator, Mood
from redux.core.event_bridge import event_to_frame, events_to_frames
from redux.engine import normalize_event
from redux.detect import FrameType, DetectEngine, RogueAPDetector, TrustedNetwork


# --- narrator ---------------------------------------------------------------- #

def test_narrator_voices_reasons_with_mood():
    n = Narrator()
    n.say_reason("pointing bettercap at wlan1 and starting recon")
    assert n.mood is Mood.HUNTING
    assert "wlan1" in n.latest().text
    assert n.latest().reason  # glass-box: underlying reason preserved


def test_narrator_warning_is_alert_mood():
    n = Narrator()
    line = n.say_reason("warning: your Alfa keeps dropping on USB 2.0")
    assert line.mood is Mood.ALERT
    assert "USB 2.0" in line.text


def test_narrator_dedups_consecutive_repeats():
    n = Narrator()
    assert n.say_reason("recon on wlan1") is not None
    assert n.say_reason("recon on wlan1") is None  # same line, not repeated
    assert len(n.lines()) == 1


def test_narrator_voices_alert():
    class A:
        reason = "known SSID 'Home' from an unexpected BSSID"
        severity = "critical"
        class kind:  # noqa
            value = "rogue_ap"
    line = Narrator().say_alert(A())
    assert line.mood is Mood.ALERT
    assert "rogue_ap" in line.text and "Home" in line.text


def test_tft_is_short_and_glyphed():
    n = Narrator()
    n.say_reason("pointing bettercap at wlan1 and starting recon")
    out = n.tft(width=20)
    assert out.startswith(">")      # hunting glyph
    assert len(out) <= 20


def test_voice_leadins_match_the_face_lexicon():
    n = Narrator()
    l1 = n.say_reason("PMKID elicited from HomeNet on ch6")
    assert l1.mood is Mood.HUNTING and "cached" in l1.text and "HomeNet" in l1.text
    l2 = n.say_reason("new AP seen: CoffeeShop (WPA2)")
    assert l2.mood is Mood.HUNTING and "new face" in l2.text and "CoffeeShop" in l2.text
    # the real machine reason is always carried verbatim (glass-box)
    assert l1.reason == "PMKID elicited from HomeNet on ch6"


# --- event bridge (honest coverage) ----------------------------------------- #

def test_ap_new_event_bridges_to_beacon_frame():
    ev = normalize_event({"tag": "wifi.ap.new", "time": 10.0,
                          "data": {"mac": "aa:bb:cc:dd:ee:ff", "essid": "Home",
                                   "channel": 6, "encryption": "WPA2"}})
    f = event_to_frame(ev)
    assert f is not None
    assert f.type is FrameType.BEACON
    assert f.bssid == "aa:bb:cc:dd:ee:ff" and f.ssid == "Home" and f.channel == 6


def test_non_ap_events_have_no_frame():
    for tag in ("wifi.client.handshake", "ble.device.new", "wifi.client.new"):
        ev = normalize_event({"tag": tag, "data": {}})
        assert event_to_frame(ev) is None  # honest: no raw-frame equivalent


# --- full pipeline through the supervisor ------------------------------------ #

class FakeDriver:
    def __init__(self, events): self._e = events
    def set_interface(self, iface): pass
    def recon(self, on=True): pass
    def poll_events(self, clear=True): return list(self._e)


def test_supervisor_pump_detects_and_narrates_rogue_ap():
    from redux.radio import Radio, Intent
    from redux.core import Supervisor
    # a rogue-AP detector that trusts 'Home' only on its real BSSID
    engine = DetectEngine(rogue=RogueAPDetector([TrustedNetwork(ssid="Home", bssids=frozenset({"11:11:11:11:11:11"}))]))
    narrator = Narrator()
    # two beacons for SSID 'Home': the trusted one, then an impostor BSSID
    evs = [
        {"tag": "wifi.ap.new", "time": 1.0, "data": {"mac": "11:11:11:11:11:11", "essid": "Home", "channel": 6}},
        {"tag": "wifi.ap.new", "time": 2.0, "data": {"mac": "99:99:99:99:99:99", "essid": "Home", "channel": 6}},
    ]
    from redux.engine import normalize_event as ne
    drv = FakeDriver([ne(e) for e in evs])
    onboard = Radio("wlan0", bands=frozenset({"2.4"}), monitor=True, inject=False, driver="brcmfmac", onboard=True)
    sup = Supervisor([onboard], intent=Intent.RECON, driver=drv, detect_engine=engine, narrator=narrator)
    alerts = sup.pump()
    assert any("rogue" in a.kind.value for a in alerts)         # impostor caught
    assert any("rogue" in l.text.lower() for l in sup.creature_lines())  # and narrated


def test_supervisor_without_narrator_or_engine_still_works():
    from redux.radio import Radio, Intent
    from redux.core import Supervisor
    sup = Supervisor([], intent=Intent.RECON)   # no driver/engine/narrator
    assert sup.pump() == []
    assert sup.creature_lines() == []
    assert sup.creature_tft() == "…"
