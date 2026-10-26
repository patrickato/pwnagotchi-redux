"""Passive PMKID sweep — zero-transmit loot harvest + correlation."""
from redux.crack.passive_pmkid import (
    PassivePmkidSweep, PmkidHit, parse_22000_pmkid, load_state,
)
from redux.detect.frames import Frame, FrameType


def _pmkid(bssid, ts=1.0, ssid="", channel=None, security="wpa2-psk", rssi=-50):
    return Frame(type=FrameType.PMKID, ts=ts, bssid=bssid, ssid=ssid,
                 channel=channel, security=security, rssi=rssi)


def test_ignores_non_pmkid_frames():
    s = PassivePmkidSweep()
    assert s.feed(Frame(type=FrameType.BEACON, ts=1.0, bssid="aa:bb:cc:dd:ee:ff")) is None
    assert s.hits() == []


def test_harvests_pmkid_and_marks_new_loot_unscoped():
    s = PassivePmkidSweep()                      # no scope → LAB unscoped
    hit = s.feed(_pmkid("AA:BB:CC:00:11:22", ssid="linksys", channel=6))
    assert isinstance(hit, PmkidHit)
    assert hit.bssid == "aa:bb:cc:00:11:22" and hit.ssid == "linksys"
    assert hit.crackable and hit.in_scope and not hit.already_captured
    assert hit.new_loot is True
    assert "unscoped" in hit.scope_reason.lower()


def test_repeat_sightings_dedupe_and_count():
    s = PassivePmkidSweep()
    s.feed(_pmkid("aa:bb:cc:00:11:22", ts=1.0))
    s.feed(_pmkid("aa:bb:cc:00:11:22", ts=5.0))
    hits = s.hits()
    assert len(hits) == 1
    assert hits[0].count == 2 and hits[0].first_seen == 1.0 and hits[0].last_seen == 5.0


def test_already_captured_is_flagged_not_new_loot():
    s = PassivePmkidSweep(retired={"AA:BB:CC:00:11:22"})
    hit = s.feed(_pmkid("aa:bb:cc:00:11:22", ssid="home"))
    assert hit.already_captured is True
    assert hit.new_loot is False          # already owned — don't re-loot


def test_scope_gates_when_a_scope_is_supplied():
    class _Scope:
        def authorize(self, *, bssid=None, ssid=None):
            return (bssid == "aa:bb:cc:00:11:22", "armed" if bssid == "aa:bb:cc:00:11:22"
                    else "not in scope")
    s = PassivePmkidSweep(scope=_Scope())
    armed = s.feed(_pmkid("aa:bb:cc:00:11:22", ssid="lab"))
    other = s.feed(_pmkid("de:ad:be:ef:00:01", ssid="neighbor"))
    assert armed.in_scope and armed.new_loot
    assert not other.in_scope and not other.new_loot      # scoped out


def test_inventory_enriches_a_bare_pmkid_frame():
    # PMKID frame carries only the BSSID; the beacon inventory supplies the SSID.
    class _AP:
        bssid = "aa:bb:cc:00:11:22"
        ssid = "CorpWiFi"
        channel = 11
        security = "wpa2-psk"
        rssi = -40
    s = PassivePmkidSweep()
    s.learn_inventory([_AP()])
    hit = s.feed(_pmkid("aa:bb:cc:00:11:22", ssid="", channel=None, security=""))
    assert hit.ssid == "CorpWiFi" and hit.channel == 11 and hit.security == "wpa2-psk"


def test_summary_counts_and_zero_transmit_note():
    s = PassivePmkidSweep(retired={"11:11:11:11:11:11"})
    s.feed(_pmkid("aa:bb:cc:00:11:22", ssid="a"))
    s.feed(_pmkid("11:11:11:11:11:11", ssid="owned"))     # already captured
    summ = s.summary()
    assert summ["aps_leaking_pmkid"] == 2
    assert summ["new_loot"] == 1 and summ["already_captured"] == 1
    assert "zero" in summ["transmit"] and summ["footprint"] == "silent"


def test_ranking_puts_new_loot_first():
    s = PassivePmkidSweep(retired={"11:11:11:11:11:11"})
    s.feed(_pmkid("11:11:11:11:11:11", ssid="owned", rssi=-20))   # strong but owned
    s.feed(_pmkid("aa:bb:cc:00:11:22", ssid="fresh", rssi=-70))   # weak but new loot
    ranked = s.hits()
    assert ranked[0].bssid == "aa:bb:cc:00:11:22"     # new loot outranks owned


def test_parse_real_22000_pmkid_file():
    # a real hashcat-22000 PMKID line (type 01): WPA*01*pmkid*ap*sta*essid_hex*...
    essid = "MyNet".encode().hex()                     # 4d794e6574
    text = (
        f"WPA*01*2582a8281bf9d4308d6f5731d0e61c61*aabbcc001122*ddeeff445566*{essid}***\n"
        "WPA*02*deadbeef*112233445566*665544332211*4f74686572*anonce*eapol*02\n"   # EAPOL, not PMKID
        "garbage line\n"
    )
    frames = parse_22000_pmkid(text)
    assert len(frames) == 1                            # only the PMKID line
    assert frames[0].type is FrameType.PMKID
    assert frames[0].bssid == "aa:bb:cc:00:11:22" and frames[0].ssid == "MyNet"
    # and it flows straight through the sweep
    s = PassivePmkidSweep()
    s.feed_many(frames)
    assert s.new_loot()[0].ssid == "MyNet"


def test_state_roundtrip_for_ui_surfacing(tmp_path):
    s = PassivePmkidSweep()
    s.feed(_pmkid("aa:bb:cc:00:11:22", ssid="fresh"))
    p = str(tmp_path / "pmkid.json")
    assert s.save_state(p) is True
    state = load_state(p)
    assert state["summary"]["new_loot"] == 1
    assert state["summary"]["footprint"] == "silent"
    assert state["hits"][0]["ssid"] == "fresh"


def test_load_state_failsafe_on_missing_file(tmp_path):
    assert load_state(str(tmp_path / "nope.json")) == {}
