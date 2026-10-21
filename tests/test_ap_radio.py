"""Rogue-AP / EAP gets its own radio so it runs alongside capture, not on top of it."""
from redux.radio import Radio, Intent, Assignment, decide, assign_ap_radio

ONBOARD = Radio("wlan0", bands=frozenset({"2.4"}), monitor=True, inject=False,
                driver="brcmfmac", onboard=True)
ALFA = Radio("wlan1", bands=frozenset({"2.4", "5"}), monitor=True, inject=True,
             driver="mt76x2u", usb_gen=3)


def test_ap_takes_an_idle_spare_without_touching_capture():
    a = decide([ONBOARD, ALFA], Intent.ONLINE)   # onboard uplink, alfa idle
    ap = assign_ap_radio(a)
    assert ap["iface"] == "wlan1" and ap["collides"] is False


def test_ap_collides_when_every_radio_is_capturing():
    a = decide([ONBOARD, ALFA], Intent.HUNT)      # alfa CAPTURE + onboard SCAN (tandem)
    ap = assign_ap_radio(a)
    assert ap["collides"] is True
    assert ap["iface"] == "wlan1"                  # would share the capture radio
    assert "add a radio" in ap["reason"]


def test_ap_rides_the_uplink_when_capture_is_kept_online():
    a = decide([ONBOARD, ALFA], Intent.HUNT, keep_uplink=True)  # onboard UPLINK
    ap = assign_ap_radio(a)
    assert ap["iface"] == "wlan0" and ap["collides"] is False
    assert "internet drops" in ap["reason"]


def test_no_radio_available_for_ap():
    ap = assign_ap_radio(Assignment(roles={}))
    assert ap["iface"] is None


def test_eap_plan_assigns_its_own_ap_radio_and_flags_collision():
    from redux.core.augur import Augur
    aug = Augur(radios=[ONBOARD, ALFA], intent=Intent.HUNT)
    out = aug.eap_plan("CorpWiFi", authorized=False)
    assert "ap_iface" in out and "ap_reason" in out
    # both radios are capturing in HUNT -> rogue-AP collision is surfaced
    assert out.get("ap_collision")


def test_eap_plan_explicit_iface_is_respected():
    from redux.core.augur import Augur
    aug = Augur(radios=[ONBOARD, ALFA], intent=Intent.HUNT)
    out = aug.eap_plan("CorpWiFi", authorized=False, iface="wlan3")
    assert out["ap_iface"] == "wlan3" and "ap_collision" not in out
